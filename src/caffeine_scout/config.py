"""YAML and environment-backed application configuration."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator


class LocationConfig(BaseModel):
    zip_code: str = Field(pattern=r"^\d{5}(?:-\d{4})?$")
    maximum_distance_miles: float = Field(gt=0)


class BrandConfig(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    target_price_per_can: Decimal = Field(gt=0)
    default_caffeine_mg: int | None = Field(default=None, gt=0)


class ProductFilterConfig(BaseModel):
    required_terms: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)


class BaseSourceConfig(BaseModel):
    enabled: bool = True


class JsonLdSourceConfig(BaseSourceConfig):
    product_urls: list[str] = Field(default_factory=list)
    request_delay_seconds: float = Field(default=0.5, ge=0)
    timeout_seconds: float = Field(default=15, gt=0)
    user_agent: str = "CaffeineScout/0.1 (+public-product-page JSON-LD reader)"
    use_playwright: bool = False


class AmazonSourceConfig(BaseSourceConfig):
    credential_id: str | None = None
    credential_secret: str | None = None
    partner_tag: str | None = None
    marketplace: str = "www.amazon.com"

    @property
    def has_credentials(self) -> bool:
        return all((self.credential_id, self.credential_secret, self.partner_tag))


class SourcesConfig(BaseModel):
    mock: BaseSourceConfig = Field(default_factory=BaseSourceConfig)
    jsonld: JsonLdSourceConfig = Field(default_factory=JsonLdSourceConfig)
    amazon: AmazonSourceConfig = Field(default_factory=lambda: AmazonSourceConfig(enabled=False))


class ScoringConfig(BaseModel):
    incredible_price_per_can: Decimal = Field(gt=0)
    ordinary_price_per_can: Decimal = Field(gt=0)
    history_window_days: int = Field(default=30, gt=0)
    minimum_history_samples: int = Field(default=3, gt=0)

    @model_validator(mode="after")
    def validate_price_range(self) -> ScoringConfig:
        if self.incredible_price_per_can >= self.ordinary_price_per_can:
            raise ValueError("incredible price must be lower than ordinary price")
        return self


class AlertsConfig(BaseModel):
    minimum_robbery_score: int = Field(ge=0, le=100)
    maximum_price_per_can: Decimal = Field(gt=0)
    notify_on_new_historical_low: bool = True


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location: LocationConfig
    brands: list[BrandConfig] = Field(min_length=1)
    product_filters: ProductFilterConfig
    sources: SourcesConfig
    scoring: ScoringConfig
    alerts: AlertsConfig
    database_url: str = "sqlite:///caffeine_scout.db"


def resolve_config_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    env_path = os.getenv("CAFFEINE_SCOUT_CONFIG")
    if env_path:
        return Path(env_path)
    local = Path("config.yaml")
    return local if local.exists() else Path("config.example.yaml")


def load_config(path: Path | None = None) -> AppConfig:
    load_dotenv()
    config_path = resolve_config_path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}. Run 'caffeine-scout init-config'."
        )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    amazon = raw.setdefault("sources", {}).setdefault("amazon", {})
    amazon.setdefault("credential_id", os.getenv("AMAZON_CREATORS_CREDENTIAL_ID"))
    amazon.setdefault("credential_secret", os.getenv("AMAZON_CREATORS_CREDENTIAL_SECRET"))
    amazon.setdefault("partner_tag", os.getenv("AMAZON_PARTNER_TAG"))
    return AppConfig.model_validate(raw)
