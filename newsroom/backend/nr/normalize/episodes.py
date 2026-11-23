"""Position episodes: one per (trader, coin) from the first time we see a side until it is closed or flipped.

What we know about the start is kept as-is: an episode that begins with a radar `first_seen` was already open when
first polled, so it is never described as opened then (start_kind=first_seen, start_precision=unknown).
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

ZERO = Decimal(0)


@dataclass
class Change:
    event: dict          # the source_event row
    episode: dict        # the episode the event now belongs to (after applying it)
    ended: dict | None   # an episode this event closed or flipped (its final state)


def direction(szi: Decimal) -> str:
    return "long" if szi > 0 else "short"


def notional(szi: Decimal | None, px: Decimal | None) -> Decimal:
    return abs(szi) * px if szi is not None and px is not None else ZERO


def _get_open(s: Session, trader_id: int, coin: str) -> dict | None:
    row = s.execute(text("""SELECT * FROM position_episode WHERE trader_id = :t AND coin = :c AND status = 'open'
                            FOR UPDATE"""), {"t": trader_id, "c": coin}).mappings().first()
    return dict(row) if row else None


def _new(s: Session, e: dict, start_kind: str, precision: str, start_from, start_to, szi: Decimal) -> dict:
    ntl = notional(szi, e["mark_px"])
    row = s.execute(text("""
        INSERT INTO position_episode (trader_id, venue, coin, direction, start_kind, start_precision, start_from,
          start_to, peak_abs_szi, peak_notional, last_szi, last_mark_px, last_entry_px, first_source_event_id,
          last_source_event_id)
        VALUES (:t, :v, :c, :d, :k, :p, :sf, :st, :peak, :ntl, :szi, :mark, :entry, :e, :e) RETURNING *"""),
        {"t": e["trader_id"], "v": e["venue"], "c": e["coin"], "d": direction(szi), "k": start_kind, "p": precision,
         "sf": start_from, "st": start_to, "peak": abs(szi), "ntl": ntl, "szi": szi, "mark": e["mark_px"],
         "entry": e["entry_px"], "e": e["id"]}).mappings().one()
    return dict(row)


def _end(s: Session, ep: dict, e: dict, status: str) -> dict:
    row = s.execute(text("""
        UPDATE position_episode SET status = :st, end_from = :ef, end_to = :et, last_szi = 0, last_mark_px = :mark,
               last_source_event_id = :e, updated_at = now()
        WHERE id = :i RETURNING *"""),
        {"st": status, "ef": e["window_start"], "et": e["observed_at"], "mark": e["mark_px"], "e": e["id"],
         "i": ep["id"]}).mappings().one()
    return dict(row)


def _resize(s: Session, ep: dict, e: dict) -> dict:
    inc = e["kind"] == "increase"
    szi = e["szi_after"]
    row = s.execute(text("""
        UPDATE position_episode SET last_szi = :szi, last_mark_px = coalesce(:mark, last_mark_px),
               last_entry_px = coalesce(:entry, last_entry_px),
               peak_abs_szi = greatest(peak_abs_szi, :abs), peak_notional = greatest(peak_notional, :ntl),
               n_increases = n_increases + :inc, n_decreases = n_decreases + :dec,
               added_notional = added_notional + :add, reduced_notional = reduced_notional + :red,
               last_source_event_id = :e, updated_at = now()
        WHERE id = :i RETURNING *"""),
        {"szi": szi, "mark": e["mark_px"], "entry": e["entry_px"], "abs": abs(szi), "ntl": notional(szi, e["mark_px"]),
         "inc": int(inc), "dec": int(e["kind"] == "decrease"), "add": e["notional_change"] if inc else ZERO,
         "red": ZERO if inc else e["notional_change"], "e": e["id"], "i": ep["id"]}).mappings().one()
    return dict(row)


def _start(e: dict) -> tuple[str, object, object]:
    """(precision, start_from, start_to) of an open or flip: exact when the venue reported the open time."""
    if e["time_precision"] == "exact":
        return "exact", e["event_time"], e["event_time"]
    return "window", e["window_start"], e["observed_at"]


def apply(s: Session, e: dict) -> Change:
    """Apply one source event (in id order) and mark it processed."""
    ep = _get_open(s, e["trader_id"], e["coin"])
    ended = None
    kind = e["kind"]
    if kind == "first_seen":
        cur = ep or _new(s, e, "first_seen", "unknown", None, e["observed_at"], e["szi_after"])
    elif kind == "open":
        if ep:  # we missed its close: end it at this window rather than merging two positions
            ended = _end(s, ep, e, "closed")
        p, a, b = _start(e)
        cur = _new(s, e, "open", p, a, b, e["szi_after"])
    elif kind == "flip":
        if ep:
            ended = _end(s, ep, e, "flipped")
        p, a, b = _start(e)
        cur = _new(s, e, "flip", p, a, b, e["szi_after"])
    elif kind in ("increase", "decrease"):
        if ep is None or ep["direction"] != direction(e["szi_after"]):
            if ep:
                ended = _end(s, ep, e, "closed")
            # the position existed before anything we ingested: its start is unknown
            ep = _new(s, e, "reconstructed", "unknown", None, e["observed_at"],
                      e["szi_before"])
        cur = _resize(s, ep, e)
    else:  # close
        if ep is None:
            ep = _new(s, e, "reconstructed", "unknown", None, e["window_start"] or e["observed_at"], e["szi_before"])
        cur = ended = _end(s, ep, e, "closed")
    s.execute(text("UPDATE source_event SET episode_id = :ep, processed_at = now() WHERE id = :i"),
              {"ep": cur["id"], "i": e["id"]})
    return Change(e, cur, ended if ended is not None and ended["id"] != cur["id"] else None)


def pending(s: Session, limit: int) -> list[dict]:
    rows = s.execute(text("""
        SELECT se.*, t.trader_key FROM source_event se JOIN trader t ON t.id = se.trader_id
        WHERE se.processed_at IS NULL ORDER BY se.id LIMIT :n FOR UPDATE OF se SKIP LOCKED"""), {"n": limit}
    ).mappings().all()
    return [dict(r) for r in rows]
