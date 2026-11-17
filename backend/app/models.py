"""P0 tables. Only tables that P0 actually fills live here; the full model is in docs/03-data-model.md."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
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
    # Canonical trader key: a Hyperliquid address (0x...) or "okx:<authorId>" (see app/traders.py).
    address: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    venue: Mapped[str] = mapped_column(String(16), default="hyperliquid", server_default="hyperliquid", index=True)
    venue_uid: Mapped[str | None] = mapped_column(String(64))  # OKX authorId
    handle: Mapped[str | None] = mapped_column(String(64))  # OKX uniqueName (history / profile requests)
    avatar_url: Mapped[str | None] = mapped_column(Text)
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
        Index("uq_wallet_venue_uid", "venue", "venue_uid", unique=True, postgresql_where=text("venue_uid IS NOT NULL")),
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


# --- Market data & swing research (migration 0002) ---


class MarketCandle(Base):
    """Candles we record ourselves: the API only keeps the latest 5000 per interval."""

    __tablename__ = "market_candle"

    source: Mapped[str] = mapped_column(String(16), primary_key=True)  # hyperliquid | binance_um
    coin: Mapped[str] = mapped_column(String(32), primary_key=True)
    interval: Mapped[str] = mapped_column(String(8), primary_key=True)
    t: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    o: Mapped[Decimal] = mapped_column(Numeric)
    h: Mapped[Decimal] = mapped_column(Numeric)
    l: Mapped[Decimal] = mapped_column(Numeric)  # noqa: E741
    c: Mapped[Decimal] = mapped_column(Numeric)
    v: Mapped[Decimal] = mapped_column(Numeric)
    n: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SwingLeg(Base):
    """One leg between two zigzag pivots. Definitions are versioned; the last leg stays provisional
    (confirmed=false) until price reverses by the threshold. Retrospective by construction: used to
    evaluate past behaviour, never as a trading signal."""

    __tablename__ = "swing_leg"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    coin: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(16))
    level: Mapped[str] = mapped_column(String(8))  # major | minor
    direction: Mapped[str] = mapped_column(String(4))  # up | down
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    start_px: Mapped[Decimal] = mapped_column(Numeric)
    end_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_px: Mapped[Decimal] = mapped_column(Numeric)
    change: Mapped[Decimal] = mapped_column(Numeric)
    confirmed: Mapped[bool] = mapped_column(Boolean)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("coin", "version", "level", "start_ts"),)


class WalletLegReturn(Base):
    """Flow-adjusted return of a wallet over one swing leg, from portfolio allTime points (all assets)."""

    __tablename__ = "wallet_leg_return"

    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), primary_key=True)
    leg_id: Mapped[int] = mapped_column(ForeignKey("swing_leg.id", ondelete="CASCADE"), primary_key=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    anchor_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    anchor_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    n_points: Mapped[int] = mapped_column(Integer)
    twr: Mapped[Decimal | None] = _num()
    pnl: Mapped[Decimal | None] = _num()
    net_flows: Mapped[Decimal | None] = _num()
    bench_return: Mapped[Decimal | None] = _num()  # coin return over the same anchored window

    __table_args__ = (Index("ix_wallet_leg_return_leg", "leg_id"),)


class WalletSwingProfile(Base):
    __tablename__ = "wallet_swing_profile"

    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), primary_key=True)
    version: Mapped[str] = mapped_column(String(16), primary_key=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    legs_covered: Mapped[int] = mapped_column(Integer)
    legs_profitable: Mapped[int] = mapped_column(Integer)
    up_profitable: Mapped[int] = mapped_column(Integer)
    down_profitable: Mapped[int] = mapped_column(Integer)
    up_capture: Mapped[Decimal | None] = _num()
    down_capture: Mapped[Decimal | None] = _num()
    luck_p: Mapped[Decimal | None] = _num()  # P(>= this many profitable legs | peer base rates)
    current_cycle_both: Mapped[bool] = mapped_column(Boolean)  # profitable in the latest up AND latest down leg
    min_cycle_return: Mapped[Decimal | None] = _num()
    candidate: Mapped[bool] = mapped_column(Boolean, index=True)


class Fill(Base):
    __tablename__ = "fill"

    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), primary_key=True)
    tid: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    side: Mapped[str] = mapped_column(String(1), primary_key=True)  # B buy | A sell
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    coin: Mapped[str] = mapped_column(String(48))
    px: Mapped[Decimal] = mapped_column(Numeric)
    sz: Mapped[Decimal] = mapped_column(Numeric)
    dir: Mapped[str] = mapped_column(String(32))
    start_position: Mapped[Decimal | None] = _num()
    closed_pnl: Mapped[Decimal | None] = _num()
    fee: Mapped[Decimal | None] = _num()
    fee_token: Mapped[str | None] = mapped_column(String(16))
    oid: Mapped[int | None] = mapped_column(BigInteger)
    crossed: Mapped[bool | None] = mapped_column(Boolean)
    twap_id: Mapped[int | None] = mapped_column(BigInteger)
    hash: Mapped[str | None] = mapped_column(String(66))
    liquidation: Mapped[dict | None] = mapped_column(JSONB)
    raw_object_id: Mapped[int | None] = mapped_column(ForeignKey("raw_object.id"))

    __table_args__ = (Index("ix_fill_wallet_coin_time", "wallet_id", "coin", "time"),)


class FillSync(Base):
    """Queue + coverage record for fill archiving (the API only keeps ~the latest 10k fills per wallet)."""

    __tablename__ = "fill_sync"

    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), primary_key=True)
    reason: Mapped[str] = mapped_column(String(32))
    priority: Mapped[float] = mapped_column(Numeric)
    requested_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | synced | failed
    earliest_fill_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_fill_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    n_fills: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool | None] = mapped_column(Boolean)  # earliest available fill later than requested
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    reconstructed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recon_breaks: Mapped[int | None] = mapped_column(Integer)  # startPosition chain breaks (missing fills)


# --- Binance klines & position reconstruction (migration 0003) ---


class MarketFile(Base):
    """Ledger of imported bulk files, so imports are idempotent and auditable."""

    __tablename__ = "market_file"

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    coin: Mapped[str] = mapped_column(String(32))
    interval: Mapped[str] = mapped_column(String(8))
    period: Mapped[str] = mapped_column(String(10))  # 2026-08 (monthly) or 2026-11-15 (daily)
    sha256: Mapped[str] = mapped_column(String(64))
    rows: Mapped[int] = mapped_column(Integer)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MarketSymbol(Base):
    """Hyperliquid coin -> external market symbol, validated against Hyperliquid prices."""

    __tablename__ = "market_symbol"

    coin: Mapped[str] = mapped_column(String(48), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(32))
    multiplier: Mapped[Decimal] = mapped_column(Numeric)  # HL price = external price * multiplier
    status: Mapped[str] = mapped_column(String(16))  # valid | invalid | unavailable | unsupported
    median_ratio: Mapped[Decimal | None] = _num()
    basis_p95_bps: Mapped[Decimal | None] = _num()
    n_samples: Mapped[int] = mapped_column(Integer, default=0)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class PositionCycle(Base):
    """A position from flat (or first observed) to flat or a flip, rebuilt from fills."""

    __tablename__ = "position_cycle"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"))
    coin: Mapped[str] = mapped_column(String(48))
    version: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(5))  # long | short
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(8))  # closed | flipped | open
    complete: Mapped[bool] = mapped_column(Boolean)  # false: opened before our first fill, or a chain break inside
    scale: Mapped[str] = mapped_column(String(8))  # scalp | short | swing | macro (by holding time)
    holding_hours: Mapped[float] = mapped_column(Numeric)
    entry_px: Mapped[Decimal | None] = _num()  # size-weighted average of opening/adding fills
    exit_px: Mapped[Decimal | None] = _num()  # size-weighted average of reducing/closing fills
    max_size: Mapped[Decimal] = mapped_column(Numeric)
    max_notional: Mapped[Decimal] = mapped_column(Numeric)
    n_fills: Mapped[int] = mapped_column(Integer)
    n_decisions: Mapped[int] = mapped_column(Integer)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric)
    fees: Mapped[Decimal] = mapped_column(Numeric)
    # Per-decision legs of the cycle: [[iso time, vwap, size], ...] (size in coin units, positive)
    entries: Mapped[list | None] = mapped_column(JSONB)
    exits: Mapped[list | None] = mapped_column(JSONB)

    __table_args__ = (Index("ix_position_cycle_wallet", "wallet_id", "coin", "opened_at"),
                      Index("ix_position_cycle_scale", "scale", "closed_at"))


class TradeDecision(Base):
    """Consecutive same-side fills on one coin within a short gap, i.e. one trading decision."""

    __tablename__ = "trade_decision"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"))
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("position_cycle.id", ondelete="CASCADE"))
    coin: Mapped[str] = mapped_column(String(48))
    version: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(8))  # open | add | reduce | close | flip
    side: Mapped[str] = mapped_column(String(4))  # buy | sell
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    n_fills: Mapped[int] = mapped_column(Integer)
    size: Mapped[Decimal] = mapped_column(Numeric)
    notional: Mapped[Decimal] = mapped_column(Numeric)
    vwap: Mapped[Decimal] = mapped_column(Numeric)
    pos_before: Mapped[Decimal] = mapped_column(Numeric)
    pos_after: Mapped[Decimal] = mapped_column(Numeric)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric)
    fee: Mapped[Decimal] = mapped_column(Numeric)

    __table_args__ = (Index("ix_trade_decision_wallet", "wallet_id", "coin", "started_at"),
                      Index("ix_trade_decision_coin_time", "coin", "started_at"),
                      Index("ix_trade_decision_cycle", "cycle_id", "ended_at"))


# --- Timing scorecard T3 (migration 0004) ---


class TimingPivot(Base):
    """Swing pivots per coin and timeframe scale (zigzag on the scale's candle interval)."""

    __tablename__ = "timing_pivot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    coin: Mapped[str] = mapped_column(String(48))
    scale: Mapped[str] = mapped_column(String(8))
    version: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(1))  # H top | L bottom
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    px: Mapped[Decimal] = mapped_column(Numeric)
    leg_change: Mapped[Decimal] = mapped_column(Numeric)  # move of the leg that ends at this pivot
    leg_hours: Mapped[float] = mapped_column(Numeric)
    confirmed: Mapped[bool] = mapped_column(Boolean)

    __table_args__ = (UniqueConstraint("coin", "scale", "version", "ts", "kind"),
                      Index("ix_timing_pivot_lookup", "coin", "scale", "ts"))


class DecisionTiming(Base):
    """Location of one decision within its scale's price window (retrospective by construction)."""

    __tablename__ = "decision_timing"

    decision_id: Mapped[int] = mapped_column(ForeignKey("trade_decision.id", ondelete="CASCADE"), primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"))
    coin: Mapped[str] = mapped_column(String(48))
    scale: Mapped[str] = mapped_column(String(8))
    version: Mapped[str] = mapped_column(String(16))
    t: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    side: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(8))  # scored | pending (window not complete yet) | no_data
    location: Mapped[Decimal | None] = _num()  # 1 = bought the window low / sold the window high
    weight: Mapped[Decimal] = mapped_column(Numeric)
    pivot_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # matched pivot, if any

    __table_args__ = (Index("ix_decision_timing_wallet", "wallet_id", "scale", "t"),)


class TimingScore(Base):
    __tablename__ = "timing_score"

    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), primary_key=True)
    scale: Mapped[str] = mapped_column(String(8), primary_key=True)
    window: Mapped[str] = mapped_column("time_window", String(8), primary_key=True)
    coin_group: Mapped[str] = mapped_column(String(48), primary_key=True)  # all | btc_eth | c:<coin>
    version: Mapped[str] = mapped_column(String(16))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))  # scored | insufficient | invalid_window
    n_decisions: Mapped[int] = mapped_column(Integer)
    n_scored: Mapped[int] = mapped_column(Integer)
    location: Mapped[Decimal | None] = _num()
    null_mean: Mapped[Decimal | None] = _num()
    edge: Mapped[Decimal | None] = _num()
    edge_shrunk: Mapped[Decimal | None] = _num()
    p_value: Mapped[Decimal | None] = _num()
    q_value: Mapped[Decimal | None] = _num()
    pivot_hits: Mapped[int] = mapped_column(Integer)
    pivots: Mapped[int] = mapped_column(Integer)
    pivots_hit: Mapped[int] = mapped_column(Integer)
    precision: Mapped[Decimal | None] = _num()
    recall: Mapped[Decimal | None] = _num()
    alignment: Mapped[Decimal | None] = _num()
    cycles: Mapped[int] = mapped_column(Integer)
    net_pnl: Mapped[Decimal | None] = _num()
    # buy / sell split: mean location and share of decisions in the best 20% of the window
    n_buy: Mapped[int | None] = mapped_column(Integer)
    n_sell: Mapped[int | None] = mapped_column(Integer)
    buy_location: Mapped[Decimal | None] = _num()
    sell_location: Mapped[Decimal | None] = _num()
    buy_extreme: Mapped[Decimal | None] = _num()
    sell_extreme: Mapped[Decimal | None] = _num()
    null_extreme: Mapped[Decimal | None] = _num()  # same share under random time shifts (~0.2)
    maker_share: Mapped[Decimal | None] = _num()  # share of fills that rested on the book (not crossed)

    __table_args__ = (Index("ix_timing_score_rank", "scale", "time_window", "coin_group", "edge_shrunk"),)


class LegCapture(Base):
    """How much of a macro swing leg a wallet held and captured on that coin (version c1)."""

    __tablename__ = "leg_capture"

    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), primary_key=True)
    coin: Mapped[str] = mapped_column(String(48), primary_key=True)
    leg_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    leg_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    direction: Mapped[str] = mapped_column(String(4))  # up | down
    leg_change: Mapped[Decimal] = mapped_column(Numeric)
    confirmed: Mapped[bool] = mapped_column(Boolean)
    coverage: Mapped[str] = mapped_column(String(8))  # full | partial (fills start after the leg start)
    time_aligned: Mapped[Decimal] = mapped_column(Numeric)  # share of leg time positioned with the leg
    time_against: Mapped[Decimal] = mapped_column(Numeric)
    max_notional: Mapped[Decimal] = mapped_column(Numeric)
    avg_aligned_notional: Mapped[Decimal] = mapped_column(Numeric)
    pnl_mark: Mapped[Decimal] = mapped_column(Numeric)  # mark-to-market PnL on the coin during the leg
    capture_ratio: Mapped[Decimal | None] = _num()  # pnl / (max notional * |leg move|)
    version: Mapped[str] = mapped_column(String(16))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_leg_capture_rank", "coin", "leg_start", "capture_ratio"),)


# --- Watchlists S1 (migration 0006). Single owner today; the owner column keeps multi-user possible. ---


class WatchGroup(Base):
    __tablename__ = "watch_group"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner: Mapped[str] = mapped_column(String(32), default="default", server_default="default")
    name: Mapped[str] = mapped_column(String(64))
    color: Mapped[str | None] = mapped_column(String(16))
    sort: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("owner", "name"),)


class WatchItem(Base):
    __tablename__ = "watch_item"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("watch_group.id", ondelete="CASCADE"))
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), index=True)
    note: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("group_id", "wallet_id"),)


class WalletAlias(Base):
    """The owner's own name for a wallet; shown everywhere instead of the address."""

    __tablename__ = "wallet_alias"

    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), primary_key=True)
    owner: Mapped[str] = mapped_column(String(32), primary_key=True, default="default", server_default="default")
    alias: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Live monitoring & email alerts S2 (migration 0007) ---


class AppSetting(Base):
    """Owner settings (SMTP, live monitor). Secrets are Fernet-encrypted in `secret` and never returned."""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    secret: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertSubscription(Base):
    __tablename__ = "alert_subscription"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner: Mapped[str] = mapped_column(String(32), default="default", server_default="default")
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    events: Mapped[list[str]] = mapped_column(ARRAY(Text))  # open increase decrease close flip leverage liq_risk
    min_notional: Mapped[Decimal] = mapped_column(Numeric, default=0)
    min_change_pct: Mapped[Decimal] = mapped_column(Numeric, default=0)
    coins: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")  # empty = all
    recipients: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")  # empty = default
    quiet_start: Mapped[int | None] = mapped_column(Integer)  # local hour 0-23
    quiet_end: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("owner", "wallet_id"),)


class LiveAccount(Base):
    __tablename__ = "live_account"

    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), primary_key=True)
    account_value: Mapped[Decimal | None] = _num()
    total_ntl_pos: Mapped[Decimal | None] = _num()
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class LivePosition(Base):
    """Last seen position per monitored wallet and coin (the baseline the next poll is compared with)."""

    __tablename__ = "live_position"

    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), primary_key=True)
    coin: Mapped[str] = mapped_column(String(48), primary_key=True)
    szi: Mapped[Decimal] = mapped_column(Numeric)
    entry_px: Mapped[Decimal | None] = _num()
    position_value: Mapped[Decimal | None] = _num()
    unrealized_pnl: Mapped[Decimal | None] = _num()
    leverage: Mapped[int | None] = mapped_column(Integer)
    leverage_type: Mapped[str | None] = mapped_column(String(16))
    liquidation_px: Mapped[Decimal | None] = _num()
    liq_risk: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PositionEvent(Base):
    __tablename__ = "position_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"))
    coin: Mapped[str] = mapped_column(String(48))
    kind: Mapped[str] = mapped_column(String(12))  # open increase decrease close flip leverage liq_risk
    t: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    szi_before: Mapped[Decimal] = mapped_column(Numeric)
    szi_after: Mapped[Decimal] = mapped_column(Numeric)
    px: Mapped[Decimal | None] = _num()  # mid price when detected
    notional_change: Mapped[Decimal | None] = _num()
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notify_status: Mapped[str] = mapped_column(String(12), default="pending")  # pending sent skipped failed

    __table_args__ = (Index("ix_position_event_wallet_t", "wallet_id", "t"),
                      Index("ix_position_event_pending", "notify_status", "t"))


class Notification(Base):
    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    wallet_id: Mapped[int | None] = mapped_column(ForeignKey("wallet.id"))
    event_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), default=list)
    recipients: Mapped[list[str]] = mapped_column(ARRAY(Text))
    subject: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12))  # sent failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- Copy simulation S3 (migration 0008) ---


class CopySim(Base):
    """A forward-only simulated copy of one leader with its own capital, positions and ledger."""

    __tablename__ = "copy_sim"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner: Mapped[str] = mapped_column(String(32), default="default", server_default="default")
    name: Mapped[str] = mapped_column(String(64))
    leader_wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), index=True)
    status: Mapped[str] = mapped_column(String(12))  # running | paused | stopped
    stop_reason: Mapped[str | None] = mapped_column(String(32))  # manual | drawdown | liquidated
    capital: Mapped[Decimal] = mapped_column(Numeric)
    sizing: Mapped[str] = mapped_column(String(16))  # equity_ratio | fixed_multiple | fixed_cap
    size_factor: Mapped[Decimal] = mapped_column(Numeric)
    cap_per_coin: Mapped[Decimal | None] = _num()
    max_leverage: Mapped[Decimal] = mapped_column(Numeric)
    coins_include: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    coins_exclude: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    follow_adjust: Mapped[bool] = mapped_column(Boolean, default=True)  # also follow increases / decreases
    copy_existing: Mapped[bool] = mapped_column(Boolean, default=False)  # mirror positions open at start
    delay_seconds: Mapped[int] = mapped_column(Integer, default=0)
    fee_bps: Mapped[Decimal] = mapped_column(Numeric)
    slippage_bps: Mapped[Decimal] = mapped_column(Numeric)
    min_trade_usd: Mapped[Decimal] = mapped_column(Numeric)
    rebalance_pct: Mapped[Decimal] = mapped_column(Numeric)
    stop_drawdown_pct: Mapped[Decimal | None] = _num()
    cash: Mapped[Decimal] = mapped_column(Numeric)  # capital + realized - fees - funding
    peak_equity: Mapped[Decimal] = mapped_column(Numeric)
    state: Mapped[dict] = mapped_column(JSONB, default=dict)  # followed coins, baseline, pending delayed orders
    last_funding_hour: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_equity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SimPosition(Base):
    __tablename__ = "sim_position"

    sim_id: Mapped[int] = mapped_column(ForeignKey("copy_sim.id", ondelete="CASCADE"), primary_key=True)
    coin: Mapped[str] = mapped_column(String(48), primary_key=True)
    size: Mapped[Decimal] = mapped_column(Numeric)
    entry_px: Mapped[Decimal] = mapped_column(Numeric)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    mark_px: Mapped[Decimal | None] = _num()  # live mid at the last cycle


class SimTrade(Base):
    __tablename__ = "sim_trade"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sim_id: Mapped[int] = mapped_column(ForeignKey("copy_sim.id", ondelete="CASCADE"))
    t: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    coin: Mapped[str] = mapped_column(String(48))
    kind: Mapped[str] = mapped_column(String(12))  # trade | funding | liquidation | stop
    side: Mapped[str | None] = mapped_column(String(4))  # buy | sell
    size: Mapped[Decimal] = mapped_column(Numeric)
    mid: Mapped[Decimal | None] = _num()
    px: Mapped[Decimal | None] = _num()
    fee: Mapped[Decimal] = mapped_column(Numeric, default=0)
    slippage_cost: Mapped[Decimal] = mapped_column(Numeric, default=0)
    funding: Mapped[Decimal] = mapped_column(Numeric, default=0)  # paid (+) / received (-)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric, default=0)  # price PnL of the closed part
    position_after: Mapped[Decimal] = mapped_column(Numeric)
    leader_szi: Mapped[Decimal | None] = _num()
    reason: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (Index("ix_sim_trade_sim_t", "sim_id", "t"),)


class SimEquity(Base):
    __tablename__ = "sim_equity"

    sim_id: Mapped[int] = mapped_column(ForeignKey("copy_sim.id", ondelete="CASCADE"), primary_key=True)
    t: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    equity: Mapped[Decimal] = mapped_column(Numeric)
    cash: Mapped[Decimal] = mapped_column(Numeric)
    unrealized: Mapped[Decimal] = mapped_column(Numeric)
    gross_exposure: Mapped[Decimal] = mapped_column(Numeric)
    leader_equity: Mapped[Decimal | None] = _num()


# --- OKX source (migration 0009, docs/11) ---


class OkxInstrument(Base):
    """OKX perpetual contract specs: contracts -> coin units (linear) or USD (inverse)."""

    __tablename__ = "okx_instrument"

    inst_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    coin: Mapped[str] = mapped_column(String(32))
    ct_type: Mapped[str] = mapped_column(String(8))  # linear | inverse
    ct_val: Mapped[Decimal] = mapped_column(Numeric)
    ct_val_ccy: Mapped[str | None] = mapped_column(String(16))
    settle_ccy: Mapped[str | None] = mapped_column(String(16))
    max_lever: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str | None] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OkxPosition(Base):
    """One closed (or partly closed) position from position-history, as OKX reports it."""

    __tablename__ = "okx_position"

    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), primary_key=True)
    pos_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    inst_id: Mapped[str] = mapped_column(String(48))
    coin: Mapped[str] = mapped_column(String(32))
    ct_type: Mapped[str] = mapped_column(String(8))  # linear | inverse
    direction: Mapped[str] = mapped_column(String(5))  # long | short
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open_avg_px: Mapped[Decimal] = mapped_column(Numeric)
    close_avg_px: Mapped[Decimal | None] = _num()
    max_size: Mapped[Decimal] = mapped_column(Numeric)  # peak position, coin units
    closed_size: Mapped[Decimal] = mapped_column(Numeric)  # total closed over the position's life, coin units
    max_notional: Mapped[Decimal] = mapped_column(Numeric)  # USD at the open price
    pnl: Mapped[Decimal] = mapped_column(Numeric)  # USD, price PnL before fees and funding
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric)  # USD, after fees and funding
    fee: Mapped[Decimal] = mapped_column(Numeric)  # USD, positive = paid
    funding: Mapped[Decimal] = mapped_column(Numeric)  # USD, positive = paid
    pnl_ratio: Mapped[Decimal | None] = _num()
    lever: Mapped[Decimal | None] = _num()
    mgn_mode: Mapped[str | None] = mapped_column(String(16))
    close_type: Mapped[str] = mapped_column(String(24))  # allClose | partClose | liquidateClose | adl | ...
    liquidated: Mapped[bool] = mapped_column(Boolean)
    raw: Mapped[dict] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_okx_position_wallet_close", "wallet_id", "close_time"),
                      Index("ix_okx_position_coin_open", "coin", "open_time"))


class OkxTrader(Base):
    """Per-trader OKX state: history coverage and cursor, pool status, latest profile and assets."""

    __tablename__ = "okx_trader"

    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), primary_key=True)
    history_status: Mapped[str] = mapped_column(String(16))  # partial | target_complete | source_complete | error_backoff
    pool_status: Mapped[str] = mapped_column(String(16))  # active | watching | dropped | inactive | error_backoff
    priority: Mapped[int] = mapped_column(Integer, default=3)
    oldest_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    newest_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    n_positions: Mapped[int] = mapped_column(Integer, default=0)
    target_days: Mapped[int] = mapped_column(Integer, default=365)
    cursor_after: Mapped[str | None] = mapped_column(String(32))  # backfill resumes below this posId
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    history_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    profile_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    asset: Mapped[Decimal | None] = _num()
    asset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_pool_source: Mapped[str | None] = mapped_column(String(32))
    last_seen_official_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    derived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OkxLeaderboardRun(Base):
    """One crawl of an OKX leaderboard window; only complete runs are used for presence statistics."""

    __tablename__ = "okx_leaderboard_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_date: Mapped[date] = mapped_column(Date)
    time_window: Mapped[str] = mapped_column(String(8))  # 7d | 30d | 90d | 365d
    status: Mapped[str] = mapped_column(String(12))  # running | complete | failed
    data_version: Mapped[str | None] = mapped_column(String(32))
    expected_pages: Mapped[int | None] = mapped_column(Integer)
    expected_total: Mapped[int | None] = mapped_column(Integer)
    pages_done: Mapped[int] = mapped_column(Integer, default=0)
    unique_count: Mapped[int] = mapped_column(Integer, default=0)
    new_traders: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="crawl")  # crawl | sqlite_import

    __table_args__ = (Index("ix_okx_lb_run_date", "time_window", "business_date", "status"),)


class OkxLeaderboardRow(Base):
    __tablename__ = "okx_leaderboard_row"

    run_id: Mapped[int] = mapped_column(ForeignKey("okx_leaderboard_run.id", ondelete="CASCADE"), primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), primary_key=True, index=True)
    rank: Mapped[int] = mapped_column(Integer)
    asset: Mapped[Decimal | None] = _num()
    pnl: Mapped[Decimal | None] = _num()
    pnl_ratio: Mapped[Decimal | None] = _num()
    win_rate: Mapped[Decimal | None] = _num()
    max_drawdown: Mapped[Decimal | None] = _num()
    rates: Mapped[list | None] = mapped_column(JSONB)  # daily cumulative return series [[statTime, value], ...]


class OkxPresence(Base):
    """How often a trader shows up on a leaderboard window (complete daily runs only)."""

    __tablename__ = "okx_presence"

    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id"), primary_key=True)
    time_window: Mapped[str] = mapped_column(String(8), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    seen_days: Mapped[int] = mapped_column(Integer)
    streak: Mapped[int] = mapped_column(Integer)
    currently_seen: Mapped[bool] = mapped_column(Boolean)
    last_rank: Mapped[int | None] = mapped_column(Integer)
    best_rank: Mapped[int | None] = mapped_column(Integer)
