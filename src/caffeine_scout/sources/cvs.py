"""CVS public sport and energy-drink catalog discovery."""

from caffeine_scout.sources.discovery import CatalogDiscoverySource, CatalogSelectors


class CVSSource(CatalogDiscoverySource):
    name = "cvs"
    retailer = "CVS"
    selectors = CatalogSelectors(
        cards=("[data-testid='product-card']", "[data-test='product-card']", ".product-card"),
        names=("[data-testid='product-title']", ".product-title", ".product-name"),
        current_prices=(
            "[data-testid='sale-price']",
            "[data-testid='price']",
            ".sale-price",
            ".current-price",
        ),
        regular_prices=("[data-testid='regular-price']", ".regular-price", "s"),
        promotions=("[data-testid='promotion']", ".promotion", ".deal"),
        links=("a[data-testid='product-title']", "a.product-link", "a[href*='/shop/']"),
        availability=("[data-testid='availability']", ".availability"),
        fulfillment=("[data-testid='fulfillment']", ".fulfillment"),
        next_links=("a[rel='next']", "a[aria-label='Next']", "a[aria-label='next page']"),
    )
