from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from caffeine_scout.config import CrawlerConfig, DiscoverySourceConfig
from caffeine_scout.crawler import EthicalPageCrawler
from caffeine_scout.models import SearchRequest
from caffeine_scout.normalization import normalize_offer
from caffeine_scout.sources.cvs import CVSSource
from caffeine_scout.sources.discovery import RetailerDiscoveryUnavailable, parse_multibuy
from caffeine_scout.sources.target import TargetSource

FIXTURES = Path(__file__).parent / "fixtures"


def _request() -> SearchRequest:
    return SearchRequest(
        zip_code="19103",
        maximum_distance_miles=5,
        brands=["Alani Nu", "Ghost", "C4", "Monster"],
    )


def _crawler_config() -> CrawlerConfig:
    return CrawlerConfig(
        minimum_request_interval_seconds=0,
        response_cache_hours=12,
        maximum_pages_per_source_per_scan=3,
    )


def test_parse_multibuy_preserves_required_quantity() -> None:
    assert parse_multibuy("Buy 5 for $12") == (5, Decimal("12"), Decimal("2.40"))
    assert parse_multibuy("Save $2 today") is None


def test_target_cards_promo_duplicates_exclusions_and_malformed(config) -> None:  # type: ignore[no-untyped-def]
    source = TargetSource(
        DiscoverySourceConfig(discovery_urls=["https://www.target.com/c/energy-drinks/-/N-4uez2"])
    )
    source._requested_brands = {"alani nu", "ghost", "c4", "monster"}
    html = (FIXTURES / "target_category_page_1.html").read_text(encoding="utf-8")
    offers = source._extract_catalog(html, "https://www.target.com/c/energy-drinks/-/N-4uez2")
    assert len(offers) == 1
    raw = offers[0]
    assert raw.canonical_brand == "Alani Nu"
    assert raw.promotion_required_quantity == 5
    assert raw.promotion_total == Decimal("12")
    assert raw.promotional_unit_price == Decimal("2.40")
    assert "utm_" not in raw.url

    offer = normalize_offer(raw, config)
    assert offer.pack_count == 5
    assert offer.effective_price == Decimal("12.00")
    assert offer.price_per_can == Decimal("2.4000")
    assert offer.listed_price == Decimal("2.79")


def test_cvs_cards_sale_regular_inventory_and_exclusions() -> None:
    source = CVSSource(
        DiscoverySourceConfig(
            discovery_urls=["https://www.cvs.com/shop/grocery/beverages/sport-energy-drinks"]
        )
    )
    source._requested_brands = {"alani nu", "ghost", "c4", "monster"}
    html = (FIXTURES / "cvs_category_page_1.html").read_text(encoding="utf-8")
    offers = source._extract_catalog(
        html, "https://www.cvs.com/shop/grocery/beverages/sport-energy-drinks"
    )
    assert {offer.canonical_brand for offer in offers} == {"Alani Nu", "C4"}
    alani = next(offer for offer in offers if offer.canonical_brand == "Alani Nu")
    c4 = next(offer for offer in offers if offer.canonical_brand == "C4")
    assert alani.listed_price == Decimal("2.49")
    assert alani.regular_price == Decimal("2.99")
    assert alani.advertised_discount_percent == pytest.approx(16.72, rel=0.01)
    assert alani.in_stock is None
    assert c4.in_stock is False


@pytest.mark.asyncio
async def test_target_discovery_follows_at_most_configured_pages(tmp_path: Path) -> None:
    page_one = "https://www.target.com/c/energy-drinks/-/N-4uez2"
    page_two = "https://www.target.com/c/energy-drinks/-/N-4uez2?page=2"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        body = (
            FIXTURES
            / (
                "target_category_page_2.html"
                if str(request.url) == page_two
                else "target_category_page_1.html"
            )
        ).read_text(encoding="utf-8")
        return httpx.Response(
            200, text=body, headers={"content-type": "text/html"}, request=request
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    crawler = EthicalPageCrawler(_crawler_config(), client=client, cache_dir=tmp_path)
    source = TargetSource(
        DiscoverySourceConfig(discovery_urls=[page_one], maximum_pages=2),
        _crawler_config(),
        crawler,
    )
    offers = await source.discover(_request())
    await client.aclose()
    assert len(source.last_results) == 2
    assert {offer.canonical_brand for offer in offers} == {"Alani Nu", "Ghost", "Monster"}
    ghost = next(offer for offer in offers if offer.canonical_brand == "Ghost")
    assert ghost.regular_price == Decimal("24.99")
    assert ghost.advertised_unit_price == "$18.99 ($0.10/fluid ounce)"
    monster = next(offer for offer in offers if offer.canonical_brand == "Monster")
    assert monster.listed_price == Decimal("24.49")


@pytest.mark.asyncio
async def test_cvs_pagination_and_explicit_inventory(tmp_path: Path) -> None:
    page_one = "https://www.cvs.com/shop/grocery/beverages/sport-energy-drinks"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        fixture = (
            "cvs_category_page_2.html"
            if request.url.params.get("page") == "2"
            else "cvs_category_page_1.html"
        )
        return httpx.Response(
            200,
            text=(FIXTURES / fixture).read_text(encoding="utf-8"),
            headers={"content-type": "text/html"},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source = CVSSource(
        DiscoverySourceConfig(discovery_urls=[page_one], maximum_pages=2),
        _crawler_config(),
        EthicalPageCrawler(_crawler_config(), client=client, cache_dir=tmp_path),
    )
    offers = await source.discover(_request())
    await client.aclose()
    ghost = next(offer for offer in offers if offer.canonical_brand == "Ghost")
    assert ghost.in_stock is True
    assert len(source.last_results) == 2


@pytest.mark.asyncio
async def test_source_refusal_stops_without_fallback(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        return httpx.Response(403, text="Access denied", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source = TargetSource(
        DiscoverySourceConfig(discovery_urls=["https://www.target.com/c/energy-drinks/-/N-4uez2"]),
        _crawler_config(),
        EthicalPageCrawler(_crawler_config(), client=client, cache_dir=tmp_path),
    )
    with pytest.raises(RetailerDiscoveryUnavailable, match="refused_access"):
        await source.discover(_request())
    await client.aclose()
    assert len(source.last_results) == 1
    assert source.last_results[0].fetch.method_used is None
