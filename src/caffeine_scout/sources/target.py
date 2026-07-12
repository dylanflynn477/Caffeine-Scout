"""Target public energy-drink catalog discovery."""

from caffeine_scout.sources.discovery import CatalogDiscoverySource, CatalogSelectors


class TargetSource(CatalogDiscoverySource):
    name = "target"
    retailer = "Target"
    selectors = CatalogSelectors(
        cards=(
            "[data-test='@web/site-top-of-funnel/ProductCardWrapper']",
            "[data-test='product-card']",
            "[data-test='productCardVariantMini']",
            ".product-card",
        ),
        names=(
            "[data-test='product-title']",
            "[data-test='product-card-title']",
            "[data-test='productCardVariantMiniTitle']",
            ".product-name",
        ),
        current_prices=(
            "[data-test='current-price']",
            "[data-test='product-price']",
            "[data-test='@web/Price/PriceAndPromoMinimal']",
            ".current-price",
        ),
        regular_prices=("[data-test='comparison-price']", ".regular-price", "s"),
        promotions=("[data-test='offer-text']", "[data-test='promotion']", ".promotion"),
        links=(
            "a[data-test='product-title']",
            "a[data-test='product-card-title']",
            "[data-test='productCardVariantMiniTitle'] a",
            "a.product-link",
            "a[href*='/p/']",
        ),
        availability=("[data-test='availability']", ".availability"),
        fulfillment=("[data-test='fulfillment']", ".fulfillment"),
        next_links=("a[rel='next']", "a[aria-label='next page']", "a[data-test='next']"),
    )
