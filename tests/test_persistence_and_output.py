from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from conftest import make_offer

from caffeine_scout.database import Repository
from caffeine_scout.models import ScanResult
from caffeine_scout.presentation import scan_csv, scan_json


def test_no_duplicate_snapshot_within_same_scan(tmp_path: Path) -> None:
    repository = Repository(f"sqlite:///{tmp_path / 'history.db'}")
    now = datetime.now(UTC)
    scan_id = repository.start_scan("19103", 1, now)
    offer = make_offer()
    assert repository.record_offers(scan_id, [offer, offer]) == 1


def test_history_latest_low_median_and_change(tmp_path: Path) -> None:
    repository = Repository(f"sqlite:///{tmp_path / 'history.db'}")
    prices = [Decimal("2.0000"), Decimal("1.5000"), Decimal("1.7500")]
    for index, price in enumerate(prices):
        observed = datetime.now(UTC) - timedelta(days=2 - index)
        scan_id = repository.start_scan("19103", 1, observed)
        offer = make_offer(
            listed_price=price * 12,
            effective_price=price * 12,
            price_per_can=price,
            collected_at=observed,
        )
        repository.record_offers(scan_id, [offer])
    row = repository.history()[0]
    assert row.latest_price == Decimal("1.7500")
    assert row.lowest_price == Decimal("1.5000")
    assert row.median_30d == Decimal("1.75")
    assert row.change_from_previous == Decimal("0.2500")


def test_json_and_csv_output_are_machine_readable() -> None:
    offer = make_offer(robbery_score=80, robbery_label="Moderate Discount")
    result = ScanResult(
        scan_id=1,
        zip_code="19103",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        sources_attempted=1,
        successful_sources=1,
        offers=[offer],
        errors=[],
    )
    payload = json.loads(scan_json(result))
    assert payload["offers"][0]["price_per_can"] == "1.5000"
    rows = list(csv.DictReader(io.StringIO(scan_csv([offer]))))
    assert rows[0]["brand"] == "Ghost"
    assert rows[0]["effective_price"] == "18.00"
