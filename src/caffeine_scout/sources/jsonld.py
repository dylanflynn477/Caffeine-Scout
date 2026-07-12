"""Progressive extraction for public product pages and embedded product JSON."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from bs4 import BeautifulSoup

from caffeine_scout.config import CrawlerConfig, JsonLdSourceConfig
from caffeine_scout.crawler import EthicalPageCrawler
from caffeine_scout.models import (
    CrawlResult,
    RawOffer,
    RetailerSource,
    SearchRequest,
    SourceDiagnostic,
    SourceStatus,
)


class StructuredPricingUnavailable(RuntimeError):
    pass


def _walk(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value, *(node for child in value.values() for node in _walk(child))]
    if isinstance(value, list):
        return [node for child in value for node in _walk(child)]
    return []


def _types(node: dict[str, Any]) -> set[str]:
    value = node.get("@type", "")
    values = value if isinstance(value, list) else [value]
    return {str(item).casefold() for item in values}


def _brand_name(value: Any) -> str | None:
    if isinstance(value, dict):
        name = value.get("name")
        return str(name).strip() if name else None
    return str(value).strip() if value else None


def _iter_products(
    value: Any, inherited_brand: str | None = None
) -> list[tuple[dict[str, Any], str | None]]:
    products: list[tuple[dict[str, Any], str | None]] = []
    if isinstance(value, dict):
        brand = _brand_name(value.get("brand")) or inherited_brand
        if "product" in _types(value):
            products.append((value, brand))
        for child in value.values():
            products.extend(_iter_products(child, brand))
    elif isinstance(value, list):
        for child in value:
            products.extend(_iter_products(child, inherited_brand))
    return products


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, dict):
        for key in ("value", "price", "amount", "lowPrice"):
            if key in value:
                parsed = _decimal(value[key])
                if parsed is not None:
                    return parsed
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _offer_price(offer: dict[str, Any]) -> Decimal | None:
    for key in ("price", "lowPrice", "salePrice", "currentPrice"):
        if key in offer:
            price = _decimal(offer[key])
            if price is not None:
                return price
    specification = offer.get("priceSpecification")
    if specification is not None:
        return _decimal(specification)
    return None


def _offer_nodes(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [node for item in value for node in _offer_nodes(item)]
    if not isinstance(value, dict):
        return []
    nested = value.get("offers")
    candidates = [value]
    if nested is not None:
        candidates.extend(_offer_nodes(nested))
    return candidates


def _availability(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").casefold()
    if any(marker in text for marker in ("instock", "in_stock", "available")):
        return True
    if any(marker in text for marker in ("outofstock", "out_of_stock", "unavailable")):
        return False
    return None


def extract_jsonld_offers(
    html: str, url: str, retailer: str = "JSON-LD retailer"
) -> list[RawOffer]:
    soup = BeautifulSoup(html, "html.parser")
    documents: list[Any] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            documents.append(json.loads(script.get_text(strip=True)))
        except (json.JSONDecodeError, TypeError):
            continue
    return extract_public_product_json(documents, url, retailer, url, structured_only=True)


def parse_jsonld_product_page(
    html: str, url: str, retailer: str = "JSON-LD retailer"
) -> list[RawOffer]:
    results = extract_jsonld_offers(html, url, retailer)
    if not results:
        raise StructuredPricingUnavailable("no Product Offer or AggregateOffer pricing found")
    return results


def extract_embedded_product_data(
    html: str, url: str, retailer: str = "JSON-LD retailer"
) -> list[RawOffer]:
    soup = BeautifulSoup(html, "html.parser")
    documents: list[Any] = []
    for script in soup.find_all("script"):
        script_type = str(script.get("type") or "").casefold()
        script_id = str(script.get("id") or "").casefold()
        named_product_state = any(
            marker in script_id
            for marker in ("next_data", "nuxt", "product", "redux", "hydration", "initial")
        )
        if script_type != "application/json" and not named_product_state:
            continue
        if any(marker in script_id for marker in ("analytics", "tracking", "customer")):
            continue
        try:
            documents.append(json.loads(script.get_text(strip=True)))
        except (json.JSONDecodeError, TypeError):
            continue
    return extract_public_product_json(documents, url, retailer, url)


def extract_public_product_json(
    payload: Any,
    page_url: str,
    retailer: str,
    endpoint: str,
    *,
    structured_only: bool = False,
) -> list[RawOffer]:
    results: list[RawOffer] = []
    seen: set[tuple[str, str, str | None]] = set()
    for product, inherited_brand in _iter_products(payload):
        name = str(product.get("name") or "").strip()
        if not name:
            continue
        offers_value = product.get("offers")
        for offer in _offer_nodes(offers_value):
            price = _offer_price(offer)
            if price is None:
                continue
            product_id = (
                str(product.get("sku") or product.get("productID") or product.get("id") or "")
                or None
            )
            key = (name, str(price), product_id)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                RawOffer(
                    source="jsonld",
                    retailer=retailer,
                    source_product_id=product_id,
                    product_name=name,
                    canonical_brand=_brand_name(product.get("brand")) or inherited_brand,
                    listed_price=price,
                    fulfillment_type="online",
                    in_stock=_availability(offer.get("availability")),
                    url=str(offer.get("url") or product.get("url") or page_url),
                    data_confidence=0.86,
                    notes=[
                        "Price parsed from public schema.org product data",
                        f"Public data endpoint: {endpoint}",
                    ],
                )
            )
    if structured_only:
        return results

    for candidate in _walk(payload):
        name = str(
            candidate.get("productName") or candidate.get("name") or candidate.get("title") or ""
        ).strip()
        if not name or "energy" not in name.casefold():
            continue
        price = None
        for price_key in ("currentPrice", "salePrice", "price"):
            if price_key in candidate:
                price = _decimal(candidate[price_key])
                if price is not None:
                    break
        if price is None:
            continue
        product_id = (
            str(candidate.get("sku") or candidate.get("productId") or candidate.get("id") or "")
            or None
        )
        fingerprint = (name, str(price), product_id)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        brand_value = candidate.get("brand") or candidate.get("brandName")
        availability_value = (
            candidate.get("availability")
            if "availability" in candidate
            else candidate.get("inStock")
        )
        results.append(
            RawOffer(
                source="jsonld",
                retailer=retailer,
                source_product_id=product_id,
                product_name=name,
                canonical_brand=_brand_name(brand_value),
                listed_price=price,
                fulfillment_type="online",
                in_stock=_availability(availability_value),
                url=str(candidate.get("canonicalUrl") or candidate.get("url") or page_url),
                data_confidence=0.72,
                notes=[
                    "Price parsed from public embedded product state",
                    f"Public data endpoint: {endpoint}",
                ],
            )
        )
    return results


class JsonLdProductPageSource(RetailerSource):
    name = "jsonld"

    def __init__(
        self,
        config: JsonLdSourceConfig,
        crawler_config: CrawlerConfig | None = None,
        crawler: EthicalPageCrawler | None = None,
    ) -> None:
        self.config = config
        self.crawler_config = crawler_config or CrawlerConfig()
        self._crawler = crawler
        self.last_diagnostics: list[SourceDiagnostic] = []
        self.last_results: list[CrawlResult] = []

    async def search(self, request: SearchRequest) -> list[RawOffer]:
        pages = self.config.enabled_product_pages
        if request.online_only:
            pages = [page for page in pages if page.fulfillment_type == "online"]
        if request.pickup_only:
            pages = [page for page in pages if page.fulfillment_type == "pickup"]
        pages = pages[: self.crawler_config.maximum_pages_per_source_per_scan]
        if not pages:
            return []

        self.last_diagnostics = []
        self.last_results = []
        results: list[RawOffer] = []
        crawler_instance = self._crawler or EthicalPageCrawler(self.crawler_config)
        async with crawler_instance as crawler:
            for page in pages:
                retailer = page.retailer

                def static_extractor(
                    html: str, url: str, retailer_name: str = retailer
                ) -> list[RawOffer]:
                    return extract_jsonld_offers(html, url, retailer_name)

                def embedded_extractor(
                    html: str, url: str, retailer_name: str = retailer
                ) -> list[RawOffer]:
                    return extract_embedded_product_data(html, url, retailer_name)

                def public_json_extractor(
                    payload: Any,
                    page_url: str,
                    endpoint: str,
                    retailer_name: str = retailer,
                ) -> list[RawOffer]:
                    return extract_public_product_json(payload, page_url, retailer_name, endpoint)

                result = await crawler.crawl(
                    source=retailer,
                    url=str(page.url),
                    static_extractor=static_extractor,
                    embedded_extractor=embedded_extractor,
                    public_json_extractor=public_json_extractor,
                )
                self.last_results.append(result)
                self.last_diagnostics.extend(result.fetch.diagnostics)
                for offer in result.offers:
                    results.append(
                        offer.model_copy(
                            update={
                                "fulfillment_type": page.fulfillment_type,
                                "store_name": page.store_name,
                                "store_address": page.store_address,
                                "distance_miles": page.distance_miles,
                                "notes": [*offer.notes, *page.notes],
                            }
                        )
                    )
        if not results:
            reasons = sorted(
                {result.failure_reason or "unsupported" for result in self.last_results}
            )
            raise StructuredPricingUnavailable(
                f"no permitted public offers found ({', '.join(reasons)})"
            )
        return results

    async def healthcheck(self) -> SourceStatus:
        enabled_count = len(self.config.enabled_product_pages)
        configured_count = len(self.config.product_urls) + len(self.config.product_pages)
        if not enabled_count:
            return SourceStatus(
                name=self.name,
                healthy=True,
                detail=(
                    f"enabled; 0/{configured_count} exact product pages active; "
                    f"{len(self.config.catalog_pages)} discovery-only catalog(s)"
                ),
            )
        return SourceStatus(
            name=self.name,
            healthy=True,
            detail=(
                f"configured with {enabled_count}/{configured_count} active exact URL(s); "
                "progressive ethical crawler enabled"
            ),
        )
