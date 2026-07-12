"""Typer command-line interface."""

from __future__ import annotations

import asyncio
import shutil
from importlib.resources import as_file, files
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.console import Console

from caffeine_scout.alerts import TerminalAlertSink, matching_alerts
from caffeine_scout.config import load_config
from caffeine_scout.database import Repository
from caffeine_scout.models import SourceStatus
from caffeine_scout.presentation import (
    print_history,
    print_scan,
    print_sources,
    scan_csv,
    scan_json,
)
from caffeine_scout.service import build_sources
from caffeine_scout.service import scan as run_scan

app = typer.Typer(
    name="caffeine-scout",
    help="Find energy-drink deals before your wallet gets mugged.",
    no_args_is_help=True,
)
console = Console()

ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", help="Path to a YAML configuration file."),
]


@app.command()
def scan(
    config: ConfigOption = None,
    brand: Annotated[str | None, typer.Option("--brand")] = None,
    online_only: Annotated[bool, typer.Option("--online-only")] = False,
    pickup_only: Annotated[bool, typer.Option("--pickup-only")] = False,
    minimum_score: Annotated[int, typer.Option("--minimum-score", min=0, max=100)] = 0,
    format: Annotated[Literal["table", "json", "csv"], typer.Option("--format")] = "table",
) -> None:
    """Scan all enabled sources and store normalized price snapshots."""
    if online_only and pickup_only:
        raise typer.BadParameter("--online-only and --pickup-only cannot be combined")
    settings = load_config(config)
    repository = Repository(settings.database_url)
    result = asyncio.run(
        run_scan(
            settings,
            repository,
            brand=brand,
            online_only=online_only,
            pickup_only=pickup_only,
        )
    )
    result.offers = [
        offer for offer in result.offers if (offer.robbery_score or 0) >= minimum_score
    ]
    if format == "json":
        typer.echo(scan_json(result))
    elif format == "csv":
        typer.echo(scan_csv(result.offers), nl=False)
    else:
        print_scan(result, console)
        TerminalAlertSink(console).send(matching_alerts(result.offers, settings.alerts))


@app.command()
def history(
    config: ConfigOption = None,
    brand: Annotated[str | None, typer.Option("--brand")] = None,
) -> None:
    """Show latest, low, median, and prior-observation price history."""
    settings = load_config(config)
    rows = Repository(settings.database_url).history(
        brand=brand, window_days=settings.scoring.history_window_days
    )
    print_history(rows, console)


@app.command()
def sources(config: ConfigOption = None) -> None:
    """Show configuration and health status for source adapters."""
    settings = load_config(config)

    async def collect() -> list[SourceStatus]:
        enabled = build_sources(settings)
        statuses = list(await asyncio.gather(*(source.healthcheck() for source in enabled)))
        enabled_names = {source.name for source in enabled}
        for name in ("mock", "jsonld", "amazon"):
            if name not in enabled_names:
                statuses.append(
                    SourceStatus(name=name, enabled=False, healthy=False, detail="disabled")
                )
        pages_by_retailer: dict[str, list[bool]] = {}
        for page in settings.sources.jsonld.product_pages:
            pages_by_retailer.setdefault(page.retailer, []).append(page.enabled)
        for retailer, page_states in pages_by_retailer.items():
            active = sum(page_states)
            statuses.append(
                SourceStatus(
                    name=f"site:{retailer}",
                    enabled=bool(active),
                    healthy=bool(active),
                    detail=f"{active}/{len(page_states)} exact product page(s) enabled",
                )
            )
        for catalog in settings.sources.jsonld.catalog_pages:
            detail = f"discovery only: {catalog.note}"
            if catalog.local_store:
                detail += f"; local: {catalog.local_store}"
            statuses.append(
                SourceStatus(
                    name=f"catalog:{catalog.retailer}",
                    enabled=False,
                    healthy=False,
                    detail=detail,
                )
            )
        return statuses

    print_sources(asyncio.run(collect()), console)


@app.command("init-config")
def init_config(
    destination: Annotated[Path, typer.Option("--destination")] = Path("config.yaml"),
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Create an editable configuration file from the shipped example."""
    if destination.exists() and not force:
        raise typer.BadParameter(f"{destination} already exists; use --force to replace it")
    resource = files("caffeine_scout").joinpath("config.example.yaml")
    with as_file(resource) as example:
        shutil.copyfile(example, destination)
    console.print(f"Created [bold]{destination}[/bold]")


if __name__ == "__main__":
    app()
