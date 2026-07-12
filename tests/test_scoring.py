from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import make_offer

from caffeine_scout.config import AppConfig
from caffeine_scout.scoring import (
    calculate_robbery_score,
    historical_median,
    robbery_label,
)


@pytest.mark.parametrize(
    ("score", "label"),
    [
        (0, "Market Price"),
        (34, "Market Price"),
        (35, "Barely Discounted"),
        (54, "Barely Discounted"),
        (55, "Slight Discount"),
        (69, "Slight Discount"),
        (70, "Moderate Discount"),
        (84, "Moderate Discount"),
        (85, "High Discount"),
        (94, "High Discount"),
        (95, "Extreme Discount"),
        (100, "Extreme Discount"),
    ],
)
def test_every_robbery_label_boundary(score: int, label: str) -> None:
    assert robbery_label(score) == label


def test_historical_median_even_and_odd() -> None:
    assert historical_median([Decimal("1"), Decimal("3"), Decimal("2")]) == Decimal("2")
    assert historical_median([Decimal("1"), Decimal("2")]) == Decimal("1.5")
    assert historical_median([]) is None


@pytest.mark.parametrize("price", [Decimal("0.01"), Decimal("99.99")])
def test_score_is_always_clamped(config: AppConfig, price: Decimal) -> None:
    offer = make_offer(price_per_can=price)
    score, _ = calculate_robbery_score(
        offer,
        config.scoring,
        [Decimal("3"), Decimal("3"), Decimal("3")],
    )
    assert 0 <= score <= 100


def test_history_component_requires_minimum_samples(config: AppConfig) -> None:
    offer = make_offer(price_per_can=Decimal("1.50"))
    without, _ = calculate_robbery_score(offer, config.scoring, [Decimal("2.50")] * 2)
    with_history, _ = calculate_robbery_score(offer, config.scoring, [Decimal("2.50")] * 3)
    assert with_history > without
