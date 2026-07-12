"""Experimental ACME Markets public catalog discovery."""

from caffeine_scout.sources.discovery import CatalogDiscoverySource, CatalogSelectors


class AcmeSource(CatalogDiscoverySource):
    name = "acme"
    retailer = "ACME Markets"
    experimental = True
    selectors = CatalogSelectors(
        cards=("[data-qa='product-card']", "[data-testid='product-card']", ".product-card"),
        names=("[data-qa='product-name']", "[data-testid='product-title']", ".product-title"),
        current_prices=("[data-qa='product-price']", "[data-testid='price']", ".current-price"),
        regular_prices=("[data-qa='regular-price']", ".regular-price", "s"),
        promotions=("[data-qa='promotion']", ".promotion"),
        links=("a[data-qa='product-name']", "a.product-link", "a[href*='/shop/product-details']"),
        availability=("[data-qa='availability']", ".availability"),
        fulfillment=("[data-qa='fulfillment']", ".fulfillment"),
        next_links=("a[rel='next']", "a[aria-label='Next']", "a[aria-label='next page']"),
    )
