"""Shared, conservative helpers for public retailer catalog discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from caffeine_scout.config import CrawlerConfig, DiscoverySourceConfig
from caffeine_scout.crawler import EthicalPageCrawler
from caffeine_scout.models import (
    CrawlResult,
    RawOffer,
    RetailerSource,
    SearchRequest,
    SourceDiagnostic,
    SourceStatus,
)
from caffeine_scout.sources.jsonld import (
    extract_embedded_product_data,
    extract_jsonld_offers,
    extract_public_product_json,
)

MONEY_PATTERN = re.compile(r"\$\s*(\d{1,5}(?:,\d{3})*(?:\.\d{2})?)")
MULTIBUY_PATTERN = re.compile(
    r"\bbuy\s+(\d{1,2})\s+(?:for|/|at)\s*\$\s*(\d+(?:\.\d{1,2})?)\b", re.I
)
EXCLUDED_TERMS = (
    "powder",
    "pre-workout",
    "pre workout",
    "packet",
    "stick pack",
    "drink mix",
    "supplement",
    "empty can",
    "collector can",
)
TRACKING_KEYS = {
    "afid",
    "clkid",
    "gclid",
    "ref",
    "ref_",
    "utm_campaign",
    "utm_medium",
    "utm_source",
}


class RetailerDiscoveryUnavailable(RuntimeError):
    """A permitted discovery attempt did not yield public normalized candidates."""


@dataclass(frozen=True)
class CatalogSelectors:
    cards: tuple[str, ...]
    names: tuple[str, ...]
    current_prices: tuple[str, ...]
    regular_prices: tuple[str, ...]
    promotions: tuple[str, ...]
    links: tuple[str, ...]
    availability: tuple[str, ...]
    fulfillment: tuple[str, ...]
    next_links: tuple[str, ...]


def parse_money(text: str) -> Decimal | None:
    match = MONEY_PATTERN.search(text)
    if not match:
        return None
    try:
        value = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


def parse_multibuy(text: str) -> tuple[int, Decimal, Decimal] | None:
    match = MULTIBUY_PATTERN.search(text)
    if not match:
        return None
    quantity = int(match.group(1))
    total = Decimal(match.group(2))
    if quantity <= 1 or total <= 0:
        return None
    return quantity, total, (total / quantity).quantize(Decimal("0.01"))


def canonical_url(url: str, base_url: str) -> str:
    absolute = urljoin(base_url, url)
    parts = urlsplit(absolute)
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.casefold() not in TRACKING_KEYS and not key.casefold().startswith("utm_")
        ]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _first_text(node: Tag, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        found = node.select_one(selector)
        if found:
            value = found.get_text(" ", strip=True)
            if value:
                return value
    return ""


def _first_attribute(node: Tag, selectors: tuple[str, ...], attribute: str) -> str:
    for selector in selectors:
        found = node.select_one(selector)
        if found and found.get(attribute):
            return str(found[attribute]).strip()
    return ""


def _target_brand(name: str) -> str | None:
    lowered = name.casefold()
    if "alani" in lowered:
        return "Alani Nu"
    if "ghost" in lowered:
        return "Ghost"
    if re.search(r"(?:^|\W)c4(?:\W|$)", lowered):
        return "C4"
    if "monster" in lowered:
        return "Monster"
    return None


def _is_candidate(name: str, requested_brands: set[str]) -> bool:
    lowered = name.casefold()
    brand = _target_brand(name)
    return bool(
        brand
        and brand.casefold() in requested_brands
        and "energy" in lowered
        and not any(term in lowered for term in EXCLUDED_TERMS)
    )


def _availability(text: str) -> bool | None:
    lowered = text.casefold()
    if any(marker in lowered for marker in ("out of stock", "unavailable", "sold out")):
        return False
    if any(marker in lowered for marker in ("in stock", "available today", "ready for pickup")):
        return True
    return None


def _fulfillment(text: str) -> str:
    lowered = text.casefold()
    if "pickup" in lowered:
        return "pickup"
    if "delivery" in lowered:
        return "delivery"
    if "shipping" in lowered or "ship" in lowered:
        return "online"
    return "unknown"


def _deduplicate_raw(offers: list[RawOffer]) -> list[RawOffer]:
    selected: dict[tuple[str, str, str, str], RawOffer] = {}
    for offer in offers:
        key = (
            offer.retailer.casefold(),
            (offer.source_product_id or "").casefold(),
            offer.product_name.casefold(),
            str(offer.listed_price),
        )
        existing = selected.get(key)
        completeness = sum(
            value is not None
            for value in (
                offer.source_product_id,
                offer.regular_price,
                offer.promotion_text,
                offer.in_stock,
            )
        )
        existing_completeness = (
            sum(
                value is not None
                for value in (
                    existing.source_product_id,
                    existing.regular_price,
                    existing.promotion_text,
                    existing.in_stock,
                )
            )
            if existing
            else -1
        )
        if existing is None or completeness >= existing_completeness:
            selected[key] = offer
    return list(selected.values())


class CatalogDiscoverySource(RetailerSource):
    """Base for bounded discovery on public category pages."""

    retailer: str
    selectors: CatalogSelectors
    experimental = False

    def __init__(
        self,
        config: DiscoverySourceConfig,
        crawler_config: CrawlerConfig | None = None,
        crawler: EthicalPageCrawler | None = None,
    ) -> None:
        self.config = config
        self.crawler_config = crawler_config or CrawlerConfig()
        self._crawler = crawler
        self.last_results: list[CrawlResult] = []
        self.last_diagnostics: list[SourceDiagnostic] = []
        self._next_urls: dict[str, str] = {}
        self._requested_brands: set[str] = set()

    async def search(self, request: SearchRequest) -> list[RawOffer]:
        return await self.discover(request)

    async def discover(self, request: SearchRequest) -> list[RawOffer]:
        self.last_results = []
        self.last_diagnostics = []
        self._next_urls = {}
        self._requested_brands = {brand.casefold() for brand in request.brands}
        pending = [str(url) for url in self.config.discovery_urls]
        visited: set[str] = set()
        found: list[RawOffer] = []
        page_budget = min(
            self.config.maximum_pages,
            self.crawler_config.maximum_pages_per_source_per_scan,
        )
        crawler_instance = self._crawler or EthicalPageCrawler(self.crawler_config)
        async with crawler_instance as crawler:
            while pending and len(visited) < page_budget:
                page_url = pending.pop(0)
                if page_url in visited:
                    continue
                visited.add(page_url)
                result = await crawler.crawl(
                    source=self.name,
                    url=page_url,
                    static_extractor=self._extract_catalog,
                    embedded_extractor=self._extract_embedded,
                    rendered_extractor=self._extract_catalog,
                    public_json_extractor=self._extract_public_json,
                )
                self.last_results.append(result)
                self.last_diagnostics.extend(result.fetch.diagnostics)
                found.extend(result.offers)
                next_url = self._next_urls.get(page_url)
                if next_url and next_url not in visited:
                    pending.append(next_url)
                if result.fetch.blocked_reason:
                    break
        offers = _deduplicate_raw(found)
        if request.online_only:
            offers = [offer for offer in offers if offer.fulfillment_type == "online"]
        if request.pickup_only:
            offers = [offer for offer in offers if offer.fulfillment_type == "pickup"]
        if not offers:
            reasons = sorted(
                {result.failure_reason or "unsupported" for result in self.last_results}
            )
            raise RetailerDiscoveryUnavailable(
                f"no permitted public {self.retailer} offers found ({', '.join(reasons)})"
            )
        return offers

    def _extract_catalog(self, html: str, page_url: str) -> list[RawOffer]:
        structured = [
            self._prepare(offer, page_url)
            for offer in extract_jsonld_offers(html, page_url, self.retailer)
            if _is_candidate(offer.product_name, self._requested_brands)
        ]
        if structured:
            return structured
        soup = BeautifulSoup(html, "html.parser")
        self._remember_next(soup, page_url)
        cards: list[Tag] = []
        for selector in self.selectors.cards:
            cards.extend(node for node in soup.select(selector) if isinstance(node, Tag))
        offers: list[RawOffer] = []
        for card in cards:
            name = _first_text(card, self.selectors.names)
            if not _is_candidate(name, self._requested_brands):
                continue
            current_text = _first_text(card, self.selectors.current_prices)
            listed_price = parse_money(current_text)
            if listed_price is None:
                continue
            regular_price = parse_money(_first_text(card, self.selectors.regular_prices))
            promo_text = _first_text(card, self.selectors.promotions)
            promotion = parse_multibuy(promo_text)
            link = _first_attribute(card, self.selectors.links, "href") or page_url
            availability_text = _first_text(card, self.selectors.availability)
            fulfillment_text = _first_text(card, self.selectors.fulfillment)
            discount = None
            if regular_price and regular_price > listed_price:
                discount = float((regular_price - listed_price) * 100 / regular_price)
            offers.append(
                RawOffer(
                    source=self.name,
                    retailer=self.retailer,
                    source_product_id=(
                        str(card.get("data-product-id") or card.get("data-sku") or "") or None
                    ),
                    product_name=name,
                    canonical_brand=_target_brand(name),
                    listed_price=listed_price,
                    regular_price=regular_price,
                    advertised_unit_price=(
                        current_text
                        if "per" in current_text.casefold() or "/fluid ounce" in current_text
                        else None
                    ),
                    promotion_text=promo_text or None,
                    promotion_required_quantity=promotion[0] if promotion else None,
                    promotion_total=promotion[1] if promotion else None,
                    promotional_unit_price=promotion[2] if promotion else None,
                    advertised_discount_percent=discount,
                    fulfillment_type=_fulfillment(fulfillment_text),
                    in_stock=_availability(availability_text),
                    url=canonical_url(link, page_url),
                    data_confidence=0.78,
                    notes=["Discovered on a public retailer catalog page"],
                )
            )
        return _deduplicate_raw(offers)

    def _extract_embedded(self, html: str, page_url: str) -> list[RawOffer]:
        offers = extract_embedded_product_data(html, page_url, self.retailer)
        return [
            self._prepare(offer, page_url)
            for offer in offers
            if _is_candidate(offer.product_name, self._requested_brands)
        ]

    def _extract_public_json(self, payload: Any, page_url: str, endpoint: str) -> list[RawOffer]:
        offers = extract_public_product_json(payload, page_url, self.retailer, endpoint)
        return [
            self._prepare(offer, page_url)
            for offer in offers
            if _is_candidate(offer.product_name, self._requested_brands)
        ]

    def _prepare(self, offer: RawOffer, page_url: str) -> RawOffer:
        return offer.model_copy(
            update={
                "source": self.name,
                "retailer": self.retailer,
                "url": canonical_url(offer.url, page_url),
            }
        )

    def _remember_next(self, soup: BeautifulSoup, page_url: str) -> None:
        for selector in self.selectors.next_links:
            link = soup.select_one(selector)
            if link and link.get("href"):
                candidate = canonical_url(str(link["href"]), page_url)
                if urlsplit(candidate).netloc == urlsplit(page_url).netloc:
                    self._next_urls[page_url] = candidate
                return

    async def healthcheck(self) -> SourceStatus:
        detail = f"configured with {len(self.config.discovery_urls)} public catalog URL(s)"
        if self.experimental:
            detail = f"experimental; {detail}"
        return SourceStatus(name=self.name, healthy=bool(self.config.discovery_urls), detail=detail)
