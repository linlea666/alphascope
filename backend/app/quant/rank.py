"""Rank statistics shared by the score check and the factor screen."""

import numpy as np


def ranks(x: np.ndarray) -> np.ndarray:
    order = x.argsort(kind="mergesort")
    r = np.empty(len(x))
    r[order] = np.arange(len(x))
    _, inv, counts = np.unique(x, return_inverse=True, return_counts=True)
    return (np.bincount(inv, r) / counts)[inv]  # ties share their mean rank


def spearman(a, b) -> float | None:
    ra, rb = ranks(np.asarray(a, float)), ranks(np.asarray(b, float))
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])
