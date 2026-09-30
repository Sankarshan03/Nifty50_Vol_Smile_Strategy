"""Predictability / mean-reversion statistics: AR(1), half-life, ADF, predictive regression, IC."""
import numpy as np
import pandas as pd
from scipy import stats


def ols_cluster(y, x, groups):
    """y = a + b x + e with day-clustered standard errors."""
    y, x, groups = np.asarray(y, float), np.asarray(x, float), np.asarray(groups)
    m = np.isfinite(y) & np.isfinite(x)
    y, x, g = y[m], x[m], groups[m]
    n = len(y)
    if n < 30 or np.std(x) == 0:
        return dict(alpha=np.nan, beta=np.nan, se=np.nan, t=np.nan, p=np.nan, n=n)
    X = np.c_[np.ones(n), x]
    XtXi = np.linalg.inv(X.T @ X)
    b = XtXi @ X.T @ y
    e = y - X @ b
    meat = np.zeros((2, 2))
    ug = np.unique(g)
    for gg in ug:
        s = X[g == gg].T @ e[g == gg]
        meat += np.outer(s, s)
    V = XtXi @ meat @ XtXi * (len(ug) / max(len(ug) - 1, 1))
    se = float(np.sqrt(V[1, 1]))
    t = b[1] / se if se > 0 else np.nan
    return dict(alpha=float(b[0]), beta=float(b[1]), se=se, t=float(t),
                p=float(2 * (1 - stats.norm.cdf(abs(t)))), n=n)


def ar1(resid, day):
    """Pooled within-day AR(1): r_t = c + phi r_{t-1}. Returns phi, half-life (steps), Dickey-Fuller t."""
    lag = resid.groupby(day).shift(1)
    m = (resid.notna() & lag.notna()).values
    if m.sum() < 30:
        return dict(phi=np.nan, half_life_steps=np.nan, adf_t=np.nan, adf_reject_5pct=False)
    y, x = resid.values[m], lag.values[m]
    n = len(y)
    X = np.c_[np.ones(n), x]
    bb = np.linalg.lstsq(X, y, rcond=None)[0]
    phi = bb[1]
    hl = -np.log(2) / np.log(phi) if 0 < phi < 1 else (np.inf if phi >= 1 else 0.0)
    dy = y - x
    b = np.linalg.lstsq(X, dy, rcond=None)[0]
    e = dy - X @ b
    se = np.sqrt(e @ e / (n - 2) * np.linalg.inv(X.T @ X)[1, 1])
    adf_t = b[1] / se
    # DF critical value with constant, 5%: -2.86 (no augmentation; panel of within-day segments)
    return dict(phi=float(phi), half_life_steps=float(hl), adf_t=float(adf_t), adf_reject_5pct=bool(adf_t < -2.86))


def future_change(series, h_steps):
    """factor_{t+h} - factor_t, strictly within the same day (no overnight jumps)."""
    return series.groupby(series.index.normalize()).shift(-h_steps) - series


def information_coefficient(signal, future, day, n_boot=300, seed=0):
    """Spearman IC with day-block bootstrap 95% CI and normal-approx p-value."""
    df = pd.DataFrame({"s": np.asarray(signal), "f": np.asarray(future), "d": np.asarray(day)}).dropna()
    if len(df) < 40 or df.d.nunique() < 3:
        return dict(ic=np.nan, lo=np.nan, hi=np.nan, n=len(df), p=np.nan)
    ic = stats.spearmanr(df.s, df.f)[0]
    groups = [g for _, g in df.groupby("d")]
    rng = np.random.default_rng(seed)
    bs = []
    for _ in range(n_boot):
        b = pd.concat([groups[i] for i in rng.integers(0, len(groups), len(groups))])
        bs.append(stats.spearmanr(b.s, b.f)[0])
    lo, hi = np.nanpercentile(bs, [2.5, 97.5])
    se = np.nanstd(bs)
    p = 2 * (1 - stats.norm.cdf(abs(ic) / se)) if se > 0 else np.nan
    return dict(ic=float(ic), lo=float(lo), hi=float(hi), n=len(df), p=float(p))


def predictability_table(resid_df, obs_df, freq_min, horizons_min=(5, 10, 15, 30, 60)):
    """For every factor and horizon: regressions, IC, AR(1)/half-life. One row per (factor, h)."""
    day = resid_df.index.normalize()
    rows = []
    for c in resid_df.columns:
        if resid_df[c].notna().sum() < 60:
            continue
        a = ar1(resid_df[c], day)
        for hm in horizons_min:
            if hm < freq_min:
                continue
            h = int(round(hm / freq_min))
            dchg = future_change(obs_df[c], h)
            reg = ols_cluster(dchg.values, resid_df[c].values, day.values)
            ic = information_coefficient(resid_df[c], dchg, day)
            reg2 = ols_cluster(resid_df[c].groupby(day).shift(-h).values, resid_df[c].values, day.values)
            rows.append(dict(factor=c, h_min=hm, beta_dchange=reg["beta"], t_dchange=reg["t"], p_dchange=reg["p"],
                             beta_resid=reg2["beta"], t_resid=reg2["t"], ic=ic["ic"], ic_lo=ic["lo"], ic_hi=ic["hi"],
                             ic_p=ic["p"], n=ic["n"], phi=a["phi"], half_life_min=a["half_life_steps"] * freq_min,
                             adf_t=a["adf_t"], adf_reject=a["adf_reject_5pct"]))
    return pd.DataFrame(rows)
