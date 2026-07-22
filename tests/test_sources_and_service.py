from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from caffeine_scout.config import AppConfig, CrawlerConfig, JsonLdSourceConfig
from caffeine_scout.crawler import EthicalPageCrawler
from caffeine_scout.database import Repository
from caffeine_scout.models import RawOffer, RetailerSource, SearchRequest
from caffeine_scout.service import scan
from caffeine_scout.sources.jsonld import (
    JsonLdProductPageSource,
    StructuredPricingUnavailable,
    parse_jsonld_product_page,
)
from caffeine_scout.sources.mock import MockSource

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_mock_source_includes_monster() -> None:
    offers = await MockSource().search(
        SearchRequest(zip_code="19103", maximum_distance_miles=5, brands=["Monster"])
    )
    assert len(offers) == 1
    assert offers[0].product_name == "Monster Energy Original - 12pk/16 fl oz Cans"
    assert offers[0].caffeine_mg_per_can == 160


class GoodSource(RetailerSource):
    name = "good"

    async def search(self, request: SearchRequest) -> list[RawOffer]:
        del request
        return [
            RawOffer(
                source=self.name,
                retailer="Good Shop",
                product_name="Ghost Energy Cherry Limeade 16 oz 12 Pack",
                pack_count=12,
                listed_price=Decimal("18.00"),
                in_stock=True,
                url="https://example.test/good",
            )
        ]


class FailingSource(RetailerSource):
    name = "broken"

    async def search(self, request: SearchRequest) -> list[RawOffer]:
        del request
        raise RuntimeError("fixture outage")


class MalformedSource(RetailerSource):
    name = "malformed"

    async def search(self, request: SearchRequest) -> list[RawOffer]:  # type: ignore[override]
        del request
        return [  # type: ignore[list-item]
            {
                "source": self.name,
                "retailer": "Bad Shop",
                "product_name": "C4 Energy Drink 12 Pack",
                "listed_price": "not-money",
                "url": "https://example.test/bad",
            }
        ]


@pytest.mark.asyncio
async def test_partial_source_failure_does_not_crash_scan(
    config: AppConfig, tmp_path: Path
) -> None:
    config.database_url = f"sqlite:///{tmp_path / 'partial.db'}"
    result = await scan(
        config, Repository(config.database_url), sources=[GoodSource(), FailingSource()]
    )
    assert len(result.offers) == 1
    assert result.successful_sources == 1
    assert result.errors[0].source == "broken"
    assert "fixture outage" in result.errors[0].message


@pytest.mark.asyncio
async def test_malformed_source_data_is_quarantined(config: AppConfig, tmp_path: Path) -> None:
    config.database_url = f"sqlite:///{tmp_path / 'malformed.db'}"
    result = await scan(
        config, Repository(config.database_url), sources=[GoodSource(), MalformedSource()]
    )
    assert len(result.offers) == 1
    assert result.quarantined_count == 1


def test_saved_json_fixture_is_well_formed() -> None:
    data = json.loads((FIXTURES / "raw_offers.json").read_text(encoding="utf-8"))
    assert len(data) == 2


def test_jsonld_offer_fixture() -> None:
    html = (FIXTURES / "product_offer.html").read_text(encoding="utf-8")
    offers = parse_jsonld_product_page(html, "https://example.test/product")
    assert offers[0].listed_price == Decimal("21.99")
    assert offers[0].in_stock is True
    assert offers[0].source_product_id == "GH-RED-12"


def test_jsonld_aggregate_offer_fixture() -> None:
    html = (FIXTURES / "product_aggregate_offer.html").read_text(encoding="utf-8")
    offers = parse_jsonld_product_page(html, "https://example.test/product")
    assert offers[0].listed_price == Decimal("18.50")


def test_jsonld_reports_unsupported_without_structured_price() -> None:
    html = (FIXTURES / "no_pricing.html").read_text(encoding="utf-8")
    with pytest.raises(StructuredPricingUnavailable, match="no Product Offer"):
        parse_jsonld_product_page(html, "https://example.test/product")


@pytest.mark.asyncio
async def test_configured_jsonld_page_applies_retailer_and_store_metadata(
    tmp_path: Path,
) -> None:
    html = (FIXTURES / "product_offer.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    crawler = EthicalPageCrawler(
        CrawlerConfig(minimum_request_interval_seconds=0),
        client=client,
        cache_dir=tmp_path / "cache",
        playwright_loader=None,
    )
    source = JsonLdProductPageSource(
        JsonLdSourceConfig.model_validate(
            {
                "product_pages": [
                    {
                        "enabled": True,
                        "retailer": "The Vitamin Shoppe",
                        "url": "https://example.test/ghost",
                        "fulfillment_type": "pickup",
                        "store_name": "Chestnut St",
                        "store_address": "1701 Chestnut St, Philadelphia, PA 19103",
                        "distance_miles": 0.2,
                        "notes": ["Fixture pickup metadata"],
                    }
                ]
            }
        ),
        crawler=crawler,
    )
    try:
        offers = await source.search(
            SearchRequest(zip_code="19103", maximum_distance_miles=5, brands=["Ghost"])
        )
    finally:
        await client.aclose()
    assert offers[0].retailer == "The Vitamin Shoppe"
    assert offers[0].fulfillment_type == "pickup"
    assert offers[0].store_name == "Chestnut St"
    assert offers[0].distance_miles == 0.2
    assert "Fixture pickup metadata" in offers[0].notes


@pytest.mark.asyncio
async def test_disabled_jsonld_sample_page_is_not_requested() -> None:
    source = JsonLdProductPageSource(
        JsonLdSourceConfig.model_validate(
            {
                "product_pages": [
                    {
                        "enabled": False,
                        "retailer": "GNC",
                        "url": "https://example.test/ghost",
                    }
                ]
            }
        )
    )
    offers = await source.search(
        SearchRequest(zip_code="19103", maximum_distance_miles=5, brands=["Ghost"])
    )
    assert offers == []
