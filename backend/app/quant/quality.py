"""Data-quality flags derived from a single leaderboard row (rule set v1).

Flags never delete data; they let rankings exclude rows whose official numbers are misleading.
Thresholds come from the 2026-11-15 leaderboard profile (see docs/00-feasibility.md).
"""

from decimal import Decimal

from app.sources.leaderboard import LeaderboardEntry

QUALITY_RULE_VERSION = "q1"

ZERO_ACCOUNT = "zero_account_value"
DUST_ACCOUNT = "dust_account"  # 0 < account value < $100
INACTIVE_30D = "inactive_30d"  # no volume in the month window
SMALL_DENOMINATOR_ROI = "small_denominator_roi"  # |ROI| > 100% on an account under $1k
EXTREME_ROI = "extreme_roi"  # ROI > 1000% in any window
PASSIVE_PNL = "passive_pnl"  # all-time PnL without any own trading volume (vault/transfer driven)

# Set from per-address API data, not from the leaderboard.
AGGREGATE_ACCOUNT = "aggregate_account"  # leaderboard value far above the address's own equity (sub-accounts/spot)

DEFAULT_RANKING_EXCLUSIONS = (ZERO_ACCOUNT, DUST_ACCOUNT, SMALL_DENOMINATOR_ROI, EXTREME_ROI, PASSIVE_PNL)

ALL_FLAGS = (ZERO_ACCOUNT, DUST_ACCOUNT, INACTIVE_30D, SMALL_DENOMINATOR_ROI, EXTREME_ROI, PASSIVE_PNL,
             AGGREGATE_ACCOUNT)

_DUST = Decimal(100)
_SMALL = Decimal(1000)
_ONE = Decimal(1)
_TEN = Decimal(10)


def leaderboard_flags(e: LeaderboardEntry) -> list[str]:
    av = e.account_value
    flags: list[str] = []
    if av <= 0:
        flags.append(ZERO_ACCOUNT)
    elif av < _DUST:
        flags.append(DUST_ACCOUNT)
    if e.windows["month"][2] == 0:
        flags.append(INACTIVE_30D)
    rois = [w[1] for w in e.windows.values()]
    if av < _SMALL and any(abs(r) > _ONE for r in rois):
        flags.append(SMALL_DENOMINATOR_ROI)
    if any(r > _TEN for r in rois):
        flags.append(EXTREME_ROI)
    pnl, _, vlm = e.windows["alltime"]
    if vlm == 0 and pnl != 0:
        flags.append(PASSIVE_PNL)
    return flags
