"""Turn residuals into hypothesis signals (z-scores scaled by an EXPANDING, past-only residual volatility)."""
import numpy as np
import pandas as pd

HYPOTHESES = {
    "H1_atm": "atm_iv",
    "H2_skew": "put_skew25",
    "H2c_skew": "call_skew25",
    "H3_wing": "wing_spread",
    "H4_curv": "bf25",
}


def expanding_z(resid, min_periods=30, lag=1):
    """z_t = resid_t / std(resid_{<t}): the scale never includes the current or future observations."""
    sd = resid.expanding(min_periods=min_periods).std().shift(lag)
    return resid / sd.replace(0, np.nan)


def hypothesis_signals(resid_df, min_periods=30):
    z = pd.DataFrame(index=resid_df.index)
    for h, col in HYPOTHESES.items():
        if col in resid_df:
            z[h] = expanding_z(resid_df[col], min_periods)
    return z


def pillar_rv_signal(features, model, min_periods=30):
    """Cross-strike RV: residual of each delta pillar vs neighbour-implied fair; score = z(rich) - z(cheap)."""
    zs = {}
    for n in ["p10", "p25", "atm", "c25", "c10"]:
        xs, ob = features.get(f"xs_{model}_{n}"), features.get(f"{model}_{n}")
        if xs is None or ob is None:
            return pd.DataFrame(index=features.index)
        zs[n] = expanding_z(ob - xs, min_periods)
    Z = pd.DataFrame(zs)
    valid = Z.notna().all(axis=1)
    return pd.DataFrame({"rv_rich": Z.idxmax(axis=1).where(valid), "rv_cheap": Z.idxmin(axis=1).where(valid),
                         "H6_rv": (Z.max(axis=1) - Z.min(axis=1)).where(valid)}, index=features.index)


def pca_factor_signals(scores, min_periods=30, window=24):
    """Hypothesis 5: z-score of a PCA smile factor vs its own trailing mean (the mean-reversion target)."""
    out = {}
    for c in scores:
        dev = scores[c] - scores[c].rolling(window, min_periods=8).mean().shift(1)
        out[f"H5_{c}"] = expanding_z(dev, min_periods)
    return pd.DataFrame(out, index=scores.index)
