"""Robustness: perturbation grids, regime splits, edge-vs-model-uncertainty."""
import numpy as np
import pandas as pd

from .bootstrap import bootstrap_mean


def regime_tags(features, ctx, chain):
    """Per-day regime labels. Medians are taken over the sample, so these are DESCRIPTIVE splits of results,
    never inputs to trading decisions."""
    day = features.index.normalize()
    g = features.groupby(day)
    vix_open = g["vix"].first()
    vix_prev_close = g["vix"].last().shift(1)
    gap = ctx.groupby(day)["gap"].first().abs()
    rv = ctx.groupby(day)["rv30"].mean()
    logf = np.log(features["F"])
    eff = (g["F"].last().apply(np.log) - g["F"].first().apply(np.log)).abs() / \
        logf.groupby(day).diff().abs().groupby(day).sum()
    # dealer gamma sign at the open: OI-weighted gamma, calls positive / puts negative (standard but assumed convention)
    first_ts = g.apply(lambda d: d.index[0])
    gex = {}
    for d, ts in first_ts.items():
        c = chain[chain.timestamp == ts]
        c = c[c.gamma.notna() & c.oi.notna()]
        gex[d] = float((c.cp * c.gamma * c.oi * c.F ** 2).sum()) if len(c) else np.nan
    tags = pd.DataFrame({
        "vix_regime": np.where(vix_open > vix_open.median(), "high_vix", "low_vix"),
        "vix_dir": np.where(vix_open >= vix_prev_close, "rising_vix", "falling_vix"),
        "gap": np.where(gap > gap.median(), "large_gap", "small_gap"),
        "rv_regime": np.where(rv > rv.median(), "high_rv", "low_rv"),
        "trend": np.where(eff > eff.median(), "trending", "mean_reverting"),
        "gamma": np.where(pd.Series(gex) > 0, "pos_gamma", "neg_gamma"),
        "expiry_day": "expiry_day",
    }, index=vix_open.index)
    return tags


def regime_table(trades, tags):
    """Net P&L / trade count / hit-rate per regime label. A strategy earning everything in one slice is not robust."""
    if not len(trades):
        return pd.DataFrame()
    t = trades.merge(tags, left_on="day", right_index=True, how="left")
    rows = []
    for col in tags.columns:
        for lab, g in t.groupby(col):
            rows.append(dict(regime=col, label=lab, trades=len(g), days=g.day.nunique(), net=g.net_pnl.sum(),
                             avg=g.net_pnl.mean(), hit=(g.net_pnl > 0).mean()))
    return pd.DataFrame(rows)


def perturbation_table(run_fn, base, grids):
    """run_fn(params)->dict(total_net, net_sharpe, n_trades). grids: {param: [values]} perturbed one at a time."""
    rows = [dict(param="base", value="", **run_fn(base))]
    for k, vals in grids.items():
        for v in vals:
            p = {**base, k: v}
            rows.append(dict(param=k, value=v, **run_fn(p)))
    return pd.DataFrame(rows)


def robustness_score(table):
    """0..1. Half = share of perturbations that keep the sign of base net P&L; half = gradual degradation
    (worst perturbed / base, clipped). Collapse or sign flips under small perturbations -> low score."""
    base = table.iloc[0]["total_net"]
    pert = table.iloc[1:]
    if not len(pert) or not np.isfinite(base):
        return np.nan
    if base <= 0:
        return 0.0
    keep = float((pert.total_net > 0).mean())
    worst = float(np.clip(pert.total_net.min() / base, 0.0, 1.0))
    return 0.5 * keep + 0.5 * worst


def edge_vs_uncertainty(trades, model_rmse_vol, avg_abs_vega):
    """ObservedEdge = mean net P&L per trade (gross - hedge cost - spread - slippage - fees).
    ModelUncertainty = 95% bootstrap half-width of that mean + fair-smile model error x position vega (Rs per vol unit).
    Interesting only if ObservedEdge > ModelUncertainty."""
    if not len(trades):
        return dict(observed_edge=np.nan, model_uncertainty=np.nan, ratio=np.nan, passes=False)
    b = bootstrap_mean(trades.net_pnl.values)
    est_unc = (b["hi"] - b["lo"]) / 2
    model_unc = model_rmse_vol * avg_abs_vega if np.isfinite(model_rmse_vol) and np.isfinite(avg_abs_vega) else 0.0
    unc = est_unc + model_unc
    return dict(observed_edge=b["mean"], model_uncertainty=unc, ratio=b["mean"] / unc if unc > 0 else np.nan,
                passes=bool(b["mean"] > unc))
