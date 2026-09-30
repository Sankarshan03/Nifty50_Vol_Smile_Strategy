"""Bootstrap confidence intervals on day-level P&L (block = day, preserves intraday dependence)."""
import numpy as np


def bootstrap_sharpe(daily, ann=248, n=2000, seed=0):
    x = np.asarray(daily, float)
    if len(x) < 5 or x.std(ddof=1) == 0:
        return dict(sharpe=np.nan, lo=np.nan, hi=np.nan, p_le_zero=np.nan)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(x), (n, len(x)))
    b = x[idx]
    sr = b.mean(1) / np.where(b.std(1, ddof=1) > 0, b.std(1, ddof=1), np.nan) * np.sqrt(ann)
    return dict(sharpe=float(x.mean() / x.std(ddof=1) * np.sqrt(ann)), lo=float(np.nanpercentile(sr, 2.5)),
                hi=float(np.nanpercentile(sr, 97.5)), p_le_zero=float(np.nanmean(sr <= 0)))


def bootstrap_mean(x, n=2000, seed=0):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return dict(mean=np.nan, lo=np.nan, hi=np.nan)
    rng = np.random.default_rng(seed)
    m = x[rng.integers(0, len(x), (n, len(x)))].mean(1)
    return dict(mean=float(x.mean()), lo=float(np.percentile(m, 2.5)), hi=float(np.percentile(m, 97.5)))
