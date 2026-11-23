"""Column builders (docs/11, docs/12). Each returns the issues to (re)build for this run, or none when there is nothing
worth an issue. Builders only read the feed and the newsroom's own tables; every number goes into a fact."""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from nr.columns.base import (ColumnIssue, chart, count_fact, day_key, hours_fact, month_key, pct_fact, short_name,
                             stale, text_fact, time_fact, week_key)
from nr.facts import fmt
from nr.facts.builder import D, Facts
from nr.feed.client import FeedClient

COINS = ("BTC", "ETH")


@dataclass
class Column:
    key: str
    zh: str
    en: str
    cadence: str  # weekly | monthly | daily | hourly | trigger
    build: Callable[[Session, FeedClient, datetime, dict], list[ColumnIssue]]
    phase: str
    venue: str  # hyperliquid | okx | multi: which source the gate checks and which accounts may carry it


def _usd(f: Facts, fid: str, v, source: str, kind: str = "notional", **x) -> None:
    if v is not None:
        f.usd(fid, kind, D(v), source, **x)


# ---- N1 board ROI vs copy returns -------------------------------------------------------------------------------

def copy_vs_board(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    window = params.get("window", "30d")
    # AlphaScope backtests a capped pool, so look down the board for traders that have one
    d = c.research("copy_board", "/copy/board", {"window": window, "limit": 100}, "rows", "copy_board.row")
    it = ColumnIssue("copy_vs_board", f"{week_key(now)}-{window}", title_zh=f"榜单收益 vs 跟单回测（{window}）",
                     title_en=f"Board ROI vs copy backtest ({window})", params={"window": window})
    f = it.facts
    f.constraints |= {"COPY_BACKTEST_ASSUMPTIONS", "OKX_LEAD_ASSETS", "SURVIVORSHIP", "NO_IDENTITY"}
    rows = [r for r in d["rows"] if r["copy_ret"] is not None and r["trades"]][:10]
    if (b := stale(d["run"]["completed_at"] if d["run"] else None, now, 72, "OKX board")):
        it.blocking.append(b)
    if len(rows) < 3:
        it.blocking.append({"code": "INSUFFICIENT_SAMPLE", "detail": f"{len(rows)} traders with a backtest"})
    text_fact(f, "window", window, "copy_board.window")
    if d["run"]:
        time_fact(f, "board.date", d["run"]["completed_at"], "okx_leaderboard_run.completed_at")
    chart_rows = []
    for i, r in enumerate(rows, 1):
        p = f"row.{i}"
        text_fact(f, f"{p}.trader", short_name(r["trader_key"], r["display_name"]), "copy_board.display_name",
                  label="self-chosen nickname on OKX")
        count_fact(f, f"{p}.rank", r["rank"], "okx_leaderboard_row.rank")
        pct_fact(f, f"{p}.board_roi", r["pnl_ratio"], "okx_leaderboard_row.pnl_ratio", label="official board ROI")
        pct_fact(f, f"{p}.copy_return", r["copy_ret"], "copy_backtest.ret", label="copy backtest return")
        if r["copy_mdd"] is not None:
            pct_fact(f, f"{p}.copy_drawdown", abs(D(r["copy_mdd"])), "copy_backtest.mdd")
        count_fact(f, f"{p}.copied_trades", r["trades"], "copy_backtest.trades")
        chart_rows.append({"label_fact": f"{p}.trader", "fact_ids": [f"{p}.board_roi", f"{p}.copy_return"]})
    gaps = [D(r["pnl_ratio"]) - D(r["copy_ret"]) for r in rows if r["pnl_ratio"] is not None]
    if gaps:
        pct_fact(f, "median_gap", sorted(gaps)[len(gaps) // 2], "derived: median(board ROI - copy return)")
    count_fact(f, "traders", len(rows), "copy_board.rows")
    count_fact(f, "board_size_checked", len(d["rows"]), "copy_board.rows",
               label="board places checked; only traders AlphaScope backtests are shown")
    chart(f, "榜单 ROI 与跟单回测", "Board ROI vs copy backtest", chart_rows)
    return [it]


# ---- N2 board dropouts ------------------------------------------------------------------------------------------

def board_dropouts(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    d = c.research("dropouts", "/okx/dropouts", {"days": 30, "max_best_rank": 30}, "rows", "dropouts.row")
    it = ColumnIssue("board_dropouts", week_key(now), title_zh="消失的榜单交易员", title_en="Who left the OKX board")
    f = it.facts
    f.constraints |= {"SURVIVORSHIP", "DROPOUT_REASON_UNKNOWN", "OKX_LEAD_ASSETS", "NO_IDENTITY"}
    rows = d["rows"][:10]
    if len(rows) < 3:
        it.blocking.append({"code": "INSUFFICIENT_SAMPLE", "detail": f"{len(rows)} dropouts"})
    chart_rows = []
    for i, r in enumerate(rows, 1):
        p = f"row.{i}"
        text_fact(f, f"{p}.trader", short_name(r["trader_key"], r["display_name"]), "okx_presence",
                  label="self-chosen nickname on OKX")
        count_fact(f, f"{p}.best_rank", r["best_rank"], "okx_presence.best_rank")
        count_fact(f, f"{p}.days_on_board", r["seen_days"], "okx_presence.seen_days")
        time_fact(f, f"{p}.last_seen", r["last_seen_at"], "okx_presence.last_seen_at")
        count_fact(f, f"{p}.closes_since", r["closes_since"], "okx_position (closed after last seen)")
        _usd(f, f"{p}.realized_since", r["realized_since"] or 0, "okx_position.realized_pnl (since last seen)",
             kind="realized_pnl")
        count_fact(f, f"{p}.liquidations_since", r["liquidations_since"], "okx_position.liquidated (since last seen)")
        chart_rows.append({"label_fact": f"{p}.trader", "fact_ids": [f"{p}.best_rank", f"{p}.realized_since"]})
    count_fact(f, "dropouts", len(rows), "okx_presence")
    chart(f, "离榜交易员之后的已实现盈亏", "Realized P&L after leaving the board", chart_rows)
    return [it]


# ---- N3 liquidation ladder --------------------------------------------------------------------------------------

def liq_ladder(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    out = []
    for coin in params.get("coins", COINS):
        d = c.research("structure", "/market/structure", {"coin": coin, "venue": "hyperliquid"})
        it = ColumnIssue("liq_ladder", f"{week_key(now)}-{coin}", coin=coin, title_zh=f"{coin} 强平价阶梯（Hyperliquid）",
                         title_en=f"{coin} liquidation ladder (Hyperliquid)")
        f = it.facts
        f.constraints |= {"OBSERVED_SAMPLE", "NOTIONAL_NOT_MARGIN"}
        if not d.get("ref"):
            it.blocking.append({"code": "INSUFFICIENT_SAMPLE", "detail": f"no fresh {coin} positions"})
            out.append(it)
            continue
        f.add("ref.mark_px", "price", D(d["ref"]["px"]), fmt.price(D(d["ref"]["px"])), "structure.ref.px")
        time_fact(f, "ref.observed_at", d["ref"]["observed_at"], "structure.ref.observed_at")
        count_fact(f, "sample.positions", d["positions"], "structure.positions")
        chart_rows = []
        for side, zh in (("long", "多"), ("short", "空")):
            liq = d["liquidations"][side]
            if liq.get("known_share") is not None:
                pct_fact(f, f"{side}.known_share", liq["known_share"], f"structure.liquidations.{side}.known_share",
                         label="share of notional with a venue-reported liquidation price")
            for step in liq.get("ladder") or []:
                move = D(step["move"])
                fid = f"{side}.move_{abs(int(move))}pct"
                _usd(f, f"{fid}.notional", step["notional"], f"structure.liquidations.{side}.ladder")
                count_fact(f, f"{fid}.positions", step["positions"], f"structure.liquidations.{side}.ladder")
                pct_fact(f, f"{fid}.move", move / 100, "structure ladder step")
                chart_rows.append({"label": {"zh": f"{zh} {move:+}%", "en": f"{side} {move:+}%"},
                                   "fact_ids": [f"{fid}.notional"]})
        cov = d.get("coverage") or {}
        if (b := stale(cov.get("latest"), now, 1, "radar")):
            it.blocking.append(b)
        chart(f, f"{coin} 价格变动触及的强平名义价值", f"{coin} notional with liquidation prices within each move",
              chart_rows)
        out.append(it)
    return out


# ---- N4 forecast ledger -----------------------------------------------------------------------------------------

def forecast_ledger(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    out = []
    for coin in params.get("coins", COINS):
        d = c.research("forecasts", "/research/forecasts", {"coin": coin, "days": 30}, "scores", "forecasts.score")
        it = ColumnIssue("forecast_ledger", f"{month_key(now)}-{coin}", coin=coin, title_zh=f"{coin} 预测公开账本",
                         title_en=f"{coin} forecast ledger")
        f = it.facts
        f.constraints |= {"FORECAST_SHADOW", "OBSERVED_SAMPLE"}
        settled = sum(r["settled"] for r in d["scores"])
        if settled < 20:
            it.blocking.append({"code": "INSUFFICIENT_SAMPLE", "detail": f"{settled} settled forecasts"})
        chart_rows = []
        for i, r in enumerate(d["scores"], 1):
            p = f"model.{i}"
            text_fact(f, f"{p}.name", f"{r['model']} · {r['horizon']}h", "research_forecast.model/horizon")
            count_fact(f, f"{p}.settled", r["settled"], "research_forecast (settled)")
            count_fact(f, f"{p}.missing", r["missing"], "research_forecast (missing)")
            count_fact(f, f"{p}.beat_baseline", r["beat_baseline"], "research_forecast (brier < baseline)")
            if r["settled"]:
                pct_fact(f, f"{p}.beat_rate", D(r["beat_baseline"]) / D(r["settled"]), "derived")
            if r["brier"] is not None:
                f.add(f"{p}.brier", "score", D(str(round(r["brier"], 4))),
                      {"zh": [f"{r['brier']:.4f}"], "en": [f"{r['brier']:.4f}"]}, "research_forecast.p_brier (mean)")
                f.add(f"{p}.baseline_brier", "score", D(str(round(r["base_brier"], 4))),
                      {"zh": [f"{r['base_brier']:.4f}"], "en": [f"{r['base_brier']:.4f}"]},
                      "research_forecast.baseline_brier (mean)")
            if r["skill_p"] is not None:
                f.add(f"{p}.skill_p", "score", D(str(round(r["skill_p"], 3))),
                      {"zh": [f"p={r['skill_p']:.3f}"], "en": [f"p={r['skill_p']:.3f}"]}, "block sign-flip test")
            chart_rows.append({"label_fact": f"{p}.name", "fact_ids": [f"{p}.settled", f"{p}.beat_baseline"]})
        count_fact(f, "settled", settled, "research_forecast")
        chart(f, f"{coin} 预测：已结算 / 胜过基准", f"{coin} forecasts: settled / beat baseline", chart_rows)
        out.append(it)
    return out


# ---- N5 funding vs specialists (trigger) ------------------------------------------------------------------------

def _pctile(v) -> Decimal | None:
    if v is None:
        return None
    x = D(v)
    return x / 100 if x > 1 else x


def funding_divergence(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    out = []
    for coin in params.get("coins", COINS):
        d = c.research("funding", "/research/funding", {"coin": coin, "hours": 6}, "frames", "funding.frame")
        frames = [fr for fr in d["frames"] if fr["divergence"] is not None and (fr["smart_n"] or 0) >= 5
                  and fr["venue"] == "hyperliquid"]
        pc = _pctile(d["funding"]["percentile"])
        if not frames or pc is None:
            continue
        latest = frames[0]
        div = D(latest["divergence"])
        crowded_long, crowded_short = pc >= Decimal("0.9"), pc <= Decimal("0.1")
        # specialists leaning against a crowded funding side
        if not ((crowded_long and div <= Decimal("-0.2")) or (crowded_short and div >= Decimal("0.2")) or params.get("force")):
            continue
        it = ColumnIssue("funding_divergence", f"{day_key(now)}-{coin}", coin=coin,
                         title_zh=f"{coin} 资金费率与专家仓位背离", title_en=f"{coin}: funding vs specialist positioning")
        f = it.facts
        f.constraints |= {"SCREEN_NOT_SIGNAL", "MATCHED_CONTROL", "OBSERVED_SAMPLE"}
        if d["funding"]["rate"] is not None:
            rate = D(d["funding"]["rate"])
            shown = f"{rate * 100:.4f}%"  # funding rates are tiny: two decimals would read as 0%
            f.add("funding.rate", "percent", rate, {"zh": [shown], "en": [shown]}, "exchange_observation.funding.rate",
                  label="Binance funding rate per settlement")
        pct_fact(f, "funding.percentile", pc, "funding history rank (prior 12 months)")
        time_fact(f, "funding.at", d["funding"]["source_at"], "exchange_observation.funding.source_at")
        text_fact(f, "frame.venue", latest["venue"], "research_frame.venue")
        pct_fact(f, "specialists.long_share", latest["smart_long"], "research_frame.metrics.smart.long_share")
        pct_fact(f, "control.long_share", latest["control_long"], "research_frame.metrics.control.long_share")
        count_fact(f, "specialists.n", latest["smart_n"], "research_frame.metrics.smart.n")
        count_fact(f, "control.n", latest["control_n"], "research_frame.metrics.control.n")
        pct_fact(f, "divergence", abs(div), "research_frame.metrics.divergence (specialists - control)")
        time_fact(f, "frame.at", latest["bucket_ts"], "research_frame.bucket_ts")
        if (b := stale(latest["bucket_ts"], now, 3, "research frame")):
            it.blocking.append(b)
        chart(f, "专家组 vs 对照组 多头占比", "Specialists vs control: share long",
              [{"label": {"zh": "专家组", "en": "Specialists"}, "fact_ids": ["specialists.long_share"]},
               {"label": {"zh": "对照组", "en": "Control"}, "fact_ids": ["control.long_share"]}])
        out.append(it)
    return out


# ---- N6 holding profile -----------------------------------------------------------------------------------------

def holding_profile(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    d = c.research("holding", "/cycles/holding", {"days": 90, "venue": "hyperliquid"}, "rows", "holding.row")
    it = ColumnIssue("holding_profile", month_key(now), title_zh="谁拿得住：盈利单与亏损单的持仓时长",
                     title_en="Who holds: time in winners vs losers")
    f = it.facts
    f.constraints |= {"COMPLETE_CYCLES_ONLY", "OBSERVED_SAMPLE"}
    total = sum(int(r["n"]) for r in d["rows"])
    if total < 200:
        it.blocking.append({"code": "INSUFFICIENT_SAMPLE", "detail": f"{total} complete cycles"})
    count_fact(f, "cycles", total, "position_cycle (complete, closed, 90 days)")
    count_fact(f, "days", d["days"], "window")
    chart_rows = []
    names = {"scalp": ("超短线", "scalp"), "short": ("短线", "short"), "swing": ("波段", "swing"),
             "macro": ("大周期", "macro")}
    for r in d["rows"]:
        k = f"{r['scale']}.{'winners' if r['winner'] else 'losers'}"
        count_fact(f, f"{k}.n", r["n"], "position_cycle")
        hours_fact(f, f"{k}.median_hours", r["median"], "position_cycle.holding_hours (median)")
        hours_fact(f, f"{k}.p75_hours", r["p75"], "position_cycle.holding_hours (p75)")
        zh, en = names[r["scale"]]
        chart_rows.append({"label": {"zh": f"{zh}·{'盈' if r['winner'] else '亏'}",
                                     "en": f"{en} {'win' if r['winner'] else 'loss'}"},
                           "fact_ids": [f"{k}.median_hours"]})
    chart(f, "持仓时长中位数（小时）", "Median holding time (hours)", chart_rows)
    return [it]


# ---- N7 style changes (newsroom's own snapshots) ----------------------------------------------------------------

def style_changes(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    rows = s.execute(text("""
        WITH snaps AS (
            SELECT t.trader_key, t.display_name, cs.fetched_at,
                   (SELECT coalesce(array_agg(x->>'style' ORDER BY x->>'style'), '{}') FROM
                      jsonb_array_elements(cs.payload->'quality'->'styles') x WHERE x->>'status' = 'verified') AS verified,
                   row_number() OVER (PARTITION BY cs.trader_id ORDER BY cs.fetched_at) AS first_rn,
                   row_number() OVER (PARTITION BY cs.trader_id ORDER BY cs.fetched_at DESC) AS last_rn
            FROM trader_context_snapshot cs JOIN trader t ON t.id = cs.trader_id WHERE cs.fetched_at > :since)
        SELECT a.trader_key, a.display_name, a.fetched_at AS first_at, a.verified AS first_verified,
               b.fetched_at AS last_at, b.verified AS last_verified
        FROM snaps a JOIN snaps b ON a.trader_key = b.trader_key AND a.first_rn = 1 AND b.last_rn = 1
        WHERE a.verified <> b.verified AND b.fetched_at - a.fetched_at > interval '14 days'
        ORDER BY b.fetched_at DESC LIMIT 10"""), {"since": now - timedelta(days=120)}).mappings().all()
    if not rows:
        return []
    it = ColumnIssue("style_changes", month_key(now), title_zh="交易员风格变迁", title_en="Traders whose style changed")
    f = it.facts
    f.constraints |= {"STYLE_RULE_VERSION", "NO_IDENTITY", "OBSERVED_SAMPLE"}
    for i, r in enumerate(rows, 1):
        p = f"row.{i}"
        text_fact(f, f"{p}.trader", short_name(r["trader_key"], r["display_name"]), "trader")
        text_fact(f, f"{p}.before", ", ".join(r["first_verified"]) or "none", "trader_context_snapshot (first)")
        text_fact(f, f"{p}.after", ", ".join(r["last_verified"]) or "none", "trader_context_snapshot (latest)")
        time_fact(f, f"{p}.from", r["first_at"], "trader_context_snapshot.fetched_at")
        time_fact(f, f"{p}.to", r["last_at"], "trader_context_snapshot.fetched_at")
    count_fact(f, "traders", len(rows), "trader_context_snapshot")
    return [it]


# ---- P2 weekly scorecard (newsroom's own publications) ----------------------------------------------------------

def weekly_scorecard(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    rows = s.execute(text("""
        SELECT DISTINCT ON (e.id) e.id, e.event_class, e.coin, e.direction, pr.published_at, acc.handle,
               fp.content AS packet, ep.status AS ep_status, ep.last_mark_px, ep.end_to, ep.last_szi,
               t.trader_key, t.display_name
        FROM publication_record pr JOIN article a ON a.id = pr.article_id JOIN news_event e ON e.id = a.news_event_id
        JOIN editorial_account acc ON acc.id = pr.account_id
        JOIN article_version v ON v.id = pr.article_version_id JOIN fact_packet fp ON fp.id = v.fact_packet_id
        LEFT JOIN position_episode ep ON ep.id = e.episode_id LEFT JOIN trader t ON t.id = e.trader_id
        WHERE pr.status = 'published' AND pr.published_at > :since AND e.column_key IS NULL
        ORDER BY e.id, pr.published_at"""), {"since": now - timedelta(days=7)}).mappings().all()
    it = ColumnIssue("weekly_scorecard", week_key(now), title_zh="一周巨鲸成绩单", title_en="Weekly whale scorecard")
    f = it.facts
    f.constraints |= {"PUBLISHED_OUTCOMES", "OBSERVED_SAMPLE", "NO_IDENTITY", "PNL_ESTIMATED_FROM_MARK"}
    if not rows:
        it.blocking.append({"code": "INSUFFICIENT_SAMPLE", "detail": "nothing was published this week"})
    for i, r in enumerate(rows[:12], 1):
        p = f"item.{i}"
        facts = {x["id"]: x for x in r["packet"]["facts"]}
        text_fact(f, f"{p}.trader", short_name(r["trader_key"], r["display_name"]) if r["trader_key"] else "—", "trader")
        text_fact(f, f"{p}.what", f"{r['event_class']} {r['coin']} {r['direction'] or ''}".strip(), "news_event")
        time_fact(f, f"{p}.published_at", r["published_at"], "publication_record.published_at")
        mark_then = facts.get("position.mark_px") or facts.get("change.mark_px")
        if mark_then:
            f.add(f"{p}.mark_then", "price", D(mark_then["value"]), fmt.price(D(mark_then["value"])),
                  "fact packet at publication")
        if r["last_mark_px"] is not None:
            f.add(f"{p}.mark_now", "price", D(r["last_mark_px"]), fmt.price(D(r["last_mark_px"])),
                  "position_episode.last_mark_px (latest observation)")
            if mark_then and D(mark_then["value"]):
                pct_fact(f, f"{p}.price_change", (D(r["last_mark_px"]) - D(mark_then["value"])) / D(mark_then["value"]),
                         "derived: mark now vs at publication")
        text_fact(f, f"{p}.position_now", {"open": "still open", "closed": "closed", "flipped": "flipped"}.get(
            r["ep_status"], "unknown"), "position_episode.status")
        if r["end_to"]:
            time_fact(f, f"{p}.closed_by", r["end_to"], "position_episode.end_to", label="closed before this time")
    count_fact(f, "items", min(len(rows), 12), "publication_record")
    return [it]


# ---- P3 long/short duel ------------------------------------------------------------------------------------------

GROUPS = {("hyperliquid", "all"): ("Hyperliquid 观测钱包", "Hyperliquid wallets"),
          ("okx", "all"): ("OKX 带单员", "OKX lead traders"),
          ("all", "timing"): ("择时显著交易员", "Timing-significant traders"),
          ("okx", "okx_board"): ("OKX 7 日榜上带单员", "OKX 7d board traders")}
MIN_GROUP = 10  # fewer traders than this is an anecdote, not a group


def long_short_duel(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    out = []
    for coin in params.get("coins", COINS):
        d = c.research("groups", "/market/groups", {"coin": coin}, "rows", "groups.row")
        it = ColumnIssue("long_short_duel", f"{week_key(now)}-{coin}", coin=coin, title_zh=f"{coin} 多空对决",
                         title_en=f"{coin} long/short duel")
        f = it.facts
        f.constraints |= {"OBSERVED_SAMPLE", "GROUPS_OVERLAP"}
        latest = d["latest"]
        now_rows = {(r["venue"], r["cohort"]): r for r in d["rows"] if r["bucket_ts"] == latest}
        old_rows = {(r["venue"], r["cohort"]): r for r in d["rows"] if r["bucket_ts"] != latest}
        chart_rows, used = [], 0
        for key, (zh, en) in GROUPS.items():
            r = now_rows.get(key)
            n = (r["long_traders"] + r["short_traders"]) if r else 0
            if n < MIN_GROUP:
                continue
            used += 1
            g = f"{key[0]}_{key[1]}"
            f.add(f"{g}.name", "text", zh, {"zh": [zh], "en": [en]}, "market_snapshot venue/cohort")
            count_fact(f, f"{g}.traders", n, "market_snapshot long_traders + short_traders")
            pct_fact(f, f"{g}.long_share", D(r["long_traders"]) / D(n), "market_snapshot (traders net long / all)")
            gross = D(r["long_ntl"]) + D(r["short_ntl"])
            if gross:
                pct_fact(f, f"{g}.long_notional_share", D(r["long_ntl"]) / gross, "market_snapshot long_ntl / gross")
            o = old_rows.get(key)
            on = (o["long_traders"] + o["short_traders"]) if o else 0
            if on >= MIN_GROUP:
                pct_fact(f, f"{g}.long_share_week_ago", D(o["long_traders"]) / D(on), "market_snapshot 7 days earlier")
            chart_rows.append({"label_fact": f"{g}.name", "fact_ids": [f"{g}.long_share", f"{g}.long_notional_share"]})
        if latest:
            time_fact(f, "snapshot.at", latest, "market_snapshot.bucket_ts")
            if (b := stale(latest, now, 3, "hourly snapshot")):
                it.blocking.append(b)
        if used < 2:
            it.blocking.append({"code": "INSUFFICIENT_SAMPLE", "detail": f"{used} groups with at least {MIN_GROUP} traders"})
        count_fact(f, "groups", used, "market_snapshot")
        chart(f, f"{coin} 各群体多头占比（人数 / 名义价值）", f"{coin} share long by group (traders / notional)", chart_rows)
        out.append(it)
    return out


# ---- P3 streak breaker (trigger) ---------------------------------------------------------------------------------

def streak_breaker(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    d = c.research("closed", "/cycles/closed", {"hours": 48, "min_streak": params.get("min_streak", 4),
                                                 "venue": "hyperliquid"}, "rows", "closed.row")
    out = []
    for r in d["rows"]:
        net = D(r["realized_pnl"]) - D(r["fees"])
        if net >= 0 or not r["prior_known"] or r["coverage_status"] != "synced" or r["truncated"]:
            continue
        key = f"{r['trader_key']}|{r['coin']}|{r['direction']}|{r['opened_at']}|{r['version']}"
        it = ColumnIssue("streak_breaker", hashlib.sha256(key.encode()).hexdigest()[:24], coin=r["coin"],
                         trader_key=r["trader_key"], title_zh="连胜终结", title_en="A winning streak ends")
        f = it.facts
        f.constraints |= {"COMPLETE_CYCLES_ONLY", "NO_IDENTITY"}
        text_fact(f, "trader", short_name(r["trader_key"], r["display_name"]), "wallet")
        count_fact(f, "streak.before", r["prior_streak"], "complete closed cycles before this one, net of fees",
                   label="consecutive winning complete cycles; a losing cycle precedes them")
        text_fact(f, "cycle.direction", r["direction"], "position_cycle.direction")
        time_fact(f, "cycle.opened_at", r["opened_at"], "position_cycle.opened_at (from fills)")
        time_fact(f, "cycle.closed_at", r["closed_at"], "position_cycle.closed_at (from fills)")
        hours_fact(f, "cycle.holding_hours", r["holding_hours"], "position_cycle.holding_hours")
        _usd(f, "cycle.max_notional", r["max_notional"], "position_cycle.max_notional")
        _usd(f, "cycle.net_realized_usd", net, "position_cycle.realized_pnl - fees", kind="realized_pnl")
        if r["entry_px"] is not None:
            f.add("cycle.entry_px", "price", D(r["entry_px"]), fmt.price(D(r["entry_px"])), "position_cycle.entry_px")
        if r["exit_px"] is not None:
            f.add("cycle.exit_px", "price", D(r["exit_px"]), fmt.price(D(r["exit_px"])), "position_cycle.exit_px")
        out.append(it)
    return out


# ---- P3 trader dossier (on demand) -------------------------------------------------------------------------------

def dossier(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    key = params["trader_key"]
    ctx = c.context(key)
    it = ColumnIssue("dossier", f"{day_key(now)}-{key[-10:]}", trader_key=key, title_zh="交易员真实性调查",
                     title_en="Is this trader really that good?", params={"trader_key": key})
    f = it.facts
    f.constraints |= {"NO_IDENTITY", "SURVIVORSHIP", "COMPLETE_CYCLES_ONLY"}
    text_fact(f, "trader", short_name(key, ctx["trader"]["display_name"]), "wallet")
    cov = ctx["fill_coverage"]
    if not (cov and cov["status"] == "synced" and not cov["truncated"]):
        it.blocking.append({"code": "INSUFFICIENT_SAMPLE", "detail": "trading history archive is incomplete"})
    else:
        time_fact(f, "history.since", cov["earliest_at"], "fill coverage")
    done = [x for x in ctx["cycles"] if x["complete"] and x["status"] != "open"]
    if done:
        nets = [D(x["realized_pnl"]) - D(x["fees"]) for x in done]
        count_fact(f, "cycles", len(done), "context.cycles (complete)")
        count_fact(f, "wins", sum(n > 0 for n in nets), "context.cycles")
        pct_fact(f, "win_rate", D(sum(n > 0 for n in nets)) / D(len(nets)), "derived")
        _usd(f, "net_realized", sum(nets, Decimal(0)), "sum of net realized P&L", kind="realized_pnl")
        _usd(f, "worst_cycle", min(nets), "worst complete cycle, net", kind="realized_pnl")
        _usd(f, "best_cycle", max(nets), "best complete cycle, net", kind="realized_pnl")
        if sum(nets) > 0:
            pct_fact(f, "best_cycle_share", max(nets) / sum(nets), "best cycle / total net",
                     label="how much of the total one trade explains")
    for m in ctx["metrics"]:
        if m["window"] == "90d" and m["twr"] is not None and m["reliability"] in ("high", "medium"):
            pct_fact(f, "twr_90d", m["twr"], "wallet_metric.twr", coverage={"reliability": m["reliability"]})
            if m["mdd"] is not None:
                pct_fact(f, "max_drawdown_90d", abs(D(m["mdd"])), "wallet_metric.mdd")
    q = ctx["quality"]
    text_fact(f, "styles.verified", ", ".join(x["style"] for x in q["styles"] if x["status"] == "verified") or "none",
              "style_tag (verified = held out of sample)")
    sig = [t for t in q["timing"] if t["status"] == "scored" and t["q_value"] is not None and D(t["q_value"]) <= Decimal("0.1")]
    text_fact(f, "timing.significant", ", ".join(f"{t['scale']}/{t['window']}" for t in sig) or "none",
              "timing_score (BH q <= 0.1)")
    if q["copy"]:
        text_fact(f, "copy.grade", q["copy"]["grade"] or q["copy"]["status"], "copy_score")
    return [it]


# ---- P3 story recap (trigger on a covered position closing) -------------------------------------------------------

def recap(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    ep = s.execute(text("""SELECT ep.*, t.trader_key, t.display_name FROM position_episode ep
                           JOIN trader t ON t.id = ep.trader_id WHERE ep.id = :e"""),
                   {"e": params["episode_id"]}).mappings().first()
    if ep is None or ep["status"] == "open":
        return []
    chapters = s.execute(text("""
        SELECT e.id, e.event_class, se.seq, se.role, fp.content, fp.version,
               (SELECT min(pr.published_at) FROM publication_record pr JOIN article a ON a.id = pr.article_id
                WHERE a.news_event_id = e.id AND pr.status = 'published') AS published_at
        FROM news_event e JOIN story_event se ON se.news_event_id = e.id
        LEFT JOIN fact_packet fp ON fp.id = coalesce(se.pinned_fact_packet_id, e.current_fact_packet_id)
        WHERE e.episode_id = :e ORDER BY se.seq"""), {"e": ep["id"]}).mappings().all()
    if not any(ch["published_at"] for ch in chapters):
        return []
    it = ColumnIssue("recap", f"episode-{ep['id']}", coin=ep["coin"], trader_key=ep["trader_key"],
                     title_zh="一段仓位的完整复盘", title_en="The whole story of one position",
                     params={"episode_id": ep["id"]})
    f = it.facts
    f.constraints |= {"NO_IDENTITY", "OBSERVED_SAMPLE", "HINDSIGHT", "NOTIONAL_NOT_MARGIN"}
    if ep["start_kind"] in ("first_seen", "reconstructed"):
        f.constraints.add("FIRST_SEEN_NOT_OPEN")
    else:
        f.constraints.add("TIME_WINDOW_ONLY")
    text_fact(f, "trader", short_name(ep["trader_key"], ep["display_name"]), "trader")
    text_fact(f, "direction", ep["direction"], "position_episode.direction")
    time_fact(f, "start", ep["start_to"], "position_episode.start_to",
              label="first observed" if ep["start_kind"] in ("first_seen", "reconstructed") else "opened by")
    time_fact(f, "end", ep["end_to"], "position_episode.end_to", label="closed by")
    _usd(f, "peak_notional", ep["peak_notional"], "position_episode.peak_notional")
    count_fact(f, "increases", ep["n_increases"], "position_episode.n_increases")
    count_fact(f, "decreases", ep["n_decreases"], "position_episode.n_decreases")
    text_fact(f, "outcome", ep["status"], "position_episode.status")
    for ch in chapters:
        p = f"chapter.{ch['seq']}"
        text_fact(f, f"{p}.what", ch["event_class"], "news_event.event_class")
        if ch["published_at"]:
            time_fact(f, f"{p}.published_at", ch["published_at"], "publication_record")
        facts = {x["id"]: x for x in (ch["content"] or {}).get("facts", [])}
        for fid in ("position.notional_usd", "change.notional_usd", "position.unrealized_pnl_usd",
                    "close.pnl_estimate_usd", "liquidation.closed_pnl_usd"):
            if fid in facts:
                x = facts[fid]
                f.add(f"{p}.{fid}", x["kind"], D(x["value"]), x["display"], f"fact packet v{ch['version']} of event {ch['id']}")
                if x["kind"] == "unrealized_pnl":
                    f.constraints.add("PNL_UNREALIZED")
                if x["kind"] == "estimate":
                    f.constraints.add("PNL_ESTIMATED_FROM_MARK")
    count_fact(f, "chapters", len(chapters), "story_event")
    return [it]


COLUMNS: dict[str, Column] = {col.key: col for col in (
    Column("weekly_scorecard", "一周巨鲸成绩单", "Weekly whale scorecard", "weekly", weekly_scorecard, "P2", "multi"),
    Column("long_short_duel", "多空对决", "Long/short duel", "weekly", long_short_duel, "P3", "multi"),
    Column("streak_breaker", "连胜终结者", "Streak breaker", "hourly", streak_breaker, "P3", "hyperliquid"),
    Column("recap", "故事复盘", "Story recap", "trigger", recap, "P3", "trader"),
    Column("dossier", "交易员真实性调查", "Trader dossier", "trigger", dossier, "P3", "trader"),
    Column("copy_vs_board", "榜单收益 vs 真实可跟单收益", "Board ROI vs copyable returns", "weekly", copy_vs_board, "N1", "okx"),
    Column("board_dropouts", "榜单幸存者偏差追踪", "Board survivorship", "weekly", board_dropouts, "N2", "okx"),
    Column("liq_ladder", "强平价阶梯周报", "Liquidation ladder", "weekly", liq_ladder, "N3", "hyperliquid"),
    Column("forecast_ledger", "预测公开账本", "Forecast ledger", "monthly", forecast_ledger, "N4", "hyperliquid"),
    Column("funding_divergence", "资金费率与巨鲸方向背离", "Funding vs specialists", "daily", funding_divergence, "N5", "hyperliquid"),
    Column("holding_profile", "谁拿得住：持仓时长画像", "Who holds: holding times", "monthly", holding_profile, "N6", "hyperliquid"),
    Column("style_changes", "风格变迁档案", "Style change archive", "monthly", style_changes, "N7", "multi"),
)}
