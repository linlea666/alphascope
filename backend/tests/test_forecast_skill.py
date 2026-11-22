import numpy as np

from app.research.forward import skill


def overlapping(rng, n, horizon, shift):
    # each loss differential averages the shocks of the hours it spans, like hourly 24h forecasts
    shocks = rng.normal(0, 1, n + horizon)
    return [(shift + shocks[i : i + horizon].mean(), 0.0) for i in range(n)]


def test_needs_eight_blocks_of_two_horizons():
    assert skill([(0.5, 0.6)] * (8 * 48 - 1), 24)["p"] is None
    assert skill([(0.5, 0.6)] * (8 * 48), 24)["blocks"] == 8
    assert skill([], 1)["p"] is None and skill([], 1)["mean_diff"] is None


def test_better_model_is_significant_and_equal_one_is_not():
    rng = np.random.default_rng(5)
    better = skill([(0.60 + e, 0.65) for e in rng.normal(0, 0.05, 400)], 1)
    assert better["mean_diff"] < 0 and better["p"] < 0.001
    same = skill([(0.65 + e, 0.65) for e in rng.normal(0, 0.05, 400)], 1)
    assert same["p"] > 0.01


def test_overlapping_forecasts_keep_the_nominal_level():
    # a naive t-test on 600 overlapping points rejects far too often; the block test stays near 5%
    false = sum(skill(overlapping(np.random.default_rng(k), 600, 24, 0.0), 24)["p"] < 0.05 for k in range(300))
    assert false < 27
    assert skill(overlapping(np.random.default_rng(1), 600, 24, -0.6), 24)["p"] < 0.05
