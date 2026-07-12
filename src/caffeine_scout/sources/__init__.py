"""Retailer source adapters."""

from caffeine_scout.sources.amazon import AmazonSource
from caffeine_scout.sources.jsonld import JsonLdProductPageSource
from caffeine_scout.sources.mock import MockSource

__all__ = ["AmazonSource", "JsonLdProductPageSource", "MockSource"]
