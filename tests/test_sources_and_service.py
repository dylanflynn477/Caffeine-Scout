from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from caffeine_scout.config import AppConfig
from caffeine_scout.database import Repository
from caffeine_scout.models import RawOffer, RetailerSource, SearchRequest
from caffeine_scout.service import scan
from caffeine_scout.sources.jsonld import (
    StructuredPricingUnavailable,
    parse_jsonld_product_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


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
