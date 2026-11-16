from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from app.research.positions import FillRow, reconstruct, scale_for

T0 = datetime(2026, 8, 1, tzinfo=UTC)
_tid = iter(range(1, 10_000))


def f(minutes: float, side: str, sz, px, start, pnl=0, fee=0) -> FillRow:
    return FillRow(T0 + timedelta(minutes=minutes), next(_tid), side, D(str(sz)), D(str(px)), D(str(start)),
                   D(str(pnl)), D(str(fee)))


def test_open_add_reduce_close_cycle():
    r = reconstruct([
        f(0, "B", 1, 100, 0, fee=0.1),
        f(60, "B", 1, 90, 1, fee=0.1),      # add (separate decision: > 15 min later)
        f(120, "A", 1, 120, 2, pnl=25),     # reduce
        f(180, "A", 1, 130, 1, pnl=35),     # close
    ])
    assert len(r.cycles) == 1 and r.breaks == 0
    c = r.cycles[0]
    assert c.direction == 1 and c.status == "closed" and c.complete
    assert c.entry_val / c.entry_sz == D(95) and c.exit_val / c.exit_sz == D(125)
    assert c.max_size == D(2) and c.realized_pnl == D(60) and c.n_fills == 4 and c.n_decisions == 4
    assert [d.kind for d in r.decisions] == ["open", "add", "reduce", "close"]
    assert c.holding_hours(T0) == 3.0


def test_split_fills_within_gap_are_one_decision():
    r = reconstruct([f(0, "B", 0.5, 100, 0), f(3, "B", 0.5, 101, 0.5), f(14, "B", 1, 102, 1)])
    assert len(r.decisions) == 1
    d = r.decisions[0]
    assert d.kind == "open" and d.n_fills == 3 and d.size == D(2) and d.pos_after == D(2)


def test_flip_closes_one_cycle_and_opens_the_next():
    r = reconstruct([f(0, "B", 2, 100, 0), f(600, "A", 5, 110, 2, pnl=20, fee=1)])
    assert [c.status for c in r.cycles] == ["flipped", "open"]
    long, short = r.cycles
    assert long.exit_sz == D(2) and long.realized_pnl == D(20) and long.fees == D("0.4")
    assert short.direction == -1 and short.entry_sz == D(3) and short.fees == D("0.6") and short.complete
    assert r.decisions[-1].kind == "flip"


def test_position_open_before_archive_is_incomplete():
    r = reconstruct([f(0, "A", 1, 100, 3, pnl=5), f(30, "A", 2, 101, 2, pnl=7)])
    c = r.cycles[0]
    assert not c.complete and c.status == "closed" and c.exit_sz == D(3)
    assert [d.kind for d in r.decisions] == ["reduce", "close"]


def test_chain_break_marks_cycle_incomplete():
    r = reconstruct([f(0, "B", 1, 100, 0), f(60, "B", 1, 100, 3)])  # 2 units bought where we cannot see
    assert r.breaks == 1 and not r.cycles[0].complete and len(r.cycles) == 1


def test_chain_break_through_flat_closes_the_old_cycle():
    r = reconstruct([f(0, "B", 1, 100, 0), f(600, "A", 1, 90, 0)])  # long closed unseen, then a new short
    assert r.breaks == 1
    assert [(c.direction, c.status, c.complete) for c in r.cycles] == [(1, "closed", False), (-1, "open", True)]


def test_scale_boundaries():
    assert scale_for(3.9) == "scalp" and scale_for(4) == "short"
    assert scale_for(71.9) == "short" and scale_for(72) == "swing"
    assert scale_for(503) == "swing" and scale_for(504) == "macro"


def test_same_timestamp_fills_are_chained_not_tid_ordered():
    # One taker order filled against three makers in the same millisecond; tids are not chronological.
    t = T0
    fills = [
        FillRow(t, 900, "A", D("20"), D(1), D("-2.5"), D(0), D(0)),
        FillRow(t, 100, "A", D("4.3"), D(1), D("-22.5"), D(0), D(0)),
        FillRow(t - timedelta(seconds=7), 500, "A", D("2.5"), D(1), D(0), D(0), D(0)),
        FillRow(t, 300, "A", D("12.5"), D(1), D("-26.8"), D(0), D(0)),
    ]
    r = reconstruct(fills)
    assert r.breaks == 0
    assert len(r.cycles) == 1 and r.cycles[0].max_size == D("39.3")


def test_rebuild_wallet_excludes_spot_and_uses_as_of(sf):
    from sqlalchemy import text

    from app.research.positions import rebuild_wallet

    with sf() as s:
        wid = s.execute(text("""INSERT INTO wallet (address, first_seen_at, first_seen_source, last_seen_at,
                                present_in_latest) VALUES (:a, now(), 't', now(), true) RETURNING id"""),
                        {"a": "0x" + "d" * 40}).scalar_one()
        fills = [("BTC", 0, "B", "1", "0"), ("BTC", 600, "A", "1", "1"), ("ETH", 700, "B", "2", "0"),
                 ("@142", 10, "B", "5", "0"), ("@142", 20, "B", "5", "4.99")]  # spot: fee-in-token break
        for i, (coin, m, side, sz, sp) in enumerate(fills):
            s.execute(text("""INSERT INTO fill (wallet_id, tid, side, time, coin, px, sz, dir, start_position,
                              closed_pnl, fee) VALUES (:w, :tid, :side, :t, :coin, 100, :sz, '', :sp, 0, 0)"""),
                      {"w": wid, "tid": i, "side": side, "t": T0 + timedelta(minutes=m), "coin": coin, "sz": sz,
                       "sp": sp})
        s.commit()
        as_of = T0 + timedelta(minutes=760)  # last archived fill + 1h: the ETH long is a data boundary
        r = rebuild_wallet(s, wid, as_of)
        s.commit()
        assert r["coins"] == 2 and r["breaks"] == 0  # spot excluded
        eth = s.execute(text("SELECT holding_hours, scale, status FROM position_cycle WHERE coin = 'ETH'")).one()
        assert float(eth.holding_hours) == 1.0 and eth.scale == "scalp" and eth.status == "open"
