"""Validated domain and source-boundary models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FulfillmentType = Literal["online", "pickup", "delivery", "unknown"]


class SearchRequest(BaseModel):
    zip_code: str = Field(pattern=r"^\d{5}(?:-\d{4})?$")
    maximum_distance_miles: float = Field(gt=0)
    brands: list[str]
    online_only: bool = False
    pickup_only: bool = False

    @model_validator(mode="after")
    def mutually_exclusive_fulfillment(self) -> SearchRequest:
        if self.online_only and self.pickup_only:
            raise ValueError("online_only and pickup_only cannot both be enabled")
        return self


class RawOffer(BaseModel):
    """Permissive adapter output; shared normalization enforces domain invariants."""

    model_config = ConfigDict(extra="forbid")

    source: str
    retailer: str
    product_name: str
    listed_price: Decimal
    url: str
    source_product_id: str | None = None
    canonical_brand: str | None = None
    product_line: str | None = None
    flavor: str | None = None
    pack_count: int | None = None
    can_size_oz: float | None = None
    caffeine_mg_per_can: int | None = None
    coupon_value: Decimal = Decimal("0")
    shipping_cost: Decimal = Decimal("0")
    advertised_discount_percent: float | None = None
    fulfillment_type: FulfillmentType = "unknown"
    store_name: str | None = None
    store_address: str | None = None
    distance_miles: float | None = None
    in_stock: bool | None = None
    membership_required: bool = False
    subscription_required: bool = False
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data_confidence: float = Field(default=0.8, ge=0, le=1)
    notes: list[str] = Field(default_factory=list)


class Offer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    retailer: str
    source_product_id: str | None
    product_name: str
    canonical_brand: str
    product_line: str | None
    flavor: str | None
    pack_count: int = Field(gt=0)
    can_size_oz: float | None = Field(default=None, gt=0)
    caffeine_mg_per_can: int | None = Field(default=None, gt=0)
    listed_price: Decimal = Field(gt=0)
    coupon_value: Decimal = Field(ge=0)
    shipping_cost: Decimal = Field(ge=0)
    effective_price: Decimal = Field(gt=0)
    price_per_can: Decimal = Field(gt=0)
    caffeine_mg_per_dollar: float | None = Field(default=None, ge=0)
    advertised_discount_percent: float | None = Field(default=None, ge=0, le=100)
    fulfillment_type: FulfillmentType
    store_name: str | None
    store_address: str | None
    distance_miles: float | None = Field(default=None, ge=0)
    in_stock: bool | None
    membership_required: bool
    subscription_required: bool
    url: str
    collected_at: datetime
    data_confidence: float = Field(ge=0, le=1)
    robbery_score: int | None = Field(default=None, ge=0, le=100)
    robbery_label: str | None = None
    notes: list[str] = Field(default_factory=list)
    is_new_historical_low: bool = False

    @field_validator("listed_price", "coupon_value", "shipping_cost", "effective_price")
    @classmethod
    def currency_has_finite_value(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("currency values must be finite")
        return value


class SourceStatus(BaseModel):
    name: str
    enabled: bool = True
    healthy: bool
    detail: str


class SourceError(BaseModel):
    source: str
    error_type: str
    message: str


class ScanResult(BaseModel):
    scan_id: int
    zip_code: str
    started_at: datetime
    completed_at: datetime
    sources_attempted: int
    successful_sources: int
    offers: list[Offer]
    errors: list[SourceError]
    quarantined_count: int = 0


class RetailerSource(ABC):
    name: str

    @abstractmethod
    async def search(self, request: SearchRequest) -> list[RawOffer]:
        """Return source-native offers at the validation boundary."""

    async def healthcheck(self) -> SourceStatus:
        return SourceStatus(name=self.name, healthy=True, detail="ready")


class AlertSink(ABC):
    @abstractmethod
    def send(self, offers: list[Offer]) -> None:
        """Deliver alerts."""


class HistoryRow(BaseModel):
    brand: str
    product_name: str
    retailer: str
    latest_price: Decimal
    lowest_price: Decimal
    median_30d: Decimal
    change_from_previous: Decimal | None
    last_seen: datetime
