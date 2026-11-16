"""Resource tiers decide how much API budget a wallet gets. Rules are versioned; history is kept.

Z cold   : account < $100 and no 30d volume -> leaderboard rows only
C basic  : everything else                    -> leaderboard history only
B screen : 30d volume > 0 and account >= $1k  -> clearinghouse + portfolio enrichment
A deep / S live are assigned from screening scores and watchlists (P1+), never from raw ROI.
"""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

TIER_RULE_VERSION = "t1"

TIER_SQL = """
CASE
  WHEN coalesce(account_value, 0) < 100 AND coalesce(month_vlm, 0) = 0 THEN 'Z'
  WHEN coalesce(month_vlm, 0) > 0 AND coalesce(account_value, 0) >= 1000 THEN 'B'
  ELSE 'C'
END
"""


def tier_for(account_value: float, month_vlm: float) -> str:
    """Python mirror of TIER_SQL (used by tests and docs)."""
    if account_value < 100 and month_vlm == 0:
        return "Z"
    if month_vlm > 0 and account_value >= 1000:
        return "B"
    return "C"


def recompute_tiers(session: Session, now: datetime) -> dict[str, int]:
    """Recompute tiers for wallets present on the latest leaderboard; log every change."""
    changed = session.execute(
        text(f"""
        WITH calc AS (
          SELECT id, tier AS old_tier, {TIER_SQL} AS new_tier
          FROM wallet WHERE present_in_latest
        ), upd AS (
          UPDATE wallet w SET tier = c.new_tier, tier_rule_version = :ver, tier_updated_at = :now
          FROM calc c
          WHERE w.id = c.id AND (c.old_tier IS DISTINCT FROM c.new_tier OR w.tier_rule_version IS DISTINCT FROM :ver)
          RETURNING w.id, c.old_tier, c.new_tier
        )
        INSERT INTO wallet_tier_history (wallet_id, tier, previous_tier, rule_version, assigned_at)
        SELECT id, new_tier, old_tier, :ver, :now FROM upd WHERE old_tier IS DISTINCT FROM new_tier
        RETURNING 1
        """),
        {"ver": TIER_RULE_VERSION, "now": now},
    ).rowcount
    dist = dict(session.execute(
        text("SELECT tier, count(*) FROM wallet WHERE present_in_latest GROUP BY tier")).all())
    return {"changed": changed, **{f"tier_{k}": v for k, v in dist.items()}}
