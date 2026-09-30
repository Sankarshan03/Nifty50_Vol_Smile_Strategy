"""Multiple-testing control and permutation tests."""
import numpy as np
import pandas as pd


def benjamini_hochberg(pvals, q=0.05):
    """Returns (adjusted p-values, reject mask) under BH false-discovery-rate control."""
    p = np.asarray(pvals, float)
    n = np.isfinite(p).sum()
    adj = np.full(p.shape, np.nan)
    if n == 0:
        return adj, np.zeros(p.shape, bool)
    idx = np.where(np.isfinite(p))[0]
    order = idx[np.argsort(p[idx])]
    ranked = p[order] * n / (np.arange(1, n + 1))
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    adj[order] = np.minimum(ranked, 1.0)
    return adj, adj <= q


def holm(pvals, alpha=0.05):
    p = np.asarray(pvals, float)
    order = np.argsort(p)
    m = len(p)
    adj = np.empty(m)
    run = 0.0
    for r, i in enumerate(order):
        run = max(run, min((m - r) * p[i], 1.0))
        adj[i] = run
    return adj, adj <= alpha


def permutation_ic_test(signal, future, day, n_perm=500, seed=0):
    """Null: within each day the signal is unrelated to the future move. Shuffle the signal WITHIN day
    (keeps intraday/time-of-day structure) and compare |IC| to the observed one."""
    df = pd.DataFrame({"s": np.asarray(signal), "f": np.asarray(future), "d": np.asarray(day)}).dropna()
    if len(df) < 40:
        return dict(ic=np.nan, p=np.nan)
    rng = np.random.default_rng(seed)
    obs = df.s.rank().corr(df.f.rank())
    groups = [g.index.values for _, g in df.groupby("d")]
    sr, fr = df.s.rank().values, df.f.rank().values
    pos = {ix: i for i, ix in enumerate(df.index.values)}
    cnt = 0
    for _ in range(n_perm):
        perm = sr.copy()
        for g in groups:
            ii = [pos[x] for x in g]
            perm[ii] = rng.permutation(sr[ii])
        c = np.corrcoef(perm, fr)[0, 1]
        cnt += abs(c) >= abs(obs)
    return dict(ic=float(obs), p=float((cnt + 1) / (n_perm + 1)))
