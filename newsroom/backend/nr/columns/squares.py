"""Columns for the squares (OKX / Binance Square, docs/16): traders as characters. Boards become multiples lists and
dark horses; a trader's peak and fall becomes a story; stars get a daily scorecard, duels and the answer to the
question a post asked. Every number is a fact; board figures are the venue's own and say so."""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from nr import settings_store
from nr.columns.base import (ColumnIssue, chart, count_fact, day_key, pct_fact, short_name, stale, text_fact, time_fact,
                             week_key)
from nr.facts import fmt
from nr.facts.builder import D, Facts
from nr.feed.client import FeedClient

STAGE_ZH = {"rising": "崛起", "streak": "连胜", "peak": "巅峰", "crisis": "危机", "fall": "陨落", "comeback": "逆袭",
            "quiet": "沉寂"}
SIDE_ZH = {"long": "多单", "short": "空单"}
WINDOW_ZH = {"7d": "7 天", "30d": "30 天", "90d": "90 天"}
BOARD = ("LEADERBOARD_PNL", "OKX_LEAD_ASSETS", "SURVIVORSHIP", "NO_IDENTITY")


def _usd(f: Facts, fid: str, v, source: str, kind: str = "notional", **x) -> None:
    if v is not None:
        f.usd(fid, kind, D(v), source, **x)


def _multiple(f: Facts, fid: str, v, source: str, **x) -> None:
    if v is not None:
        f.add(fid, "ratio", D(v), fmt.multiple(D(v)), source, unit="x", **x)


def movers(c: FeedClient, window: str, limit: int = 100) -> dict:
    return c.research("movers", "/okx/board/movers", {"window": window, "limit": limit}, "rows", "movers.row")


def snapshot_board(s: Session, window: str, d: dict) -> None:
    """One row per trader and board day: the trajectory stories look back on ("then #3, up 10x")."""
    if not d["run"]:
        return
    for r in d["rows"]:
        s.execute(text("""
            INSERT INTO trader_board_snapshot (trader_key, board, business_date, rank, pnl, pnl_ratio, asset, off_peak, name)
            VALUES (:k, :b, :d, :r, :pnl, :ratio, :asset, :off, :n)
            ON CONFLICT (trader_key, board, business_date) DO UPDATE SET rank = EXCLUDED.rank, pnl = EXCLUDED.pnl,
              pnl_ratio = EXCLUDED.pnl_ratio, asset = EXCLUDED.asset, off_peak = EXCLUDED.off_peak, name = EXCLUDED.name"""),
            {"k": r["trader_key"], "b": window, "d": d["run"]["business_date"], "r": r["rank"], "pnl": D(r["pnl"]),
             "ratio": D(r["pnl_ratio"]), "asset": D(r["asset"]), "off": D(r["off_peak"]), "n": r["display_name"]})


def eligible(r: dict, cfg: dict) -> bool:
    """Headline material: an account big enough that its return means something (a $200 account can show 175x)."""
    return (r["asset"] is not None and D(r["asset"]) >= D(cfg["min_asset_usd"])
            and r["pnl"] is not None and D(r["pnl"]) >= D(cfg["min_pnl_usd"]))


def board_row(f: Facts, p: str, r: dict) -> None:
    text_fact(f, f"{p}.trader", short_name(r["trader_key"], r["display_name"]), "okx board nickname",
              label="self-chosen nickname on OKX")
    count_fact(f, f"{p}.rank", r["rank"], "okx_leaderboard_row.rank")
    _multiple(f, f"{p}.multiple", r["multiple"], "1 + okx_leaderboard_row.pnl_ratio",
              label="account multiple over the window (1 + board return)")
    pct_fact(f, f"{p}.return", r["pnl_ratio"], "okx_leaderboard_row.pnl_ratio", label="board return the venue reports")
    _usd(f, f"{p}.pnl", r["pnl"], "okx_leaderboard_row.pnl", kind="pnl", label="board P&L the venue reports")
    _usd(f, f"{p}.lead_assets", r["asset"], "okx_leaderboard_row.asset", label="lead-trading assets")
    if r.get("rank_change"):  # the direction is in the id, so it cannot be read the wrong way round
        up = r["rank_change"] > 0
        count_fact(f, f"{p}.{'places_up' if up else 'places_down'}", abs(r["rank_change"]),
                   "previous board rank - rank", label="places gained since the previous board" if up
                   else "places lost since the previous board")
    if r.get("new_entry"):
        text_fact(f, f"{p}.new_entry", "新上榜", "not on the previous board")
    if r.get("off_peak") is not None and r["off_peak"] >= 0.1:
        pct_fact(f, f"{p}.off_peak", r["off_peak"], "1 - equity now / equity peak (board return curve)",
                 label="how far the account is below its own peak in this window")


# ---- O1 multiples list ----------------------------------------------------------------------------------------

def okx_multipliers(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    cfg = settings_store.get(s, "squares")
    out = []
    for window in params.get("windows", ["7d", "30d"]):
        d = movers(c, window)
        snapshot_board(s, window, d)
        if not d["run"]:
            continue
        top10 = d["rows"][:10]
        rows = [r for r in d["rows"] if eligible(r, cfg)][: cfg["board_top"]]
        if len(rows) < 3:
            continue
        it = ColumnIssue("okx_multipliers", f"{d['run']['business_date']}-{window}", params={"window": window},
                         title_zh=f"OKX {WINDOW_ZH[window]}翻倍榜", title_en=f"OKX {window} multiples")
        f = it.facts
        f.constraints |= set(BOARD)
        if (b := stale(d["run"]["completed_at"], now, 48, "OKX board")):
            it.blocking.append(b)
        text_fact(f, "window", WINDOW_ZH[window], "board window")
        count_fact(f, "window_days", int(window[:-1]), "board window", label="days the board covers (7 天榜 / 30 天榜)")
        time_fact(f, "board.date", d["run"]["completed_at"], "okx_leaderboard_run.completed_at")
        rows_chart = []
        for i, r in enumerate(rows, 1):
            board_row(f, f"row.{i}", r)
            rows_chart.append({"label_fact": f"row.{i}.trader", "fact_ids": [f"row.{i}.multiple", f"row.{i}.pnl"]})
        text_fact(f, "selection", f"按带单资产和收益额筛过的前 {len(rows)} 位（榜单第 {rows[0]['rank']}–{rows[-1]['rank']} 名）",
                  "board rows that pass the square thresholds", label="what this list is: say it this way, not 前五")
        count_fact(f, "selection.size", len(rows), "board rows that pass the square thresholds")
        small = sum(1 for r in top10 if not eligible(r, cfg))
        if small:
            count_fact(f, "small_accounts_skipped", small, "board top 10",
                       label=f"traders in the top 10 left out: lead assets under {cfg['min_asset_usd']} USD or P&L "
                             f"under {cfg['min_pnl_usd']} USD")
        chart(f, f"OKX {WINDOW_ZH[window]}翻倍榜", f"OKX {window} multiples", rows_chart)
        out.append(it)
    return out


# ---- O2 dark horses -------------------------------------------------------------------------------------------

def okx_dark_horses(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    cfg = settings_store.get(s, "squares")
    d = movers(c, "7d")
    snapshot_board(s, "7d", d)
    if not d["run"]:
        return []
    rows = [r for r in d["rows"][:30] if eligible(r, cfg) and (r["new_entry"] or (r["rank_change"] or 0) >= 10)][:3]
    if not rows:
        return []
    it = ColumnIssue("okx_dark_horses", str(d["run"]["business_date"]), title_zh="OKX 新晋黑马",
                     title_en="OKX dark horses")
    f = it.facts
    f.constraints |= set(BOARD)
    if (b := stale(d["run"]["completed_at"], now, 48, "OKX board")):
        it.blocking.append(b)
    time_fact(f, "board.date", d["run"]["completed_at"], "okx_leaderboard_run.completed_at")
    count_fact(f, "window_days", 7, "board window", label="days the board covers (7 天榜)")
    for i, r in enumerate(rows, 1):
        board_row(f, f"row.{i}", r)
        if r["prev_rank"]:
            count_fact(f, f"row.{i}.prev_rank", r["prev_rank"], "previous board rank")
    return [it]


# ---- O3 fallen geniuses -----------------------------------------------------------------------------------------

def okx_fallen(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    """Traders who were at a board high (at least 4x in a window in the last 30 days) and are now far off their peak
    or gone from the board."""
    cfg = settings_store.get(s, "squares")
    current = {}
    for window in ("7d", "30d"):
        d = movers(c, window)
        snapshot_board(s, window, d)
        for r in d["rows"]:
            current.setdefault(r["trader_key"], {})[window] = r
    peaks = s.execute(text("""
        SELECT DISTINCT ON (trader_key) trader_key, board, business_date, rank, pnl_ratio, asset, name
        FROM trader_board_snapshot WHERE business_date >= :since AND pnl_ratio >= 3 AND asset >= :min_asset
        ORDER BY trader_key, pnl_ratio DESC"""),
        {"since": (now - timedelta(days=30)).date(), "min_asset": cfg["min_asset_usd"]}).mappings().all()
    out = []
    for pk in sorted(peaks, key=lambda p: -p["pnl_ratio"]):
        now_rows = current.get(pk["trader_key"], {})
        worst = max((r["off_peak"] or 0 for r in now_rows.values()), default=None)
        last_seen = s.execute(text("SELECT max(business_date) FROM trader_board_snapshot WHERE trader_key = :k"),
                              {"k": pk["trader_key"]}).scalar()
        gone = not now_rows and last_seen is not None and (now.date() - last_seen).days >= 2
        if not gone and (worst is None or worst < cfg["fall_off_peak"]):
            continue
        it = ColumnIssue("okx_fallen", f"{week_key(now)}:{pk['trader_key']}", trader_key=pk["trader_key"],
                         title_zh="天才陨落", title_en="Fallen genius", params={"trader_key": pk["trader_key"]})
        f = it.facts
        f.constraints |= set(BOARD)
        text_fact(f, "trader", short_name(pk["trader_key"], pk["name"]), "okx board nickname",
                  label="self-chosen nickname on OKX")
        _multiple(f, "peak.multiple", 1 + D(pk["pnl_ratio"]), "1 + board return at its best (board snapshots)")
        pct_fact(f, "peak.return", pk["pnl_ratio"], "trader_board_snapshot.pnl_ratio (best in 30 days)")
        count_fact(f, "peak.rank", pk["rank"], "trader_board_snapshot.rank")
        text_fact(f, "peak.board", WINDOW_ZH.get(pk["board"], pk["board"]), "board window of the peak")
        count_fact(f, "peak.board_days", int(pk["board"][:-1]), "board window of the peak")
        time_fact(f, "peak.date", datetime.combine(pk["business_date"], datetime.min.time()).replace(tzinfo=now.tzinfo),
                  "trader_board_snapshot.business_date")
        if gone:
            text_fact(f, "now.status", "已掉出榜单", "absent from today's 7-day and 30-day boards")
            time_fact(f, "now.last_on_board", datetime.combine(last_seen, datetime.min.time()).replace(tzinfo=now.tzinfo),
                      "trader_board_snapshot.business_date (latest)")
        else:
            w, r = max(now_rows.items(), key=lambda x: x[1]["off_peak"] or 0)
            text_fact(f, "now.status", "从高点大幅回撤", "board return curve")
            pct_fact(f, "now.off_peak", r["off_peak"], "1 - equity now / equity peak (board return curve)")
            pct_fact(f, "now.return", r["pnl_ratio"], f"{w} board return now")
            count_fact(f, "now.rank", r["rank"], f"{w} board rank now")
        try:
            prof = c.research("okx_profile", f"/okx/traders/{pk['trader_key']}/profile")
            cl = prof["closes_30d"]
            count_fact(f, "closes_30d.liquidations", cl["liquidations"], "okx_position (30 days)")
            if cl["worst"] is not None and D(cl["worst"]) < 0:
                _usd(f, "closes_30d.worst", cl["worst"], "okx_position.realized_pnl (worst in 30 days)", kind="realized_pnl")
        except Exception:  # noqa: BLE001 - the profile is extra colour; the fall is already in the facts
            pass
        out.append(it)
        if len(out) >= 2:
            break
    return out


# ---- O4 a turn in a trader's story ------------------------------------------------------------------------------

def story_turn(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    key = params["trader_key"]
    p = s.execute(text("SELECT * FROM trader_persona WHERE trader_key = :k"), {"k": key}).mappings().first()
    if p is None or p["stage"] != params["stage"]:
        return []
    it = ColumnIssue("story_turn", f"{p['stage']}:{day_key(now)}:{key}", trader_key=key,
                     title_zh=f"{p['name']}：{STAGE_ZH[p['prev_stage'] or 'quiet']}到{STAGE_ZH[p['stage']]}",
                     title_en=f"{p['name']}: {p['prev_stage']} to {p['stage']}", params=params)
    f = it.facts
    f.constraints |= {"NO_IDENTITY", "LEADERBOARD_PNL", "OKX_LEAD_ASSETS" if p["venue"] == "okx" else "BINANCE_LEAD_ACCOUNT"}
    text_fact(f, "trader", p["name"], "venue nickname", label="self-chosen nickname on the venue")
    text_fact(f, "stage.now", STAGE_ZH[p["stage"]], "trader_persona.stage (rules in nr/stories/persona.py)")
    text_fact(f, "stage.before", STAGE_ZH[p["prev_stage"] or "quiet"], "trader_persona.prev_stage")
    persona_facts(f, p["traits"])
    return [it]


def persona_facts(f: Facts, t: dict) -> None:
    """The persona's numbers, each with its source, so a story can cite what made the stage."""
    if t.get("multiple") is not None:
        _multiple(f, "traits.multiple", t["multiple"], f"board multiple ({t.get('board', '')})")
    if t.get("off_peak") is not None:
        pct_fact(f, "traits.off_peak", t["off_peak"], "board return curve: below own peak")
    if t.get("rank") is not None:
        count_fact(f, "traits.rank", t["rank"], f"board rank ({t.get('board', '')})")
    if t.get("roi_30d") is not None:
        pct_fact(f, "traits.roi_30d", t["roi_30d"], "platform 30-day return")
    if t.get("win_rate") is not None:
        pct_fact(f, "traits.win_rate", t["win_rate"], "win rate over closed positions")
    for k, label in (("win_streak", "consecutive winning closes"), ("loss_streak", "consecutive losing closes"),
                     ("liquidations", "liquidations in 30 days"), ("closes", "closed positions in 30 days")):
        if t.get(k):
            count_fact(f, f"traits.{k}", t[k], label)
    for k in ("pnl_7d", "best", "worst"):
        if t.get(k) is not None:
            _usd(f, f"traits.{k}", t[k], f"realized P&L ({k})", kind="realized_pnl")
    if t.get("max_lever") is not None:
        f.add("traits.max_lever", "leverage", D(t["max_lever"]), fmt.leverage(D(t["max_lever"])), "highest leverage, 30 days")
    if t.get("top_coin"):
        text_fact(f, "traits.top_coin", t["top_coin"], "most traded coin, 30 days")


# ---- S1 the stars' day -----------------------------------------------------------------------------------------

def star_daily(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    """Late in the (UTC) day: each Binance star's last 24 hours, for Binance Square."""
    if now.hour < 13 and not params.get("force"):
        return []
    since = now - timedelta(hours=24)
    stars = s.execute(text("""SELECT trader_key, name, stats_30d FROM star_trader WHERE active AND venue = 'binance'
                              ORDER BY id""")).mappings().all()
    it = ColumnIssue("star_daily", day_key(now), title_zh="明星带单员今日战绩", title_en="The stars' day")
    f = it.facts
    f.constraints |= {"BINANCE_LEAD_ACCOUNT", "LEADERBOARD_PNL", "NO_IDENTITY"}
    n = 0
    for st in stars:
        closes = s.execute(text("""SELECT pnl FROM relay_close WHERE trader_key = :k AND closed_at > :since
                                   AND pnl IS NOT NULL"""), {"k": st["trader_key"], "since": since}).scalars().all()
        opens = s.execute(text("""SELECT count(*) FROM source_event se JOIN trader t ON t.id = se.trader_id
                                  WHERE t.trader_key = :k AND se.kind IN ('open', 'flip') AND se.observed_at > :since"""),
                          {"k": st["trader_key"], "since": since}).scalar()
        if not closes and not opens:
            continue
        n += 1
        p = f"star.{n}"
        text_fact(f, f"{p}.trader", st["name"], "star list name", label="self-chosen nickname on Binance")
        count_fact(f, f"{p}.opens_24h", opens, "relay snapshots (opens in 24 hours)")
        if closes:
            count_fact(f, f"{p}.closes_24h", len(closes), "relay closed positions (24 hours)")
            count_fact(f, f"{p}.wins_24h", sum(1 for x in closes if x > 0), "relay closed positions (24 hours)")
            _usd(f, f"{p}.pnl_24h", sum(closes, Decimal(0)), "sum of realized P&L, 24 hours", kind="realized_pnl")
        if st["stats_30d"] and st["stats_30d"].get("roi") is not None:
            pct_fact(f, f"{p}.roi_30d", D(st["stats_30d"]["roi"]) / 100, "platform 30-day return")
    if n == 0:
        return []
    count_fact(f, "stars_active", n, "stars with a move in 24 hours")
    return [it]


# ---- duels between two stars, and their result ------------------------------------------------------------------

def _side_facts(f: Facts, p: str, key: str, name: str, side: str, pos: dict | None) -> None:
    text_fact(f, f"{p}.trader", name, "star list name", label="self-chosen nickname on the venue")
    text_fact(f, f"{p}.side", SIDE_ZH[side], "relay position side")
    if pos:
        _usd(f, f"{p}.notional", pos["notional"], "relay position notional")
        if pos["lever"] is not None:
            f.add(f"{p}.leverage", "leverage", D(pos["lever"]), fmt.leverage(D(pos["lever"])), "relay position lever")
        if pos["entry_px"] is not None:
            f.add(f"{p}.entry_px", "price", D(pos["entry_px"]), fmt.price(D(pos["entry_px"])), "relay position entry",
                  unit="USD")
        if pos["upl"] is not None and pos["mark_px"]:
            _usd(f, f"{p}.upl", pos["upl"], "relay position unrealized P&L", kind="unrealized_pnl")
            f.constraints.add("PNL_UNREALIZED")


def duel(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    d = s.execute(text("SELECT * FROM star_duel WHERE id = :i"), {"i": params["duel_id"]}).mappings().first()
    if d is None:
        return []
    result = params.get("result", False)
    it = ColumnIssue("duel_result" if result else "duel", f"{d['id']}", coin=d["coin"], trader_key=d["a_key"],
                     title_zh=f"{'对决结果' if result else '明星对决'}：${d['coin']}", title_en=f"Duel on {d['coin']}",
                     params=params)
    f = it.facts
    f.constraints |= {"BINANCE_LEAD_ACCOUNT", "NO_IDENTITY"}
    names = dict(s.execute(text("SELECT trader_key, name FROM star_trader WHERE trader_key IN (:a, :b)"),
                           {"a": d["a_key"], "b": d["b_key"]}).all())
    text_fact(f, "coin", d["coin"], "star_duel.coin")
    for p, key, side in (("a", d["a_key"], d["a_side"]), ("b", d["b_key"], d["b_side"])):
        pos = s.execute(text("""SELECT * FROM relay_position WHERE trader_key = :k AND side = :sd AND open
                                AND (symbol = :c || 'USDT' OR symbol = :c || 'USDC') ORDER BY last_seen_at DESC LIMIT 1"""),
                        {"k": key, "sd": side, "c": d["coin"]}).mappings().first()
        _side_facts(f, p, key, names.get(key, key), side, pos)
        if result:
            cl = s.execute(text("""SELECT pnl, closed_at FROM relay_close WHERE trader_key = :k AND side = :sd
                                   AND closed_at >= :o AND (symbol = :c || 'USDT' OR symbol = :c || 'USDC')
                                   ORDER BY closed_at LIMIT 1"""),
                           {"k": key, "sd": side, "o": d["opened_at"], "c": d["coin"]}).mappings().first()
            if cl and cl["pnl"] is not None:
                _usd(f, f"{p}.realized_pnl", cl["pnl"], "relay closed position P&L", kind="realized_pnl")
                text_fact(f, f"{p}.status", "已平仓", "relay closed positions")
            else:
                text_fact(f, f"{p}.status", "还没平仓", "relay positions")
    time_fact(f, "duel.since", d["opened_at"], "star_duel.opened_at", label="both sides were first held from here")
    if result and d["result"] and d["result"].get("winner"):
        text_fact(f, "duel.leader", names.get(d["result"]["winner"], d["result"]["winner"]),
                  "star_duel.result (higher realized or unrealized P&L when the first side closed)")
    return [it]


def duel_result(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    return duel(s, c, now, {**params, "result": True})


# ---- a post's question, answered --------------------------------------------------------------------------------

def prediction_followup(s: Session, c: FeedClient, now: datetime, params: dict) -> list[ColumnIssue]:
    p = s.execute(text("""SELECT sp.*, pr.published_at, coalesce(st.name, t.display_name) AS name
                          FROM story_prediction sp JOIN publication_record pr ON pr.article_id = sp.article_id
                            AND pr.status = 'published'
                          LEFT JOIN star_trader st ON st.trader_key = sp.trader_key
                          LEFT JOIN trader t ON t.trader_key = sp.trader_key
                          WHERE sp.id = :i"""), {"i": params["prediction_id"]}).mappings().first()
    if p is None or p["status"] != "resolved" or not p["outcome"]:
        return []
    o = p["outcome"]
    it = ColumnIssue("prediction_followup", str(p["id"]), coin=p["params"].get("coin", "*"), trader_key=p["trader_key"],
                     title_zh="上期的问题，答案来了", title_en="The answer to last time's question", params=params)
    f = it.facts
    f.constraints |= {"NO_IDENTITY", "HINDSIGHT"}
    text_fact(f, "trader", p["name"] or p["trader_key"], "venue nickname", label="self-chosen nickname on the venue")
    text_fact(f, "question", p["question"], "story_prediction.question", label="what the earlier post asked readers")
    time_fact(f, "asked_at", p["published_at"], "publication_record.published_at")
    text_fact(f, "answer", o["answer"], "story_prediction.outcome")
    if o.get("pnl") is not None:
        _usd(f, "result.realized_pnl", o["pnl"], o.get("source", "closed position"), kind="realized_pnl")
    if o.get("held_hours") is not None:
        v, unit, disp = fmt.duration(D(o["held_hours"]))
        f.add("result.held", "duration", v, disp, "closed - opened", unit=unit)
    return [it]
