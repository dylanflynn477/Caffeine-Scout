"""SQLAlchemy persistence for products, scans, snapshots, stores, and errors."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from caffeine_scout.models import HistoryRow, Offer, SourceError
from caffeine_scout.normalization import offer_fingerprint
from caffeine_scout.scoring import historical_median


class Base(DeclarativeBase):
    pass


class ProductRecord(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    canonical_brand: Mapped[str] = mapped_column(String(100), index=True)
    product_name: Mapped[str] = mapped_column(Text)
    product_line: Mapped[str | None] = mapped_column(String(100))
    flavor: Mapped[str | None] = mapped_column(String(150))
    pack_count: Mapped[int] = mapped_column(Integer)
    can_size_oz: Mapped[float | None] = mapped_column(Float)
    snapshots: Mapped[list[OfferSnapshotRecord]] = relationship(back_populates="product")


class RetailerRecord(Base):
    __tablename__ = "retailers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    snapshots: Mapped[list[OfferSnapshotRecord]] = relationship(back_populates="retailer")


class StoreRecord(Base):
    __tablename__ = "stores"
    __table_args__ = (UniqueConstraint("retailer_id", "name", "address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    retailer_id: Mapped[int] = mapped_column(ForeignKey("retailers.id"))
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(300), default="")


class ScanRunRecord(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    zip_code: Mapped[str] = mapped_column(String(10), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sources_attempted: Mapped[int] = mapped_column(Integer, default=0)
    successful_sources: Mapped[int] = mapped_column(Integer, default=0)
    offers_found: Mapped[int] = mapped_column(Integer, default=0)


class SourceErrorRecord(Base):
    __tablename__ = "source_errors"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    source: Mapped[str] = mapped_column(String(100))
    error_type: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OfferSnapshotRecord(Base):
    __tablename__ = "offer_snapshots"
    __table_args__ = (UniqueConstraint("scan_run_id", "snapshot_signature"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    retailer_id: Mapped[int] = mapped_column(ForeignKey("retailers.id"), index=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id"))
    source: Mapped[str] = mapped_column(String(100))
    source_product_id: Mapped[str | None] = mapped_column(String(200))
    listed_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    coupon_value: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    shipping_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    effective_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    price_per_can: Mapped[Decimal] = mapped_column(Numeric(12, 4), index=True)
    fulfillment_type: Mapped[str] = mapped_column(String(20))
    distance_miles: Mapped[float | None] = mapped_column(Float)
    in_stock: Mapped[bool | None] = mapped_column(Boolean)
    membership_required: Mapped[bool] = mapped_column(Boolean)
    subscription_required: Mapped[bool] = mapped_column(Boolean)
    robbery_score: Mapped[int | None] = mapped_column(Integer)
    robbery_label: Mapped[str | None] = mapped_column(String(50))
    url: Mapped[str] = mapped_column(Text)
    notes: Mapped[list[str]] = mapped_column(JSON)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    snapshot_signature: Mapped[str] = mapped_column(String(64))
    product: Mapped[ProductRecord] = relationship(back_populates="snapshots")
    retailer: Mapped[RetailerRecord] = relationship(back_populates="snapshots")


class Repository:
    def __init__(self, database_url: str) -> None:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(database_url, connect_args=connect_args)
        Base.metadata.create_all(self.engine)

    def start_scan(self, zip_code: str, attempted: int, started_at: datetime) -> int:
        with Session(self.engine) as session:
            run = ScanRunRecord(
                zip_code=zip_code,
                started_at=started_at,
                sources_attempted=attempted,
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            return run.id

    def finish_scan(
        self, scan_id: int, completed_at: datetime, successful: int, offers_found: int
    ) -> None:
        with Session(self.engine) as session:
            run = session.get(ScanRunRecord, scan_id)
            if run is None:
                raise LookupError(f"scan run {scan_id} does not exist")
            run.completed_at = completed_at
            run.successful_sources = successful
            run.offers_found = offers_found
            session.commit()

    def record_errors(self, scan_id: int, errors: list[SourceError]) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as session:
            session.add_all(
                SourceErrorRecord(
                    scan_run_id=scan_id,
                    source=error.source,
                    error_type=error.error_type,
                    message=error.message,
                    occurred_at=now,
                )
                for error in errors
            )
            session.commit()

    def historical_prices(self, fingerprint: str, window_days: int) -> list[Decimal]:
        cutoff = datetime.now(UTC) - timedelta(days=window_days)
        with Session(self.engine) as session:
            statement = (
                select(OfferSnapshotRecord.price_per_can)
                .join(ProductRecord)
                .where(
                    ProductRecord.fingerprint == fingerprint,
                    OfferSnapshotRecord.collected_at >= cutoff,
                )
                .order_by(OfferSnapshotRecord.collected_at)
            )
            return [Decimal(value) for value in session.scalars(statement)]

    def is_new_historical_low(self, fingerprint: str, price: Decimal) -> bool:
        previous = self.historical_prices(fingerprint, 36500)
        return bool(previous) and price < min(previous)

    def _product(self, session: Session, offer: Offer) -> ProductRecord:
        fingerprint = offer_fingerprint(offer)
        product = session.scalar(
            select(ProductRecord).where(ProductRecord.fingerprint == fingerprint)
        )
        if product is None:
            product = ProductRecord(
                fingerprint=fingerprint,
                canonical_brand=offer.canonical_brand,
                product_name=offer.product_name,
                product_line=offer.product_line,
                flavor=offer.flavor,
                pack_count=offer.pack_count,
                can_size_oz=offer.can_size_oz,
            )
            session.add(product)
            session.flush()
        return product

    def _retailer(self, session: Session, name: str) -> RetailerRecord:
        retailer = session.scalar(select(RetailerRecord).where(RetailerRecord.name == name))
        if retailer is None:
            retailer = RetailerRecord(name=name)
            session.add(retailer)
            session.flush()
        return retailer

    def _store(
        self, session: Session, retailer_id: int, name: str | None, address: str | None
    ) -> StoreRecord | None:
        if name is None:
            return None
        normalized_address = address or ""
        store = session.scalar(
            select(StoreRecord).where(
                StoreRecord.retailer_id == retailer_id,
                StoreRecord.name == name,
                StoreRecord.address == normalized_address,
            )
        )
        if store is None:
            store = StoreRecord(retailer_id=retailer_id, name=name, address=normalized_address)
            session.add(store)
            session.flush()
        return store

    @staticmethod
    def _signature(offer: Offer) -> str:
        data = offer.model_dump(
            mode="json", exclude={"collected_at", "robbery_score", "robbery_label"}
        )
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def record_offers(self, scan_id: int, offers: list[Offer]) -> int:
        inserted = 0
        with Session(self.engine) as session:
            existing = set(
                session.scalars(
                    select(OfferSnapshotRecord.snapshot_signature).where(
                        OfferSnapshotRecord.scan_run_id == scan_id
                    )
                )
            )
            for offer in offers:
                signature = self._signature(offer)
                if signature in existing:
                    continue
                product = self._product(session, offer)
                retailer = self._retailer(session, offer.retailer)
                store = self._store(session, retailer.id, offer.store_name, offer.store_address)
                session.add(
                    OfferSnapshotRecord(
                        scan_run_id=scan_id,
                        product_id=product.id,
                        retailer_id=retailer.id,
                        store_id=store.id if store else None,
                        source=offer.source,
                        source_product_id=offer.source_product_id,
                        listed_price=offer.listed_price,
                        coupon_value=offer.coupon_value,
                        shipping_cost=offer.shipping_cost,
                        effective_price=offer.effective_price,
                        price_per_can=offer.price_per_can,
                        fulfillment_type=offer.fulfillment_type,
                        distance_miles=offer.distance_miles,
                        in_stock=offer.in_stock,
                        membership_required=offer.membership_required,
                        subscription_required=offer.subscription_required,
                        robbery_score=offer.robbery_score,
                        robbery_label=offer.robbery_label,
                        url=offer.url,
                        notes=offer.notes,
                        collected_at=offer.collected_at,
                        snapshot_signature=signature,
                    )
                )
                existing.add(signature)
                inserted += 1
            session.commit()
        return inserted

    def history(self, brand: str | None = None, window_days: int = 30) -> list[HistoryRow]:
        cutoff = datetime.now(UTC) - timedelta(days=window_days)
        rows: list[HistoryRow] = []
        with Session(self.engine) as session:
            products = list(session.scalars(select(ProductRecord)))
            for product in products:
                if brand and product.canonical_brand.casefold() != brand.casefold():
                    continue
                snapshots = list(
                    session.scalars(
                        select(OfferSnapshotRecord)
                        .where(OfferSnapshotRecord.product_id == product.id)
                        .order_by(OfferSnapshotRecord.collected_at.desc())
                    )
                )
                if not snapshots:
                    continue
                latest = snapshots[0]
                recent_prices = [
                    Decimal(snapshot.price_per_can)
                    for snapshot in snapshots
                    if _aware(snapshot.collected_at) >= cutoff
                ]
                previous = snapshots[1] if len(snapshots) > 1 else None
                med = historical_median(recent_prices) or Decimal(latest.price_per_can)
                rows.append(
                    HistoryRow(
                        brand=product.canonical_brand,
                        product_name=product.product_name,
                        retailer=latest.retailer.name,
                        latest_price=Decimal(latest.price_per_can),
                        lowest_price=min(Decimal(item.price_per_can) for item in snapshots),
                        median_30d=med,
                        change_from_previous=(
                            Decimal(latest.price_per_can) - Decimal(previous.price_per_can)
                            if previous
                            else None
                        ),
                        last_seen=_aware(latest.collected_at),
                    )
                )
        return sorted(rows, key=lambda row: (row.brand, row.latest_price))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
