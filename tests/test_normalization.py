from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import make_offer

from caffeine_scout.config import AppConfig
from caffeine_scout.models import RawOffer
from caffeine_scout.normalization import (
    NormalizationError,
    canonicalize_brand,
    deduplicate_offers,
    extract_can_size_oz,
    extract_flavor,
    extract_pack_count,
    extract_total_quantity_oz,
    is_relevant_product,
    normalize_offer,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Alani Nu Energy Drink Cherry Slush, 12 Fl Oz Cans, Pack of 12", 12),
        ("GHOST Energy 16oz (12 Pack)", 12),
        ("C4 Performance Energy Drink Variety Pack, 12 Count", 12),
        ("GHOST Energy Drink - Orange Cream - 16oz. (12 Cans)", 12),
        ("Alani Nu 24-Pack 12 oz Energy Drinks", 24),
        ("12 oz single can", 1),
        ("12 packets", None),
    ],
)
def test_pack_count_extraction(name: str, expected: int | None) -> None:
    assert extract_pack_count(name) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("12 Fl Oz Cans, Pack of 12", 12.0),
        ("GHOST Energy 16oz (12 Pack)", 16.0),
        ("C4 Energy 11.5 fl. oz can", 11.5),
        ("C4 Energy 12 Count", None),
    ],
)
def test_can_size_extraction(name: str, expected: float | None) -> None:
    assert extract_can_size_oz(name) == expected


def test_total_quantity_extraction() -> None:
    assert extract_total_quantity_oz("Alani Nu 24-Pack 12 oz Energy Drinks") == 288.0


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Alani Cherry Slush Energy", "Alani Nu"),
        ("GHOST ENERGY Redberry", "Ghost"),
        ("Cellucor C4 Energy", "C4"),
    ],
)
def test_brand_aliases(config: AppConfig, name: str, expected: str) -> None:
    brand = canonicalize_brand(name, config.brands)
    assert brand is not None and brand.name == expected


def test_flavor_extraction(config: AppConfig) -> None:
    ghost = next(item for item in config.brands if item.name == "Ghost")
    alani = next(item for item in config.brands if item.name == "Alani Nu")
    assert (
        extract_flavor("GHOST Energy Zero Sugar, Sour Patch Kids Redberry, 16oz (12 Pack)", ghost)
        == "Sour Patch Kids Redberry"
    )
    assert (
        extract_flavor("Alani Nu Energy Drink Cherry Slush, 12 Fl Oz Cans, Pack of 12", alani)
        == "Cherry Slush"
    )
    assert extract_flavor("Energy Drink - Orange Cream - 16oz. (12 Cans)", ghost) == "Orange Cream"


@pytest.mark.parametrize(
    "name",
    [
        "C4 energy powder",
        "Ghost pre-workout powder",
        "Alani Nu energy packets",
        "C4 energy supplement mix",
        "Ghost empty can collector item",
    ],
)
def test_powders_packets_and_irrelevant_products_are_excluded(config: AppConfig, name: str) -> None:
    accepted, _ = is_relevant_product(name, config)
    assert accepted is False


def test_decimal_coupons_shipping_and_caffeine_math(config: AppConfig) -> None:
    raw = RawOffer(
        source="test",
        retailer="Shop",
        product_name="Alani Nu Energy Drink Cherry Slush, 12 oz, Pack of 12",
        listed_price=Decimal("20.00"),
        coupon_value=Decimal("2.50"),
        shipping_cost=Decimal("4.99"),
        url="https://example.test/alani",
    )
    offer = normalize_offer(raw, config)
    assert offer.effective_price == Decimal("22.49")
    assert offer.price_per_can == Decimal("1.8742")
    assert offer.caffeine_mg_per_dollar == pytest.approx(106.714)


def test_multibuy_promotion_uses_required_purchase_quantity(config: AppConfig) -> None:
    raw = RawOffer(
        source="test",
        retailer="Shop",
        product_name="Alani Nu Energy Drink Cherry Slush, 12 oz single can",
        listed_price=Decimal("2.79"),
        promotion_text="Buy 5 for $12",
        promotion_required_quantity=5,
        promotion_total=Decimal("12"),
        promotional_unit_price=Decimal("2.40"),
        url="https://example.test/alani",
    )
    offer = normalize_offer(raw, config)
    assert offer.pack_count == 5
    assert offer.effective_price == Decimal("12.00")
    assert offer.price_per_can == Decimal("2.4000")
    assert any("Requires buying 5 items" in note for note in offer.notes)


def test_inconsistent_multibuy_is_quarantinable(config: AppConfig) -> None:
    raw = RawOffer(
        source="test",
        retailer="Shop",
        product_name="C4 Energy Drink 12 oz single can",
        listed_price=Decimal("2.99"),
        promotion_required_quantity=5,
        promotion_total=Decimal("12"),
        promotional_unit_price=Decimal("1.99"),
        url="https://example.test/c4",
    )
    with pytest.raises(NormalizationError, match="inconsistent"):
        normalize_offer(raw, config)


@pytest.mark.parametrize("price", ["0", "-1"])
def test_malformed_prices_are_rejected(config: AppConfig, price: str) -> None:
    raw = RawOffer(
        source="test",
        retailer="Shop",
        product_name="C4 Energy Drink, 12 oz, 12 Pack",
        listed_price=Decimal(price),
        url="https://example.test/c4",
    )
    with pytest.raises(NormalizationError):
        normalize_offer(raw, config)


def test_deduplication_retains_more_complete_then_recent_record() -> None:
    old = make_offer(
        source_product_id=None,
        flavor="Cherry Limeade",
        collected_at=datetime.now(UTC) - timedelta(hours=1),
    )
    complete = make_offer(
        source_product_id="complete",
        flavor="Cherry Limeade",
        collected_at=datetime.now(UTC) - timedelta(hours=2),
    )
    duplicate = make_offer(
        source_product_id="complete-new",
        flavor="Cherry Limeade",
        collected_at=datetime.now(UTC),
    )
    result = deduplicate_offers([old, complete, duplicate])
    assert len(result) == 1
    assert result[0].source_product_id == "complete-new"
