"""Forward-price estimation: futures, put-call parity, and a stable blend."""
import numpy as np
import pandas as pd


def forward_from_futures(fut_bid=None, fut_ask=None, fut_ltp=None):
    ok = lambda v: v is not None and np.isfinite(v) and v > 0
    if ok(fut_bid) and ok(fut_ask) and fut_ask >= fut_bid:
        return 0.5 * (fut_bid + fut_ask)
    if ok(fut_ltp):
        return float(fut_ltp)
    return np.nan


def _weighted_median(x, w):
    o = np.argsort(x)
    x, w = x[o], w[o]
    c = np.cumsum(w)
    return float(x[min(np.searchsorted(c, 0.5 * c[-1]), len(x) - 1)])


def forward_from_pcp(chain, T, r=0.0, ref=None, n_strikes=7):
    """F ~ K + e^{rT}(C-P) from the tightest-spread strikes nearest `ref`.

    `chain` needs columns strike, cp (+1/-1), bid, ask (clean quotes only).
    Aggregation is a spread-weighted median (robust to a stray bad strike).
    """
    if chain.empty:
        return np.nan
    c = chain[chain.cp > 0].drop_duplicates("strike").set_index("strike")
    p = chain[chain.cp < 0].drop_duplicates("strike").set_index("strike")
    common = c.index.intersection(p.index)
    if len(common) < 2:
        return np.nan
    c, p = c.loc[common], p.loc[common]
    est = pd.Series(common.values + np.exp(r * T) * (0.5 * (c.bid + c.ask).values - 0.5 * (p.bid + p.ask).values),
                    index=common)
    spr = pd.Series((c.ask - c.bid).values + (p.ask - p.bid).values, index=common)
    if ref is not None and np.isfinite(ref):
        near = pd.Series(np.abs(common.values - ref), index=common).nsmallest(max(n_strikes * 2, 3)).index
        est, spr = est.loc[near], spr.loc[near]
    keep = spr.nsmallest(min(n_strikes, len(spr))).index
    return _weighted_median(est.loc[keep].values, 1.0 / np.maximum(spr.loc[keep].values, 1e-6))


def stable_forward(f_fut, f_pcp, spot, T, r=0.0, pcp_weight=0.7, max_rel_gap=0.0025):
    """Blend futures and PCP forward; fall back sensibly. Returns (F, source)."""
    ok_f, ok_p = np.isfinite(f_fut), np.isfinite(f_pcp)
    if ok_f and ok_p:
        if abs(f_fut - f_pcp) / f_fut <= max_rel_gap:
            return pcp_weight * f_pcp + (1 - pcp_weight) * f_fut, "blend"
        return f_pcp, "pcp_outlier_fut"
    if ok_p:
        return f_pcp, "pcp"
    if ok_f:
        return f_fut, "fut"
    return spot * np.exp(r * T), "spot"
