from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from caffeine_scout.config import AppConfig
from caffeine_scout.models import Offer


@pytest.fixture
def config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "location": {"zip_code": "19103", "maximum_distance_miles": 5},
            "brands": [
                {
                    "name": "Alani Nu",
                    "aliases": ["Alani"],
                    "target_price_per_can": "1.40",
                    "default_caffeine_mg": 200,
                },
                {
                    "name": "Ghost",
                    "aliases": ["Ghost Energy"],
                    "target_price_per_can": "1.60",
                    "default_caffeine_mg": 200,
                },
                {
                    "name": "C4",
                    "aliases": ["C4 Energy", "Cellucor C4"],
                    "target_price_per_can": "1.50",
                    "default_caffeine_mg": 200,
                },
                {
                    "name": "Monster",
                    "aliases": ["Monster Energy", "Java Monster", "Juice Monster"],
                    "target_price_per_can": "1.75",
                    "default_caffeine_mg": None,
                },
            ],
            "product_filters": {
                "required_terms": ["energy"],
                "excluded_terms": [
                    "powder",
                    "pre-workout",
                    "packet",
                    "mix",
                    "supplement",
                    "empty can",
                ],
            },
            "sources": {
                "mock": {"enabled": True},
                "jsonld": {"enabled": False},
                "amazon": {"enabled": False},
            },
            "scoring": {
                "incredible_price_per_can": "1.00",
                "ordinary_price_per_can": "2.50",
                "history_window_days": 30,
                "minimum_history_samples": 3,
            },
            "alerts": {
                "minimum_robbery_score": 80,
                "maximum_price_per_can": "1.50",
                "notify_on_new_historical_low": True,
            },
            "database_url": "sqlite:///:memory:",
        }
    )


def make_offer(**updates: object) -> Offer:
    values: dict[str, object] = {
        "source": "test",
        "retailer": "Test Retailer",
        "source_product_id": "sku-1",
        "product_name": "Ghost Energy Cherry Limeade 16 oz 12 Pack",
        "canonical_brand": "Ghost",
        "product_line": None,
        "flavor": "Cherry Limeade",
        "pack_count": 12,
        "can_size_oz": 16,
        "caffeine_mg_per_can": 200,
        "listed_price": Decimal("18.00"),
        "coupon_value": Decimal("0"),
        "shipping_cost": Decimal("0"),
        "effective_price": Decimal("18.00"),
        "price_per_can": Decimal("1.5000"),
        "caffeine_mg_per_dollar": 133.333,
        "advertised_discount_percent": None,
        "fulfillment_type": "online",
        "store_name": None,
        "store_address": None,
        "distance_miles": None,
        "in_stock": True,
        "membership_required": False,
        "subscription_required": False,
        "url": "https://example.test/offer",
        "collected_at": datetime.now(UTC),
        "data_confidence": 1.0,
        "robbery_score": None,
        "robbery_label": None,
        "notes": [],
    }
    values.update(updates)
    return Offer.model_validate(values)
