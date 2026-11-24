"""The squares' story machinery (docs/16), run every half hour by the worker:

- personas: tags and a story stage per trader, from the data (Binance: the relay; OKX: AlphaScope's profile). A
  stage change on a star or a trader we have published becomes a story_turn piece;
- duels: two Binance stars on opposite sides of one coin, and the result once a side closes;
- predictions: the question a published post asked, answered from what the trader did since.

The rules are plain thresholds, listed here, so a stage can always be explained."""

import json
import logging
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from nr import settings_store
from nr.feed.client import FeedClient, FeedError, FeedUnavailable
from nr.jobs.queue import enqueue
from nr.sources.relay import coin_of

log = logging.getLogger(__name__)
TELLABLE = ("rising", "streak", "peak", "crisis", "fall", "comeback")
MAX_OKX_PROFILES = 40


def D(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def f(v) -> float | None:
    return None if v is None else float(v)


def streaks(pnls: list) -> tuple[int, int]:
    """(wins, losses) in a row from the latest close; one of them is 0."""
    if not pnls:
        return 0, 0
    sign, n = pnls[0] > 0, 0
    for p in pnls:
        if (p > 0) != sign:
            break
        n += 1
    return (n, 0) if sign else (0, n)


def tags_for(t: dict) -> list[dict]:
    out = []
    if (t.get("max_lever") or 0) >= 50:
        out.append({"tag": "梭哈型", "why": f"30 天内最高 {int(t['max_lever'])} 倍杠杆"})
    if t.get("closes", 0) >= 5 and t.get("short_share") is not None:
        if t["short_share"] >= 0.7:
            out.append({"tag": "空军司令", "why": "七成以上是空单"})
        elif t["short_share"] <= 0.3:
            out.append({"tag": "多头信仰", "why": "七成以上是多单"})
    if (t.get("win_rate") or 0) >= 0.7 and (t.get("off_peak") or 0) <= 0.1 and t.get("closes", 0) >= 5:
        out.append({"tag": "稳健收租", "why": "胜率高、回撤小"})
    if t.get("top_coin") and t.get("top_coin_share", 0) >= 0.6:
        out.append({"tag": f"{t['top_coin']} 专业户", "why": "六成以上的单子在这个币"})
    if t.get("liquidations"):
        out.append({"tag": "爆过仓", "why": "30 天内有强平"})
    if (t.get("followers") or 0) >= 1000:
        out.append({"tag": "人气王", "why": f"{t['followers']} 人关注"})
    if (t.get("multiple") or 0) >= 2 or (t.get("roi_30d") or 0) >= 1:
        out.append({"tag": "翻倍选手", "why": "这个周期账户翻了倍"})
    return out


def stage_for(t: dict, prev: str | None, cfg: dict) -> str:
    equity, pnl7 = t.get("equity"), t.get("pnl_7d")
    loss7 = -pnl7 / equity if equity and pnl7 is not None and pnl7 < 0 else 0
    off = t.get("off_peak") or 0
    if off >= cfg["fall_off_peak"] or loss7 >= 0.3 or t.get("dropped") or \
            (t.get("liquidations") and (t.get("peak_multiple") or 0) >= 2):
        stage = "fall"
    elif off >= 0.2 or loss7 >= 0.1 or t.get("loss_streak", 0) >= 3:
        stage = "crisis"
    elif ((t.get("multiple") or 0) >= 3 or (t.get("roi_30d") or 0) >= 1) and off <= 0.05 and (pnl7 is None or pnl7 >= 0):
        stage = "peak"
    elif t.get("win_streak", 0) >= 4:
        stage = "streak"
    elif (t.get("rank_up") or 0) >= 10 or ((t.get("roi_30d") or 0) >= 0.3 and (pnl7 or 0) > 0):
        stage = "rising"
    else:
        stage = "quiet"
    if prev in ("fall", "crisis") and stage in ("peak", "streak", "rising"):
        return "comeback"
    return stage


def binance_traits(s: Session, key: str, now: datetime) -> dict:
    st = s.execute(text("SELECT stats_30d, equity FROM star_trader WHERE trader_key = :k"), {"k": key}).mappings().first() or {}
    closes = s.execute(text("""SELECT symbol, side, pnl, lever, closed_at FROM relay_close WHERE trader_key = :k
                               AND closed_at > :since AND pnl IS NOT NULL ORDER BY closed_at DESC"""),
                       {"k": key, "since": now - timedelta(days=30)}).mappings().all()
    levers = [c["lever"] for c in closes if c["lever"] is not None] + list(s.execute(text(
        "SELECT lever FROM relay_position WHERE trader_key = :k AND open AND lever IS NOT NULL"), {"k": key}).scalars())
    pnls = [D(c["pnl"]) for c in closes]
    wins, losses = streaks(pnls)
    coins = Counter(coin_of(c["symbol"]) for c in closes)
    stats = st.get("stats_30d") or {}
    t = {"venue": "binance", "closes": len(closes), "win_streak": wins, "loss_streak": losses,
         "pnl_7d": f(sum((D(c["pnl"]) for c in closes if c["closed_at"] > now - timedelta(days=7)), Decimal(0)))
         if closes else None,
         "best": f(max(pnls)) if pnls else None, "worst": f(min(pnls)) if pnls else None,
         "max_lever": f(max(levers)) if levers else None, "equity": f(st.get("equity")),
         "roi_30d": f(D(stats["roi"]) / 100) if stats.get("roi") is not None else None,
         "win_rate": f(D(stats["win_rate"]) / 100) if stats.get("win_rate") is not None else None}
    if coins:
        coin, n = coins.most_common(1)[0]
        t.update(top_coin=coin, top_coin_share=n / len(closes),
                 short_share=sum(1 for c in closes if c["side"] == "short") / len(closes))
    return t


def okx_traits(s: Session, c: FeedClient, key: str, now: datetime) -> dict | None:
    try:
        p = c.research("okx_profile", f"/okx/traders/{key}/profile")
    except (FeedUnavailable, FeedError) as exc:
        log.info("okx profile %s unavailable: %s", key, exc)
        return None
    on = [b for b in p["boards"] if b["on_board"]]
    best = max(on, key=lambda b: float(b["multiple"] or 0), default=None)
    cl = p["closes_30d"]
    peak = s.execute(text("""SELECT max(pnl_ratio) FROM trader_board_snapshot WHERE trader_key = :k
                             AND business_date >= :since"""), {"k": key, "since": (now - timedelta(days=30)).date()}).scalar()
    ranks = s.execute(text("""SELECT rank FROM trader_board_snapshot WHERE trader_key = :k AND board = '7d'
                              ORDER BY business_date DESC LIMIT 4"""), {"k": key}).scalars().all()
    closes = int(cl["closes"] or 0)
    t = {"venue": "okx", "followers": p.get("followers"), "closes": closes,
         "liquidations": int(cl["liquidations"] or 0), "best": f(cl["best"]), "worst": f(cl["worst"]),
         "max_lever": f(cl["max_lever"]), "top_coin": cl["top_coin"],
         "short_share": f(cl["short_share"]), "equity": f(p.get("asset")),
         "win_rate": (int(cl["wins"] or 0) / closes) if closes else None,
         "peak_multiple": 1 + float(peak) if peak is not None else None,
         "dropped": not on and peak is not None and float(peak) >= 3,
         "rank_up": (ranks[-1] - ranks[0]) if len(ranks) >= 2 else None}
    if best:
        t.update(board=best["window"], multiple=f(best["multiple"]), off_peak=f(best["off_peak"]), rank=best["rank"])
    return t


def refresh_personas(sf: sessionmaker, c: FeedClient | None, now: datetime) -> dict:
    out = {"updated": 0, "turns": 0}
    with sf() as s:
        cfg = settings_store.get(s, "squares")
        people = s.execute(text("""
            SELECT trader_key, venue, name, true AS star FROM star_trader WHERE active
            UNION
            SELECT DISTINCT t.trader_key, t.venue, coalesce(t.display_name, t.trader_key), false
            FROM publication_record pr JOIN article a ON a.id = pr.article_id JOIN news_event e ON e.id = a.news_event_id
            JOIN trader t ON t.id = e.trader_id
            WHERE pr.status = 'published' AND pr.published_at > :since AND t.venue IN ('okx', 'binance')
              AND t.trader_key NOT IN (SELECT trader_key FROM star_trader WHERE active)"""),
            {"since": now - timedelta(days=60)}).mappings().all()
        # the least recently refreshed first, so the OKX calls rotate through everyone
        seen = dict(s.execute(text("SELECT trader_key, updated_at FROM trader_persona")).all())
    okx_calls = 0
    for p in sorted(people, key=lambda x: seen.get(x["trader_key"]) or datetime.min.replace(tzinfo=now.tzinfo)):
        with sf() as s:
            if p["venue"] == "binance":
                t = binance_traits(s, p["trader_key"], now)
            elif p["venue"] == "okx" and c is not None and okx_calls < MAX_OKX_PROFILES:
                okx_calls += 1
                t = okx_traits(s, c, p["trader_key"], now)
            else:
                continue
            if t is None:
                continue
            row = s.execute(text("SELECT stage, tags FROM trader_persona WHERE trader_key = :k"),
                            {"k": p["trader_key"]}).mappings().first()
            prev = row["stage"] if row else None
            stage = stage_for(t, prev, cfg)
            edited = [x for x in (row["tags"] if row else []) if x.get("why") == "editor"]
            s.execute(text("""
                INSERT INTO trader_persona (trader_key, venue, name, tags, stage, stage_since, prev_stage, traits, updated_at)
                VALUES (:k, :v, :n, CAST(:tags AS jsonb), :st, :now, NULL, CAST(:tr AS jsonb), :now)
                ON CONFLICT (trader_key) DO UPDATE SET name = EXCLUDED.name, tags = EXCLUDED.tags, traits = EXCLUDED.traits,
                  updated_at = EXCLUDED.updated_at,
                  prev_stage = CASE WHEN trader_persona.stage <> EXCLUDED.stage THEN trader_persona.stage
                                    ELSE trader_persona.prev_stage END,
                  stage_since = CASE WHEN trader_persona.stage <> EXCLUDED.stage THEN EXCLUDED.stage_since
                                     ELSE trader_persona.stage_since END,
                  stage = EXCLUDED.stage"""),
                {"k": p["trader_key"], "v": p["venue"], "n": p["name"], "st": stage, "now": now,
                 "tags": json.dumps(edited or tags_for(t), ensure_ascii=False), "tr": json.dumps(t, default=str)})
            out["updated"] += 1
            # a turn in the story is news for a star, or for someone readers already met in a post
            if prev is not None and prev != stage and stage in TELLABLE:
                enqueue(s, "column_build", {"column": "story_turn", "params": {"trader_key": p["trader_key"],
                                                                                "stage": stage}},
                        dedupe_key=f"story_turn:{p['trader_key']}:{stage}:{now:%Y-%m-%d}", priority=55, now=now)
                out["turns"] += 1
            s.commit()
    return out


def detect_duels(sf: sessionmaker, now: datetime) -> dict:
    out = {"opened": 0, "resolved": 0}
    with sf() as s:
        floor = Decimal(str(settings_store.get(s, "stars")["min_notional_usd"]))
        held = s.execute(text("""
            SELECT p.trader_key, p.symbol, p.side, p.notional, p.first_seen_at, p.upl FROM relay_position p
            JOIN star_trader st ON st.trader_key = p.trader_key AND st.active WHERE p.open AND p.notional >= :f"""),
            {"f": floor}).mappings().all()
        by_coin: dict[str, list] = {}
        for h in held:
            by_coin.setdefault(coin_of(h["symbol"]), []).append(h)
        for coin, hs in by_coin.items():
            for a in hs:
                for b in hs:
                    if a["trader_key"] >= b["trader_key"] or a["side"] == b["side"]:
                        continue
                    if s.execute(text("""SELECT 1 FROM star_duel WHERE coin = :c AND a_key = :a AND b_key = :b
                                         AND status = 'open'"""), {"c": coin, "a": a["trader_key"], "b": b["trader_key"]}).scalar():
                        continue
                    did = s.execute(text("""
                        INSERT INTO star_duel (coin, a_key, a_side, b_key, b_side, opened_at)
                        VALUES (:c, :a, :as, :b, :bs, :o) ON CONFLICT DO NOTHING RETURNING id"""),
                        {"c": coin, "a": a["trader_key"], "as": a["side"], "b": b["trader_key"], "bs": b["side"],
                         "o": max(a["first_seen_at"], b["first_seen_at"])}).scalar()
                    if did:
                        enqueue(s, "column_build", {"column": "duel", "params": {"duel_id": did}},
                                dedupe_key=f"duel:{did}", priority=55, now=now)
                        out["opened"] += 1
        for d in s.execute(text("SELECT * FROM star_duel WHERE status = 'open'")).mappings().all():
            still = {(h["trader_key"], h["side"]) for h in by_coin.get(d["coin"], [])}
            a_in, b_in = (d["a_key"], d["a_side"]) in still, (d["b_key"], d["b_side"]) in still
            if a_in and b_in:
                continue
            results = {}
            for key, side, open_ in ((d["a_key"], d["a_side"], a_in), (d["b_key"], d["b_side"], b_in)):
                if open_:
                    upl = next((h["upl"] for h in by_coin.get(d["coin"], []) if h["trader_key"] == key and h["side"] == side), None)
                    results[key] = upl
                else:
                    results[key] = s.execute(text("""
                        SELECT pnl FROM relay_close WHERE trader_key = :k AND side = :sd AND closed_at >= :o
                          AND (symbol = :c || 'USDT' OR symbol = :c || 'USDC') ORDER BY closed_at LIMIT 1"""),
                        {"k": key, "sd": side, "o": d["opened_at"], "c": d["coin"]}).scalar()
            closed = [k for k, open_ in ((d["a_key"], a_in), (d["b_key"], b_in)) if not open_]
            if any(results[k] is None for k in closed) and now - d["opened_at"] < timedelta(hours=72):
                continue  # the relay's history has not shown the close yet
            known = {k: v for k, v in results.items() if v is not None}
            winner = max(known, key=lambda k: known[k]) if len(known) == 2 else None
            s.execute(text("""UPDATE star_duel SET status = 'resolved', resolved_at = :n, result = CAST(:r AS jsonb)
                              WHERE id = :i"""),
                      {"n": now, "i": d["id"], "r": json.dumps({"pnl": {k: str(v) if v is not None else None
                                                                         for k, v in results.items()}, "winner": winner,
                                                                 "closed": closed})})
            enqueue(s, "column_build", {"column": "duel_result", "params": {"duel_id": d["id"]}},
                    dedupe_key=f"duel_result:{d['id']}", priority=55, now=now)
            out["resolved"] += 1
        s.commit()
    return out


def resolve_predictions(sf: sessionmaker, now: datetime) -> dict:
    out = {"resolved": 0, "expired": 0}
    with sf() as s:
        open_ = s.execute(text("""
            SELECT sp.* FROM story_prediction sp WHERE sp.status = 'open' AND EXISTS (
              SELECT 1 FROM publication_record pr WHERE pr.article_id = sp.article_id AND pr.status = 'published')""")
                          ).mappings().all()
        for p in open_:
            prm, outcome = p["params"], None
            if p["kind"] == "position_closed" and p["trader_key"].startswith("binance:"):
                c = s.execute(text("""SELECT pnl, opened_at, closed_at FROM relay_close WHERE trader_key = :k AND side = :sd
                                      AND closed_at > :t AND (symbol = :c || 'USDT' OR symbol = :c || 'USDC')
                                      ORDER BY closed_at LIMIT 1"""),
                              {"k": p["trader_key"], "sd": prm["side"], "t": p["created_at"], "c": prm["coin"]}).mappings().first()
                if c and c["pnl"] is not None:
                    outcome = {"answer": "赚了" if c["pnl"] > 0 else "亏了", "pnl": str(c["pnl"]),
                               "source": "relay closed position P&L",
                               "held_hours": (c["closed_at"] - c["opened_at"]).total_seconds() / 3600 if c["opened_at"] else None}
            elif p["kind"] == "position_closed":
                e = s.execute(text("""
                    SELECT ne.event_class, p.content FROM news_event ne JOIN trader t ON t.id = ne.trader_id
                    LEFT JOIN fact_packet p ON p.id = ne.current_fact_packet_id
                    WHERE t.trader_key = :k AND ne.coin = :c AND ne.event_class IN ('whale_close', 'liquidation')
                      AND ne.detected_at > :t ORDER BY ne.detected_at LIMIT 1"""),
                    {"k": p["trader_key"], "c": prm["coin"], "t": p["created_at"]}).mappings().first()
                if e:
                    facts = {x["id"]: x for x in (e["content"] or {}).get("facts", [])}
                    pnl = next((facts[i]["value"] for i in ("liquidation.realized_pnl_usd", "close.realized_pnl_usd")
                                if i in facts), None)
                    outcome = {"answer": "爆仓了" if e["event_class"] == "liquidation" else "平仓了", "pnl": pnl,
                               "source": "venue realized P&L" if pnl is not None else None}
            if outcome:
                s.execute(text("""UPDATE story_prediction SET status = 'resolved', resolved_at = :n,
                                  outcome = CAST(:o AS jsonb) WHERE id = :i"""),
                          {"n": now, "o": json.dumps(outcome, ensure_ascii=False), "i": p["id"]})
                enqueue(s, "column_build", {"column": "prediction_followup", "params": {"prediction_id": p["id"]}},
                        dedupe_key=f"prediction:{p['id']}", priority=55, now=now)
                out["resolved"] += 1
            elif p["due_at"] < now:
                s.execute(text("UPDATE story_prediction SET status = 'expired' WHERE id = :i"), {"i": p["id"]})
                out["expired"] += 1
        # questions on drafts that never went out lapse at their due time
        out["expired"] += s.execute(text("""UPDATE story_prediction SET status = 'expired' WHERE status = 'open'
                                            AND due_at < :n"""), {"n": now}).rowcount
        s.commit()
    return out


def tick(sf: sessionmaker, c: FeedClient | None, now: datetime) -> dict:
    return {"personas": refresh_personas(sf, c, now), "duels": detect_duels(sf, now),
            "predictions": resolve_predictions(sf, now)}
