"""Concurrent scan orchestration and fault isolation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from pydantic import ValidationError

from caffeine_scout.config import AppConfig
from caffeine_scout.database import Repository
from caffeine_scout.models import (
    Offer,
    RetailerSource,
    ScanResult,
    SearchRequest,
    SourceError,
)
from caffeine_scout.normalization import (
    NormalizationError,
    deduplicate_offers,
    normalize_offer,
    offer_fingerprint,
)
from caffeine_scout.scoring import calculate_robbery_score
from caffeine_scout.sources import AmazonSource, JsonLdProductPageSource, MockSource


def build_sources(config: AppConfig) -> list[RetailerSource]:
    sources: list[RetailerSource] = []
    if config.sources.mock.enabled:
        sources.append(MockSource())
    if config.sources.jsonld.enabled:
        sources.append(JsonLdProductPageSource(config.sources.jsonld))
    if config.sources.amazon.enabled:
        sources.append(AmazonSource(config.sources.amazon))
    return sources


async def _run_source(
    source: RetailerSource, request: SearchRequest
) -> tuple[str, list[object], Exception | None]:
    try:
        return source.name, list(await source.search(request)), None
    except Exception as exc:  # adapters are an explicit fault-isolation boundary
        return source.name, [], exc


async def scan(
    config: AppConfig,
    repository: Repository,
    *,
    brand: str | None = None,
    online_only: bool = False,
    pickup_only: bool = False,
    sources: list[RetailerSource] | None = None,
) -> ScanResult:
    selected_sources = sources if sources is not None else build_sources(config)
    requested_brands = [brand] if brand else [item.name for item in config.brands]
    request = SearchRequest(
        zip_code=config.location.zip_code,
        maximum_distance_miles=config.location.maximum_distance_miles,
        brands=requested_brands,
        online_only=online_only,
        pickup_only=pickup_only,
    )
    started = datetime.now(UTC)
    scan_id = repository.start_scan(request.zip_code, len(selected_sources), started)
    outcomes = await asyncio.gather(*(_run_source(source, request) for source in selected_sources))
    errors: list[SourceError] = []
    offers: list[Offer] = []
    quarantined = 0
    successful = 0
    for source_name, raw_items, error in outcomes:
        if error is not None:
            errors.append(
                SourceError(
                    source=source_name,
                    error_type=type(error).__name__,
                    message=str(error).replace("\n", " ")[:300],
                )
            )
            continue
        successful += 1
        for raw in raw_items:
            try:
                from caffeine_scout.models import RawOffer

                validated = raw if isinstance(raw, RawOffer) else RawOffer.model_validate(raw)
                offer = normalize_offer(validated, config)
                if brand and offer.canonical_brand.casefold() != brand.casefold():
                    continue
                offers.append(offer)
            except (NormalizationError, ValidationError, ValueError, TypeError):
                quarantined += 1

    offers = deduplicate_offers(offers)
    scored: list[Offer] = []
    for offer in offers:
        fingerprint = offer_fingerprint(offer)
        history = repository.historical_prices(fingerprint, config.scoring.history_window_days)
        offer.is_new_historical_low = repository.is_new_historical_low(
            fingerprint, offer.price_per_can
        )
        score, label = calculate_robbery_score(offer, config.scoring, history)
        scored.append(offer.model_copy(update={"robbery_score": score, "robbery_label": label}))
    scored.sort(key=lambda item: (-(item.robbery_score or 0), item.price_per_can))
    repository.record_offers(scan_id, scored)
    if errors:
        repository.record_errors(scan_id, errors)
    completed = datetime.now(UTC)
    repository.finish_scan(scan_id, completed, successful, len(scored))
    return ScanResult(
        scan_id=scan_id,
        zip_code=request.zip_code,
        started_at=started,
        completed_at=completed,
        sources_attempted=len(selected_sources),
        successful_sources=successful,
        offers=scored,
        errors=errors,
        quarantined_count=quarantined,
    )
