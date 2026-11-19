"""Pre-registered forward studies. Only settled, prospectively recorded observations enter tests."""

import math
from datetime import datetime, timedelta
from statistics import median

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.models import ResearchEvaluation, ResearchExperiment, ResearchForecast, ResearchFrame, ResearchNotice
from app.research.timing import benjamini_hochberg

HORIZONS = (1, 4, 24)
MODEL = "momentum-analogs-v1"
CLASSES = ("down", "flat", "up")
HYPOTHESES = {
    "broad_long": "Broad increases in observed long sizes precede higher next-day returns than concentrated additions.",
    "short_cover": "Following a rise, long accumulation precedes higher next-day returns than shrinking shorts.",
    "eth_specialist": "Positive ETH specialist divergence precedes higher BTC-adjusted ETH returns at comparable volatility.",
}


def history(s, coin, cutoff):
    rows = s.execute(
        text("""
        SELECT t,c FROM market_candle WHERE source='hyperliquid' AND interval='1h' AND coin=:c AND c>0
        AND t>=:a AND t+interval '1 hour'<=:t AND fetched_at<=:t ORDER BY t
    """),
        dict(c=coin, a=cutoff - timedelta(days=120), t=cutoff),
    ).all()
    return {t + timedelta(hours=1): float(c) for t, c in rows}


def classify(r, band):
    return 2 if r > band else 0 if r < -band else 1


def frequencies(labels):
    return [(labels.count(i) + 1) / (len(labels) + 3) for i in range(3)]


def probabilities(prices, cutoff, horizon):
    """Conditional nearest historical 4h momentum vs the same sample's unconditional frequency."""
    h = timedelta(hours=horizon)
    four = timedelta(hours=4)
    prices = {t: px for t, px in prices.items() if t <= cutoff and px > 0}
    if not prices:
        return None
    last = max(prices)
    if cutoff - last > timedelta(hours=2) or last - four not in prices:
        return None
    sample = [
        (px / prices[t - four] - 1, prices[t + h] / px - 1)
        for t, px in prices.items()
        if t - four in prices and t + h in prices
    ]
    if len(sample) < 90:
        return None
    band = max(0.0001, median(abs(y) for _, y in sample) * 0.5)
    momentum = prices[last] / prices[last - four] - 1
    neighbors = sorted(sample, key=lambda pair: abs(pair[0] - momentum))[: max(30, len(sample) // 10)]
    return {
        "p": frequencies([classify(y, band) for _, y in neighbors]),
        "baseline": frequencies([classify(y, band) for _, y in sample]),
        "band": band,
        "momentum_4h": momentum,
        "train_n": len(sample),
        "analog_n": len(neighbors),
        "feature_time": last.isoformat(),
        "classes": list(CLASSES),
    }


def register(s, coin):
    for key, hypothesis in HYPOTHESES.items():
        if key == "eth_specialist" and coin != "ETH":
            continue
        spec = {
            "hypothesis": hypothesis,
            "horizon_hours": 24,
            "primary": "matched_mean_forward_return",
            "control": "same coin, exchange, prior momentum sign and volatility band; earlier unused control",
            "data": "frozen hourly HL observations; fixed consecutive-frame wallet intersection",
            "test": "non-overlapping 24h labels; weekly block sign permutation; BH across all registered tests",
            "min_pairs": 30,
            "min_weeks": 8,
            "promotion": "human_review",
            "feature": key,
        }
        s.execute(
            insert(ResearchExperiment)
            .values(coin=coin, hypothesis=key, version=1, specification=spec)
            .on_conflict_do_nothing()
        )


def issue(s, frame, now):
    mark = frame.context.get("fields", {}).get("mark")
    if not mark or now - frame.observed_at > timedelta(minutes=10):
        return
    prices = history(s, frame.coin, now)
    for horizon in HORIZONS:
        evidence = probabilities(prices, now, horizon)
        if evidence is None:
            continue
        evidence.update(
            mark=mark,
            divergence=frame.metrics["divergence"],
            delta=frame.metrics["delta"],
            evidence_frame=frame.id,
            trigger="completed_4h_momentum",
            invalidation="stale_observation_or_missing_settlement",
            status="shadow",
        )
        s.execute(
            insert(ResearchForecast)
            .values(
                coin=frame.coin,
                model=MODEL,
                horizon=horizon,
                issued_at=now,
                cutoff=now,
                due_at=now + timedelta(hours=horizon),
                frame_id=frame.id,
                evidence=evidence,
            )
            .on_conflict_do_nothing()
        )


def settle(s, coin, now):
    forecasts = s.scalars(
        select(ResearchForecast)
        .where(ResearchForecast.coin == coin, ResearchForecast.outcome.is_(None), ResearchForecast.due_at <= now)
        .with_for_update(skip_locked=True)
    ).all()
    notices = s.scalars(
        select(ResearchNotice)
        .join(ResearchFrame)
        .where(ResearchFrame.coin == coin, ResearchNotice.outcome.is_(None), ResearchNotice.due_at <= now)
        .with_for_update(of=ResearchNotice, skip_locked=True)
    ).all()
    for row in [*forecasts, *notices]:
        frame = s.get(ResearchFrame, row.frame_id)
        # Outcome price is the nearest observed venue mark within five minutes of the due time.
        observation = s.execute(
            text("""
            SELECT id, observed_at, (context->'fields'->>'mark')::float px FROM research_frame
            WHERE coin=:coin AND venue=:venue AND observed_at>=:start AND observed_at<=:end
            AND available_at<=:now AND (context->'fields'->>'mark')::float>0 ORDER BY abs(extract(epoch from observed_at-:due)),observed_at LIMIT 1
        """),
            dict(
                coin=coin,
                venue=frame.venue,
                due=row.due_at,
                start=row.due_at - timedelta(minutes=5),
                end=row.due_at + timedelta(minutes=5),
                now=now,
            ),
        ).first()
        initial = frame.context.get("fields", {}).get("mark")
        if now < row.due_at + timedelta(minutes=5):
            continue
        if not observation or not initial:
            if now >= row.due_at + timedelta(minutes=5):
                row.outcome = {"status": "missing", "evaluated_at": now.isoformat()}
            continue
        ret = observation.px / initial - 1
        out = {
            "status": "settled",
            "ret": ret,
            "frame": observation.id,
            "observed_at": observation.observed_at.isoformat(),
            "evaluated_at": now.isoformat(),
        }
        if isinstance(row, ResearchForecast):
            label = classify(ret, row.evidence["band"])
            out["class"] = CLASSES[label]
            for name in ("p", "baseline"):
                p = row.evidence[name]
                out[name + "_brier"] = sum((v - int(i == label)) ** 2 for i, v in enumerate(p))
                out[name + "_log_loss"] = -math.log(p[label])
        row.outcome = out


def treatment(key, e):
    d = e["delta"]
    if key == "eth_specialist":
        x = e["divergence"]
        return None if x is None or x == 0 else x > 0
    if not d["common_n"] or d["largest_share"] is None:
        return None
    if key == "broad_long":
        if d["long_change"] <= 0:
            return None
        if d["buy_breadth"] > 0.5 and d["largest_share"] < 0.5:
            return True
        return False if d["largest_share"] >= 0.5 else None
    if e["momentum_4h"] <= 0 or d["net_units"] <= 0:
        return None
    return d["long_change"] > max(0, -d["short_change"])


def paired_test(rows, key, btc_returns):
    controls, pairs = [], []
    next_open = None
    for row in rows:
        if next_open and row.issued_at < next_open:
            continue
        flag = treatment(key, row.evidence)
        if flag is None:
            continue
        value = row.outcome["ret"]
        if key == "eth_specialist":
            btc = btc_returns.get(row.issued_at.replace(minute=0, second=0, microsecond=0))
            if btc is None:
                continue
            value -= btc
        next_open = datetime.fromisoformat(row.outcome["observed_at"])
        signature = row.evidence["momentum_4h"] > 0
        if not flag:
            controls.append((signature, row.evidence["band"], value))
            continue
        choices = [
            (abs(math.log(band / row.evidence["band"])), i)
            for i, (sign, band, _) in enumerate(controls)
            if sign == signature and 0.5 <= band / row.evidence["band"] <= 2
        ]
        if choices:
            _, idx = min(choices)
            _, _, control = controls.pop(idx)
            pairs.append((row.issued_at.date().toordinal() // 7, value - control))
    weeks = {}
    for w, diff in pairs:
        weeks[w] = weeks.get(w, 0.0) + diff
    result = {
        "status": "insufficient",
        "pairs": len(pairs),
        "weeks": len(weeks),
        "p": 1.0,
        "effect": sum(d for _, d in pairs) / len(pairs) if pairs else None,
    }
    if len(pairs) >= 30 and len(weeks) >= 8:
        values = np.array(list(weeks.values()))
        signs = np.random.default_rng(17).choice([-1, 1], size=(4096, len(values)))
        result.update(status="evaluated", p=float((1 + np.count_nonzero((signs @ values) >= values.sum())) / 4097))
    return result


def evaluate(s, now):
    cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    studies = list(
        s.scalars(
            select(ResearchExperiment)
            .where(ResearchExperiment.registered_at < cutoff)
            .order_by(ResearchExperiment.id)
        )
    )
    rows = list(
        s.scalars(
            select(ResearchForecast)
            .where(
                ResearchForecast.horizon == 24,
                ResearchForecast.model == MODEL,
                ResearchForecast.due_at <= cutoff,
                ResearchForecast.outcome.is_not(None),
            )
            .order_by(ResearchForecast.issued_at)
        )
    )
    rows = [
        r
        for r in rows
        if r.outcome.get("status") == "settled" and datetime.fromisoformat(r.outcome["evaluated_at"]) <= cutoff
    ]
    btc = {
        r.issued_at.replace(minute=0, second=0, microsecond=0): r.outcome["ret"] for r in rows if r.coin == "BTC"
    }
    results = [
        paired_test([r for r in rows if r.coin == st.coin and r.issued_at >= st.registered_at], st.hypothesis, btc)
        for st in studies
    ]
    for study, result, q in zip(studies, results, benjamini_hochberg([r["p"] for r in results])):
        result.update(
            q=float(q), review_eligible=result["status"] == "evaluated" and q < 0.05 and result["effect"] > 0
        )
        s.execute(
            insert(ResearchEvaluation)
            .values(experiment_id=study.id, cutoff=cutoff, result=result)
            .on_conflict_do_nothing()
        )
