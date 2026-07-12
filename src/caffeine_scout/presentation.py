"""Human and machine-readable output."""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Literal, cast

from rich.console import Console
from rich.table import Table

from caffeine_scout.models import HistoryRow, Offer, ScanResult, SourceStatus


def scan_json(result: ScanResult) -> str:
    return json.dumps(result.model_dump(mode="json"), indent=2)


def scan_csv(offers: list[Offer]) -> str:
    output = io.StringIO(newline="")
    fields = [
        "score",
        "label",
        "brand",
        "product_name",
        "flavor",
        "pack_count",
        "can_size_oz",
        "effective_price",
        "price_per_can",
        "retailer",
        "fulfillment",
        "distance_miles",
        "shipping_cost",
        "availability",
        "conditions",
        "url",
        "collected_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for offer in offers:
        writer.writerow(_csv_row(offer))
    return output.getvalue()


def _conditions(offer: Offer) -> str:
    values = list(offer.notes)
    if offer.membership_required:
        values.append("membership required")
    if offer.subscription_required:
        values.append("subscription required")
    return "; ".join(dict.fromkeys(values)) or "-"


def _csv_row(offer: Offer) -> dict[str, Any]:
    return {
        "score": offer.robbery_score,
        "label": offer.robbery_label,
        "brand": offer.canonical_brand,
        "product_name": offer.product_name,
        "flavor": offer.flavor,
        "pack_count": offer.pack_count,
        "can_size_oz": offer.can_size_oz,
        "effective_price": str(offer.effective_price),
        "price_per_can": str(offer.price_per_can),
        "retailer": offer.retailer,
        "fulfillment": offer.fulfillment_type,
        "distance_miles": offer.distance_miles,
        "shipping_cost": str(offer.shipping_cost),
        "availability": offer.in_stock,
        "conditions": _conditions(offer),
        "url": offer.url,
        "collected_at": offer.collected_at.isoformat(),
    }


def print_scan(result: ScanResult, console: Console) -> None:
    console.print(
        "[bold cyan]Caffeine Scout[/bold cyan] - "
        f"ZIP {result.zip_code} | sources {result.successful_sources}/{result.sources_attempted} | "
        f"offers {len(result.offers)} | {result.completed_at.astimezone():%Y-%m-%d %H:%M:%S %Z}"
    )
    table = Table(show_lines=False, header_style="bold")
    for heading, justify in (
        ("Score", "right"),
        ("Brand", "left"),
        ("Product / flavor", "left"),
        ("Pack", "right"),
        ("Effective", "right"),
        ("Per can", "right"),
        ("Retailer", "left"),
        ("Fulfillment", "left"),
        ("Distance / shipping", "left"),
        ("Available", "left"),
        ("Conditions", "left"),
    ):
        table.add_column(heading, justify=cast(Literal["left", "right"], justify))
    for offer in result.offers:
        score = offer.robbery_score or 0
        score_text = f"{score}\n{offer.robbery_label or ''}"
        if score >= 85:
            score_text = f"[bold green]{score_text}[/bold green]"
        product = offer.flavor or offer.product_line or offer.product_name
        travel = (
            f"{offer.distance_miles:.1f} mi"
            if offer.distance_miles is not None
            else (f"${offer.shipping_cost:.2f} ship" if offer.shipping_cost else "free ship")
        )
        available = "yes" if offer.in_stock is True else "no" if offer.in_stock is False else "?"
        pack = (
            f"{offer.pack_count} x {offer.can_size_oz:g} oz"
            if offer.can_size_oz
            else str(offer.pack_count)
        )
        table.add_row(
            score_text,
            offer.canonical_brand,
            product,
            pack,
            f"${offer.effective_price:.2f}",
            f"${offer.price_per_can:.2f}",
            offer.retailer,
            offer.fulfillment_type,
            travel,
            available,
            _conditions(offer),
        )
    console.print(table)
    if result.quarantined_count:
        console.print(
            "[yellow]Quarantined malformed/irrelevant offers: "
            f"{result.quarantined_count}[/yellow]"
        )
    if result.errors:
        console.print("\n[bold red]Source failures[/bold red]")
        for error in result.errors:
            console.print(f"  {error.source}: {error.message}")


def print_history(rows: list[HistoryRow], console: Console) -> None:
    table = Table(title="Caffeine Scout price history", header_style="bold")
    for heading in (
        "Brand",
        "Product",
        "Retailer",
        "Latest / can",
        "Lowest",
        "30-day median",
        "Change",
        "Last seen",
    ):
        table.add_column(heading)
    for row in rows:
        change = "-" if row.change_from_previous is None else f"{row.change_from_previous:+.2f}"
        table.add_row(
            row.brand,
            row.product_name,
            row.retailer,
            f"${row.latest_price:.2f}",
            f"${row.lowest_price:.2f}",
            f"${row.median_30d:.2f}",
            change,
            row.last_seen.astimezone().strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


def print_sources(statuses: list[SourceStatus], console: Console) -> None:
    table = Table(title="Caffeine Scout sources")
    table.add_column("Source")
    table.add_column("Status")
    table.add_column("Detail")
    for status in statuses:
        state = "disabled" if not status.enabled else "ready" if status.healthy else "setup needed"
        table.add_row(status.name, state, status.detail)
    console.print(table)
