"""P0 tables. Only tables that P0 actually fills live here; the full model is in docs/03-data-model.md."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

LEADERBOARD_WINDOWS = ("day", "week", "month", "alltime")
METRIC_FIELDS = ("pnl", "roi", "vlm")


class Base(DeclarativeBase):
    pass


def _num() -> Mapped[Decimal | None]:
    return mapped_column(Numeric, nullable=True)


class IngestRun(Base):
    __tablename__ = "ingest_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # leaderboard | enrich
    status: Mapped[str] = mapped_column(String(24))  # running | success | not_modified | failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_ingest_run_kind_started", "kind", "started_at"),)


class RawObject(Base):
    """Every raw upstream response we keep, so 'what did we know at time T' stays answerable."""

    __tablename__ = "raw_object"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(48), index=True)  # leaderboard | portfolio | clearinghouseState
    request: Mapped[dict] = mapped_column(JSONB, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    storage_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    ingest_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingest_run.id"))


class Wallet(Base):
    __tablename__ = "wallet"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    address: Mapped[str] = mapped_column(String(42), unique=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_seen_source: Mapped[str] = mapped_column(String(32))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    present_in_latest: Mapped[bool] = mapped_column(Boolean, default=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Denormalized latest leaderboard values (history lives in leaderboard_row + raw archive).
    account_value: Mapped[Decimal | None] = _num()
    day_pnl: Mapped[Decimal | None] = _num()
    day_roi: Mapped[Decimal | None] = _num()
    day_vlm: Mapped[Decimal | None] = _num()
    week_pnl: Mapped[Decimal | None] = _num()
    week_roi: Mapped[Decimal | None] = _num()
    week_vlm: Mapped[Decimal | None] = _num()
    month_pnl: Mapped[Decimal | None] = _num()
    month_roi: Mapped[Decimal | None] = _num()
    month_vlm: Mapped[Decimal | None] = _num()
    alltime_pnl: Mapped[Decimal | None] = _num()
    alltime_roi: Mapped[Decimal | None] = _num()
    alltime_vlm: Mapped[Decimal | None] = _num()
    quality_flags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    leaderboard_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tier: Mapped[str | None] = mapped_column(String(1), index=True)
    tier_rule_version: Mapped[str | None] = mapped_column(String(16))
    tier_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Flags derived from per-address API calls (not overwritten by leaderboard ingest).
    enrichment_flags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    portfolio_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    clearinghouse_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_wallet_quality_flags", "quality_flags", postgresql_using="gin"),
        Index("ix_wallet_first_seen", "first_seen_at"),
        Index("ix_wallet_month_pnl", "month_pnl"),
        Index("ix_wallet_account_value", "account_value"),
    )


class LeaderboardSnapshot(Base):
    __tablename__ = "leaderboard_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ingest_run_id: Mapped[int] = mapped_column(ForeignKey("ingest_run.id"))
    raw_object_id: Mapped[int] = mapped_column(ForeignKey("raw_object.id"))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_last_modified: Mapped[str | None] = mapped_column(Text)
    row_count: Mapped[int] = mapped_column(Integer)
    rows_persisted: Mapped[bool] = mapped_column(Boolean)


class LeaderboardRow(Base):
    """Point-in-time copy of one wallet's leaderboard values (wide: one row per wallet per snapshot)."""

    __tablename__ = "leaderboard_row"

    snapshot_id: Mapped[int] = mapped_column(ForeignKey("leaderboard_snapshot.id", ondelete="CASCADE"), primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    account_value: Mapped[Decimal | None] = _num()
    day_pnl: Mapped[Decimal | None] = _num()
    day_roi: Mapped[Decimal | None] = _num()
    day_vlm: Mapped[Decimal | None] = _num()
    week_pnl: Mapped[Decimal | None] = _num()
    week_roi: Mapped[Decimal | None] = _num()
    week_vlm: Mapped[Decimal | None] = _num()
    month_pnl: Mapped[Decimal | None] = _num()
    month_roi: Mapped[Decimal | None] = _num()
    month_vlm: Mapped[Decimal | None] = _num()
    alltime_pnl: Mapped[Decimal | None] = _num()
    alltime_roi: Mapped[Decimal | None] = _num()
    alltime_vlm: Mapped[Decimal | None] = _num()
    quality_flags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")

    __table_args__ = (Index("ix_leaderboard_row_wallet", "wallet_id", "snapshot_id"),)


class WalletTierHistory(Base):
    __tablename__ = "wallet_tier_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), index=True)
    tier: Mapped[str] = mapped_column(String(1))
    previous_tier: Mapped[str | None] = mapped_column(String(1))
    rule_version: Mapped[str] = mapped_column(String(16))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EquityPoint(Base):
    """Points of portfolio accountValueHistory/pnlHistory as observed at fetched_at (append-only)."""

    __tablename__ = "equity_point"

    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), primary_key=True)
    series: Mapped[str] = mapped_column(String(16), primary_key=True)  # day/week/month/allTime(/perp*)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    account_value: Mapped[Decimal] = mapped_column(Numeric)
    pnl: Mapped[Decimal] = mapped_column(Numeric)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_object_id: Mapped[int | None] = mapped_column(ForeignKey("raw_object.id"))


class WalletMetric(Base):
    """Computed, recomputable metrics. One row per wallet and metric window (latest computation)."""

    __tablename__ = "wallet_metric"

    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), primary_key=True)
    metric_window: Mapped[str] = mapped_column(String(8), primary_key=True)  # 30d / 90d / 180d / 365d / all
    metric_version: Mapped[str] = mapped_column(String(16))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_series: Mapped[str] = mapped_column(String(16))
    covered: Mapped[bool] = mapped_column(Boolean)  # does the series actually span the window?
    span_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    span_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    n_points: Mapped[int] = mapped_column(Integer)
    n_valid_periods: Mapped[int] = mapped_column(Integer)
    median_step_hours: Mapped[float | None] = mapped_column(Numeric)
    twr: Mapped[Decimal | None] = _num()
    mdd: Mapped[Decimal | None] = _num()  # max drawdown of the TWR index, as a negative fraction
    pnl: Mapped[Decimal | None] = _num()
    pnl_mdd_usd: Mapped[Decimal | None] = _num()  # max drawdown of the cumulative PnL curve (USD)
    net_flows: Mapped[Decimal | None] = _num()
    positive_period_ratio: Mapped[Decimal | None] = _num()
    reliability: Mapped[str] = mapped_column(String(8))  # high | medium | low
    notes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")

    __table_args__ = (Index("ix_wallet_metric_window_twr", "metric_window", "twr"),)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    account_value: Mapped[Decimal] = mapped_column(Numeric)
    total_ntl_pos: Mapped[Decimal] = mapped_column(Numeric)
    total_margin_used: Mapped[Decimal] = mapped_column(Numeric)
    withdrawable: Mapped[Decimal | None] = _num()
    raw_object_id: Mapped[int | None] = mapped_column(ForeignKey("raw_object.id"))

    __table_args__ = (
        UniqueConstraint("wallet_id", "fetched_at"),
        Index("ix_account_snapshot_wallet_time", "wallet_id", "fetched_at"),
    )


class PositionSnapshot(Base):
    __tablename__ = "position_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_snapshot_id: Mapped[int] = mapped_column(ForeignKey("account_snapshot.id", ondelete="CASCADE"), index=True)
    coin: Mapped[str] = mapped_column(String(32))
    szi: Mapped[Decimal] = mapped_column(Numeric)
    entry_px: Mapped[Decimal | None] = _num()
    position_value: Mapped[Decimal | None] = _num()
    unrealized_pnl: Mapped[Decimal | None] = _num()
    return_on_equity: Mapped[Decimal | None] = _num()
    leverage_type: Mapped[str | None] = mapped_column(String(16))
    leverage_value: Mapped[int | None] = mapped_column(Integer)
    liquidation_px: Mapped[Decimal | None] = _num()
    margin_used: Mapped[Decimal | None] = _num()
