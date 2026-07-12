"""Polite parser for public product-page schema.org JSON-LD."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from caffeine_scout.config import JsonLdProductPageConfig, JsonLdSourceConfig
from caffeine_scout.models import RawOffer, RetailerSource, SearchRequest, SourceStatus


class StructuredPricingUnavailable(RuntimeError):
    pass


def _nodes(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        graph = value.get("@graph")
        return [*([value] if "@type" in value else []), *(_nodes(graph) if graph else [])]
    if isinstance(value, list):
        return [node for item in value for node in _nodes(item)]
    return []


def _is_type(node: dict[str, Any], expected: str) -> bool:
    node_type = node.get("@type", "")
    types = node_type if isinstance(node_type, list) else [node_type]
    return any(str(value).casefold() == expected.casefold() for value in types)


def parse_jsonld_product_page(
    html: str, url: str, retailer: str = "JSON-LD retailer"
) -> list[RawOffer]:
    soup = BeautifulSoup(html, "html.parser")
    parsed_nodes: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            parsed_nodes.extend(_nodes(json.loads(script.get_text(strip=True))))
        except (json.JSONDecodeError, TypeError):
            continue
    results: list[RawOffer] = []
    for product in (node for node in parsed_nodes if _is_type(node, "Product")):
        name = str(product.get("name") or "").strip()
        brand_value = product.get("brand")
        brand = brand_value.get("name") if isinstance(brand_value, dict) else brand_value
        offers_value = product.get("offers")
        offers = offers_value if isinstance(offers_value, list) else [offers_value]
        for offer in (item for item in offers if isinstance(item, dict)):
            price_value = offer.get("price")
            if price_value is None and _is_type(offer, "AggregateOffer"):
                price_value = offer.get("lowPrice")
            try:
                price = Decimal(str(price_value))
            except (InvalidOperation, TypeError):
                continue
            availability = str(offer.get("availability") or "")
            results.append(
                RawOffer(
                    source="jsonld",
                    retailer=retailer,
                    source_product_id=str(product.get("sku") or product.get("productID") or "")
                    or None,
                    product_name=name,
                    canonical_brand=str(brand) if brand else None,
                    listed_price=price,
                    fulfillment_type="online",
                    in_stock=("instock" in availability.casefold()) if availability else None,
                    url=str(offer.get("url") or url),
                    data_confidence=0.82,
                    notes=["Price parsed from public schema.org JSON-LD"],
                )
            )
    if not results:
        raise StructuredPricingUnavailable("no Product Offer or AggregateOffer pricing found")
    return results


class JsonLdProductPageSource(RetailerSource):
    name = "jsonld"

    def __init__(self, config: JsonLdSourceConfig) -> None:
        self.config = config

    async def search(self, request: SearchRequest) -> list[RawOffer]:
        pages = self.config.enabled_product_pages
        if request.online_only:
            pages = [page for page in pages if page.fulfillment_type == "online"]
        if request.pickup_only:
            pages = [page for page in pages if page.fulfillment_type == "pickup"]
        if not pages:
            return []
        results: list[RawOffer] = []
        unsupported: list[str] = []
        fetched_pages = await self._fetch_pages(pages)
        for page, html in fetched_pages:
            url = str(page.url)
            try:
                parsed = parse_jsonld_product_page(html, url, retailer=page.retailer)
                for offer in parsed:
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
            except StructuredPricingUnavailable:
                unsupported.append(url)
        if unsupported and not results:
            raise StructuredPricingUnavailable(
                f"structured pricing unavailable for {len(unsupported)} configured page(s)"
            )
        return results

    async def _fetch_pages(
        self, configured_pages: list[JsonLdProductPageConfig]
    ) -> list[tuple[JsonLdProductPageConfig, str]]:
        if self.config.use_playwright:
            pages: list[tuple[JsonLdProductPageConfig, str]] = []
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    page = await browser.new_page(user_agent=self.config.user_agent)
                    for index, configured_page in enumerate(configured_pages):
                        if index:
                            await asyncio.sleep(self.config.request_delay_seconds)
                        await page.goto(
                            str(configured_page.url),
                            wait_until="domcontentloaded",
                            timeout=int(self.config.timeout_seconds * 1000),
                        )
                        pages.append((configured_page, await page.content()))
                finally:
                    await browser.close()
            return pages

        pages = []
        headers = {"User-Agent": self.config.user_agent}
        async with httpx.AsyncClient(
            headers=headers,
            timeout=self.config.timeout_seconds,
            follow_redirects=True,
        ) as client:
            for index, configured_page in enumerate(configured_pages):
                if index:
                    await asyncio.sleep(self.config.request_delay_seconds)
                response = await client.get(str(configured_page.url))
                response.raise_for_status()
                pages.append((configured_page, response.text))
        return pages

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
                f"renderer={'playwright' if self.config.use_playwright else 'static HTTP'}"
            ),
        )
