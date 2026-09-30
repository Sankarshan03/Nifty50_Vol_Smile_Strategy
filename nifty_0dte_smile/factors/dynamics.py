"""Intraday smile dynamics: changes, rolling stats, z-scores, percentiles (all causal)."""
import numpy as np
import pandas as pd


def add_dynamics(df, cols, window=24, min_periods=8):
    """Adds d_<col>, roll_mean_, roll_vol_, z_, pct_ (percentile within trailing window). Uses only the past."""
    out = {}
    day = df.index.normalize()
    for c in cols:
        x = df[c]
        out[f"d_{c}"] = x.groupby(day).diff()
        rm = x.rolling(window, min_periods=min_periods).mean()
        rs = x.rolling(window, min_periods=min_periods).std()
        out[f"roll_mean_{c}"] = rm
        out[f"roll_vol_{c}"] = rs
        out[f"z_{c}"] = (x - rm) / rs.replace(0, np.nan)
        out[f"pct_{c}"] = x.rolling(window, min_periods=min_periods).apply(lambda a: (a[:-1] < a[-1]).mean(), raw=True)
    return pd.DataFrame(out, index=df.index)
