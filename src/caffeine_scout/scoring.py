"""Deterministic Robbery Index scoring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median

from caffeine_scout.config import ScoringConfig
from caffeine_scout.models import Offer


def robbery_label(score: int) -> str:
    if score >= 95:
        return "Extreme Discount"
    if score >= 85:
        return "High Discount"
    if score >= 70:
        return "Moderate Discount"
    if score >= 55:
        return "Slight Discount"
    if score >= 35:
        return "Barely Discounted"
    return "Market Price"


def historical_median(prices: list[Decimal]) -> Decimal | None:
    return Decimal(str(median(prices))) if prices else None


def calculate_robbery_score(
    offer: Offer,
    config: ScoringConfig,
    historical_prices: list[Decimal] | None = None,
    *,
    now: datetime | None = None,
) -> tuple[int, str]:
    incredible = config.incredible_price_per_can
    ordinary = config.ordinary_price_per_can
    absolute_ratio = (ordinary - offer.price_per_can) / (ordinary - incredible)
    absolute_points = 55 * max(Decimal("0"), min(Decimal("1"), absolute_ratio))

    history_points = Decimal("0")
    prices = historical_prices or []
    if len(prices) >= config.minimum_history_samples:
        baseline = historical_median(prices)
        if baseline and baseline > 0 and offer.price_per_can < baseline:
            savings_ratio = (baseline - offer.price_per_can) / baseline
            history_points = Decimal("25") * min(Decimal("1"), savings_ratio / Decimal("0.5"))

    discount_points = (
        Decimal("10") if (offer.advertised_discount_percent or 0) > 0 else Decimal("0")
    )
    convenience_points = Decimal("0")
    if offer.shipping_cost == 0:
        convenience_points += Decimal("5")
    if offer.in_stock is True:
        convenience_points += Decimal("5")

    penalties = Decimal("0")
    if offer.shipping_cost > 0:
        penalties += Decimal("5")
    if offer.membership_required:
        penalties += Decimal("5")
    if offer.subscription_required:
        penalties += Decimal("5")
    if offer.data_confidence < 0.8:
        penalties += Decimal("5")
    current_time = now or datetime.now(UTC)
    collected = offer.collected_at
    if collected.tzinfo is None:
        collected = collected.replace(tzinfo=UTC)
    if current_time - collected > timedelta(hours=24):
        penalties += Decimal("5")

    raw_score = absolute_points + history_points + discount_points + convenience_points - penalties
    score = max(0, min(100, int(raw_score.quantize(Decimal("1")))))
    return score, robbery_label(score)
