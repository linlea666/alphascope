"""What a square post (OKX / Binance Square, docs/16) gets besides the facts: how to name the trader, the trader's
persona and stage, which episode of the series this is, how to close, and the question it asks readers, which the
desk comes back to (nr/stories/predictions.py)."""

import json
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from nr.desk.assignment import SQUARES

CLOSINGS = ("站队", "预测", "投票", "求补充")
HOLDING = ("whale_open", "flip", "whale_add", "underwater_add", "near_liquidation", "notable_trader_move")
SIDE_ZH = {"long": "多单", "short": "空单"}


def mask(name: str) -> str:
    """熬鹰 -> 熬x, 三年好 -> 三x好, 中二的天才交易员 -> 中xxx员: recognisable to followers, not a full name."""
    chars = list(name)
    if len(chars) <= 1:
        return name
    if len(chars) == 2:
        return chars[0] + "x"
    if len(chars) == 3:
        return chars[0] + "x" + chars[2]
    return chars[0] + "xxx" + chars[-1]


def question_for(packet: dict) -> tuple[str, str, dict, timedelta] | None:
    """The verifiable question a post can close on: (question, kind, params, how long until we look)."""
    ev, subj = packet["event"], packet["subject"]
    if not subj.get("trader_key"):
        return None
    if ev["class"] in HOLDING and ev.get("direction") in SIDE_ZH:
        return (f"这笔 ${ev['coin']} {SIDE_ZH[ev['direction']]}最后是赚是亏？", "position_closed",
                {"trader_key": subj["trader_key"], "coin": ev["coin"], "side": ev["direction"]}, timedelta(days=7))
    return None


def context(s: Session, account: dict, packet: dict, article_id: int, story_id: int | None, now: datetime) -> dict:
    """Extra data for the writer of a square post; empty for other platforms."""
    if account["platform"] not in SQUARES:
        return {}
    subj = packet["subject"]
    out: dict = {"closing": CLOSINGS[article_id % len(CLOSINGS)]}
    name = subj.get("name")
    if name:
        out["naming"] = mask(name) if account.get("name_display") == "masked" else name
    key = subj.get("trader_key")
    if key:
        p = s.execute(text("""SELECT name, tags, stage, prev_stage, stage_since, traits, editor_note FROM trader_persona
                              WHERE trader_key = :k"""), {"k": key}).mappings().first()
        if p:
            out["persona"] = {**dict(p), "stage_since": p["stage_since"].isoformat() if p["stage_since"] else None,
                              "note": "人物设定：人设标签由数据算出；stage 是他现在所处的故事阶段，prev_stage 是上一个阶段"}
        if subj.get("star") and story_id:
            n = s.execute(text("""SELECT count(*) FROM publication_record pr JOIN article a ON a.id = pr.article_id
                                  WHERE a.story_id = :s AND pr.status = 'published'"""), {"s": story_id}).scalar()
            out["series"] = {"title": f"{out.get('naming') or name or ''}追踪", "episode": n + 1}
    q = question_for(packet)
    if q and story_id:
        question, kind, params, horizon = q
        existing = s.execute(text("SELECT question FROM story_prediction WHERE article_id = :a AND status = 'open'"),
                             {"a": article_id}).scalar()
        if existing is None:
            s.execute(text("""INSERT INTO story_prediction (story_id, article_id, trader_key, question, kind, params, due_at)
                              VALUES (:s, :a, :k, :q, :kind, CAST(:p AS jsonb), :due)"""),
                      {"s": story_id, "a": article_id, "k": key, "q": question, "kind": kind,
                       "p": json.dumps(params), "due": now + horizon})
        out["closing"] = "预测"
        out["closing_question"] = existing or question
    return out
