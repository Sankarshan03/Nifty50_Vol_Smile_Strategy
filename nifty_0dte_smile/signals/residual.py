"""Fair-smile models and smile residuals: Residual = Observed - FairSmile. Every model is causal."""
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from ..smile.delta_surface import iv_at_delta
from ..smile.polynomial import fit_polynomial

FACTOR_COLS = ["atm_iv", "put_skew25", "call_skew25", "rr25", "wing_spread", "bf25", "bf10", "curv"]
XS_PILLARS = {"p10": -0.10, "p25": -0.25, "atm": 0.50, "c25": 0.25, "c10": 0.10}
XS_BAND = {"p10": 0.05, "p25": 0.07, "atm": 0.12, "c25": 0.07, "c10": 0.05}


# ---------- Model 2: cross-sectional (neighbour-based) fair value ----------
def cross_sectional_fair(pts, fit, F, T):
    """For each pillar delta, refit a polynomial with options near that delta held out and
    evaluate the hold-out fit at the pillar log-moneyness. Fair = what the neighbours imply."""
    out = {}
    for name, d in XS_PILLARS.items():
        _, k0, ext = iv_at_delta(fit, d)
        if not np.isfinite(k0):
            continue
        ref = 0.5 if name == "atm" else abs(d)
        keep = np.abs(np.abs(pts.delta.values) - ref) > XS_BAND[name]
        if keep.sum() < 6:
            continue
        w = 1.0 / np.maximum((pts.iv_ask - pts.iv_bid).fillna(0.02).values[keep], 0.002) ** 2
        m = fit_polynomial(pts.k.values[keep], pts.iv_mid.values[keep], T, w, degree=2)
        if m.ok and np.isfinite(m.iv(k0)):
            out[f"xs_{name}"] = float(m.iv(k0))
    return out


def xs_fair_table(features, model):
    """Fair values of derived factors from the neighbour-implied pillars of `model`'s fit."""
    if not all(f"xs_{model}_{n}" in features for n in XS_PILLARS):
        return pd.DataFrame(index=features.index, columns=FACTOR_COLS, dtype=float)
    g = lambda n: features[f"xs_{model}_{n}"]
    atm = g("atm")
    return pd.DataFrame({
        "atm_iv": atm,
        "put_skew25": g("p25") - atm, "call_skew25": g("c25") - atm,
        "rr25": g("c25") - g("p25"), "wing_spread": g("p25") - g("c25"),
        "bf25": 0.5 * (g("p25") + g("c25")) - atm, "bf10": 0.5 * (g("p10") + g("c10")) - atm,
        "curv": np.nan,
    }, index=features.index)


# ---------- context ----------
def infer_freq_minutes(index):
    d = pd.Series(index).diff().dt.total_seconds().div(60)
    return float(d[d > 0].median())


def context_frame(features, freq_min=None):
    """Causal context: VIX, tau, gamma proxy, past returns / realised vol, overnight gap."""
    freq_min = freq_min or infer_freq_minutes(features.index)
    day = features.index.normalize()
    logf = np.log(features["F"])
    r1 = logf.groupby(day).diff()
    steps30 = max(int(round(30 / freq_min)), 2)
    c = pd.DataFrame(index=features.index)
    c["vix"] = features["vix"]
    c["tau"] = features["T"]
    c["gamma_proxy"] = 1.0 / np.sqrt(features["T"])
    c["ret30"] = r1.groupby(day).rolling(steps30, min_periods=2).sum().droplevel(0).reindex(features.index)
    rv = (r1 ** 2).groupby(day).rolling(steps30, min_periods=3).mean().droplevel(0).reindex(features.index)
    c["rv30"] = np.sqrt(rv / (freq_min / (248 * 375)))
    first = features.groupby(day)["F"].transform("first")
    prev_close = features.groupby(day)["F"].last().shift(1).reindex(day).values
    c["gap"] = np.log(first / prev_close)
    return c.fillna({"gap": 0.0})


# ---------- Model 1: historical smile conditional on time-of-day (+VIX regime for ATM) ----------
def fair_historical(fdf, ctx, cols=FACTOR_COLS, bucket_min=15, min_days=2):
    """Expected factor = mean of the SAME time-of-day bucket on strictly earlier days.
    ATM is scaled by VIX (earlier-days ratio atm_iv/VIX) = volatility-regime conditioning."""
    day = fdf.index.normalize()
    b = (fdf.index.hour * 60 + fdf.index.minute) // bucket_min
    out = pd.DataFrame(index=fdf.index, columns=cols, dtype=float)
    base = fdf[cols].copy()
    base["_vix_ratio"] = fdf["atm_iv"] * 100.0 / ctx["vix"]
    cols2 = cols + ["_vix_ratio"]
    t = base[cols2].groupby([day, b]).mean()
    idx = pd.MultiIndex.from_arrays([day, b])
    fair = {}
    for c in cols2:
        w = t[c].unstack()
        fair[c] = w.expanding(min_periods=min_days).mean().shift(1).stack(dropna=False).reindex(idx).values
    for c in cols:
        out[c] = fair[c]
    out["atm_iv"] = fair["_vix_ratio"] * ctx["vix"].values / 100.0
    return out


# ---------- Models 3 and 4: dynamic factor model (ridge) / ML (GBM), walk-forward by day ----------
def _ml_design(fdf, ctx, col, freq_min):
    x = ctx[["vix", "tau", "gamma_proxy", "ret30", "rv30", "gap"]].copy()
    steps = max(int(round(60 / freq_min)), 1)
    # slow component: EWM of the factor lagged one hour, deliberately NOT the last observation,
    # so "fair" is not simply "previous value" (residuals would then be pure innovations).
    x["slow"] = fdf[col].ewm(halflife=max(int(round(60 / freq_min)), 2), min_periods=3).mean().shift(steps)
    return x


def fair_ml(fdf, ctx, cols=FACTOR_COLS, kind="ridge", min_days=3, freq_min=None):
    """Re-fit each day on all PREVIOUS days only, predict the current day."""
    freq_min = freq_min or infer_freq_minutes(fdf.index)
    day = fdf.index.normalize()
    days = sorted(day.unique())
    out = pd.DataFrame(index=fdf.index, columns=cols, dtype=float)
    for c in cols:
        X = _ml_design(fdf, ctx, c, freq_min)
        y = fdf[c]
        okx = X.notna().all(axis=1)
        for d in days[min_days:]:
            tr = (day < d) & y.notna() & okx
            te = (day == d) & okx
            if tr.sum() < 50 or te.sum() == 0:
                continue
            sc = StandardScaler().fit(X[tr])
            mdl = Ridge(alpha=10.0) if kind == "ridge" else HistGradientBoostingRegressor(
                max_iter=80, max_depth=3, learning_rate=0.05, random_state=0)
            mdl.fit(sc.transform(X[tr]), y[tr])
            out.loc[te, c] = mdl.predict(sc.transform(X[te]))
    return out


def residuals(observed, fair):
    cols = [c for c in fair.columns if c in observed.columns]
    return observed[cols] - fair[cols]
