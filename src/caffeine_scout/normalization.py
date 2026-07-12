"""Shared product parsing, filtering, normalization, and deduplication."""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from rapidfuzz import fuzz, process

from caffeine_scout.config import AppConfig, BrandConfig
from caffeine_scout.models import Offer, RawOffer

PACK_PATTERNS = (
    re.compile(r"\bpack\s+of\s+(\d{1,3})\b", re.I),
    re.compile(r"\b(\d{1,3})\s*[- ]?pack\b", re.I),
    re.compile(r"\b(\d{1,3})\s*(?:count|ct)\b", re.I),
    re.compile(r"\b(\d{1,3})\s+cans?\b", re.I),
    re.compile(r"\bcase\s+of\s+(\d{1,3})\b", re.I),
)
CAN_SIZE_PATTERN = re.compile(r"\b(\d{1,2}(?:\.\d+)?)\s*(?:fl\.?\s*)?oz\b", re.I)
CAFFEINE_PATTERN = re.compile(r"\b(\d{2,3})\s*mg\b", re.I)


class NormalizationError(ValueError):
    """Raw source data cannot safely become an Offer."""


def extract_pack_count(name: str) -> int | None:
    lowered = name.casefold()
    if re.search(r"\bpackets?\b", lowered):
        return None
    if re.search(r"\bsingle\s+can\b|\b1\s*(?:count|ct|pack)\b", lowered):
        return 1
    for pattern in PACK_PATTERNS:
        match = pattern.search(name)
        if match:
            count = int(match.group(1))
            return count if count > 0 else None
    return 1 if re.search(r"\bcan\b", lowered) else None


def extract_can_size_oz(name: str) -> float | None:
    match = CAN_SIZE_PATTERN.search(name)
    return float(match.group(1)) if match else None


def extract_total_quantity_oz(name: str) -> float | None:
    pack_count = extract_pack_count(name)
    can_size = extract_can_size_oz(name)
    return pack_count * can_size if pack_count is not None and can_size is not None else None


def _brand_choices(brands: Iterable[BrandConfig]) -> dict[str, BrandConfig]:
    return {alias.casefold(): brand for brand in brands for alias in [brand.name, *brand.aliases]}


def canonicalize_brand(name: str, brands: list[BrandConfig]) -> BrandConfig | None:
    choices = _brand_choices(brands)
    lowered = name.casefold()
    direct = sorted((alias for alias in choices if alias in lowered), key=len, reverse=True)
    if direct:
        return choices[direct[0]]
    match = process.extractOne(lowered, choices.keys(), scorer=fuzz.partial_ratio, score_cutoff=88)
    return choices[match[0]] if match else None


def extract_product_line(name: str) -> str | None:
    for phrase in ("Zero Sugar", "Performance", "Smart Energy"):
        if phrase.casefold() in name.casefold():
            return phrase
    return None


def extract_flavor(name: str, brand: BrandConfig) -> str | None:
    if "variety pack" in name.casefold():
        return "Variety Pack"
    comma_parts = [part.strip() for part in name.split(",")]
    if len(comma_parts) >= 2:
        candidate = comma_parts[1]
        if not re.search(r"\b(?:\d+\s*(?:fl\s*)?oz|pack|count|ct|cans?)\b", candidate, re.I):
            return candidate
    cleaned = name
    for alias in sorted([brand.name, *brand.aliases], key=len, reverse=True):
        cleaned = re.sub(re.escape(alias), " ", cleaned, flags=re.I)
    for pattern in (*PACK_PATTERNS, CAN_SIZE_PATTERN, CAFFEINE_PATTERN):
        cleaned = pattern.sub(" ", cleaned)
    cleaned = re.sub(
        r"\b(?:energy|drink|drinks|zero sugar|performance|smart energy|cans?|case)\b",
        " ",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"[^A-Za-z0-9 -]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
    return cleaned.title() if cleaned and not cleaned.isdigit() else None


def is_relevant_product(name: str, config: AppConfig) -> tuple[bool, str | None]:
    lowered = name.casefold()
    for term in config.product_filters.excluded_terms:
        if term.casefold() in lowered:
            return False, f"excluded term: {term}"
    if re.search(r"\bpackets?\b", lowered):
        return False, "packet product"
    required = config.product_filters.required_terms
    if required and not any(term.casefold() in lowered for term in required):
        return False, "missing required product term"
    return True, None


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def normalize_offer(raw: RawOffer, config: AppConfig) -> Offer:
    relevant, reason = is_relevant_product(raw.product_name, config)
    if not relevant:
        raise NormalizationError(reason or "irrelevant product")
    brand = canonicalize_brand(raw.canonical_brand or raw.product_name, config.brands)
    if brand is None and raw.canonical_brand:
        brand = canonicalize_brand(raw.product_name, config.brands)
    if brand is None:
        raise NormalizationError("unrecognized brand")
    pack_count = raw.pack_count or extract_pack_count(raw.product_name)
    if pack_count is None or pack_count <= 0:
        raise NormalizationError("pack count could not be determined")
    if raw.listed_price <= 0:
        raise NormalizationError("listed price must be positive")
    if raw.coupon_value < 0 or raw.shipping_cost < 0:
        raise NormalizationError("coupon and shipping values cannot be negative")
    effective = _money(raw.listed_price + raw.shipping_cost - raw.coupon_value)
    if effective <= 0:
        raise NormalizationError("effective price must be positive")
    price_per_can = (effective / pack_count).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    caffeine = raw.caffeine_mg_per_can
    if caffeine is None:
        caffeine_match = CAFFEINE_PATTERN.search(raw.product_name)
        caffeine = int(caffeine_match.group(1)) if caffeine_match else brand.default_caffeine_mg
    caffeine_value = float(Decimal(caffeine * pack_count) / effective) if caffeine else None
    confidence = raw.data_confidence
    notes = list(raw.notes)
    if raw.pack_count is None:
        confidence = min(confidence, 0.75)
        notes.append("Pack count parsed from product name")
    return Offer(
        source=raw.source,
        retailer=raw.retailer,
        source_product_id=raw.source_product_id,
        product_name=raw.product_name,
        canonical_brand=brand.name,
        product_line=raw.product_line or extract_product_line(raw.product_name),
        flavor=raw.flavor or extract_flavor(raw.product_name, brand),
        pack_count=pack_count,
        can_size_oz=raw.can_size_oz or extract_can_size_oz(raw.product_name),
        caffeine_mg_per_can=caffeine,
        listed_price=_money(raw.listed_price),
        coupon_value=_money(raw.coupon_value),
        shipping_cost=_money(raw.shipping_cost),
        effective_price=effective,
        price_per_can=price_per_can,
        caffeine_mg_per_dollar=caffeine_value,
        advertised_discount_percent=raw.advertised_discount_percent,
        fulfillment_type=raw.fulfillment_type,
        store_name=raw.store_name,
        store_address=raw.store_address,
        distance_miles=raw.distance_miles,
        in_stock=raw.in_stock,
        membership_required=raw.membership_required,
        subscription_required=raw.subscription_required,
        url=raw.url,
        collected_at=raw.collected_at,
        data_confidence=confidence,
        notes=notes,
    )


def _slug(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").casefold()).strip("-")


def offer_fingerprint(offer: Offer) -> str:
    size = f"{offer.can_size_oz:g}" if offer.can_size_oz is not None else "unknown"
    return "|".join(
        (
            _slug(offer.canonical_brand),
            _slug(offer.product_line),
            _slug(offer.flavor),
            str(offer.pack_count),
            size,
        )
    )


def _completeness(offer: Offer) -> tuple[int, object]:
    optional = (
        offer.source_product_id,
        offer.product_line,
        offer.flavor,
        offer.can_size_oz,
        offer.caffeine_mg_per_can,
        offer.store_name,
        offer.store_address,
        offer.distance_miles,
        offer.in_stock,
    )
    return sum(value is not None for value in optional) + len(offer.notes), offer.collected_at


def deduplicate_offers(offers: Iterable[Offer]) -> list[Offer]:
    selected: dict[tuple[str, str, str, str], Offer] = {}
    for offer in offers:
        key = (
            _slug(offer.retailer),
            offer_fingerprint(offer),
            offer.fulfillment_type,
            _slug(offer.store_name),
        )
        current = selected.get(key)
        if current is None or _completeness(offer) > _completeness(current):
            selected[key] = offer
    return list(selected.values())
