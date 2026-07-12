"""Retailer source adapters."""

from caffeine_scout.sources.acme import AcmeSource
from caffeine_scout.sources.amazon import AmazonSource
from caffeine_scout.sources.cvs import CVSSource
from caffeine_scout.sources.jsonld import JsonLdProductPageSource
from caffeine_scout.sources.mock import MockSource
from caffeine_scout.sources.target import TargetSource

__all__ = [
    "AcmeSource",
    "AmazonSource",
    "CVSSource",
    "JsonLdProductPageSource",
    "MockSource",
    "TargetSource",
]
