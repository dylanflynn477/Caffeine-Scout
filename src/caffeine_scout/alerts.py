"""Terminal alerts and future sink boundary."""

from rich.console import Console

from caffeine_scout.config import AlertsConfig
from caffeine_scout.models import AlertSink, Offer


def matching_alerts(offers: list[Offer], config: AlertsConfig) -> list[Offer]:
    return [
        offer
        for offer in offers
        if (offer.robbery_score or 0) >= config.minimum_robbery_score
        or offer.price_per_can <= config.maximum_price_per_can
        or (config.notify_on_new_historical_low and offer.is_new_historical_low)
    ]


class TerminalAlertSink(AlertSink):
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def send(self, offers: list[Offer]) -> None:
        if not offers:
            return
        self.console.print(f"\n[bold yellow]Deal alerts ({len(offers)})[/bold yellow]")
        for offer in offers:
            low = " - new historical low" if offer.is_new_historical_low else ""
            product = offer.flavor or offer.product_line or offer.product_name
            self.console.print(
                f"  * {offer.canonical_brand} {product} - "
                f"{offer.price_per_can:.2f}/can, score {offer.robbery_score}{low}"
            )
