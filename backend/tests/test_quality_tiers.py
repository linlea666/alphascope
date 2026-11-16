import json
from decimal import Decimal

from app.quant import quality as q
from app.sources.leaderboard import LeaderboardEntry, parse_leaderboard
from app.tiering import tier_for
from tests.conftest import load_fixture


def entry(av, month_vlm=1, roi=0.1, alltime_pnl=10, alltime_vlm=100):
    w = {k: (Decimal(1), Decimal(str(roi)), Decimal(month_vlm)) for k in ("day", "week", "month")}
    w["alltime"] = (Decimal(alltime_pnl), Decimal(str(roi)), Decimal(alltime_vlm))
    return LeaderboardEntry("0x" + "a" * 40, None, Decimal(str(av)), w)


def test_flag_rules():
    assert q.ZERO_ACCOUNT in q.leaderboard_flags(entry(0))
    assert q.DUST_ACCOUNT in q.leaderboard_flags(entry(50))
    assert q.INACTIVE_30D in q.leaderboard_flags(entry(5000, month_vlm=0))
    assert q.SMALL_DENOMINATOR_ROI in q.leaderboard_flags(entry(500, roi=3))
    assert q.SMALL_DENOMINATOR_ROI not in q.leaderboard_flags(entry(50_000, roi=3))
    assert q.EXTREME_ROI in q.leaderboard_flags(entry(50_000, roi=11))
    assert q.PASSIVE_PNL in q.leaderboard_flags(entry(5000, alltime_vlm=0))
    assert q.leaderboard_flags(entry(50_000)) == []


def test_flags_on_real_rows_are_consistent():
    doc = load_fixture("leaderboard_sample.json")
    for e in parse_leaderboard(json.dumps(doc).encode()).entries:
        flags = q.leaderboard_flags(e)
        assert (q.ZERO_ACCOUNT in flags) == (e.account_value <= 0)
        assert (q.INACTIVE_30D in flags) == (e.windows["month"][2] == 0)


def test_tier_rules():
    assert tier_for(50, 0) == "Z"
    assert tier_for(50, 10) == "C"
    assert tier_for(5_000, 0) == "C"
    assert tier_for(5_000, 10) == "B"
    assert tier_for(999, 10) == "C"
