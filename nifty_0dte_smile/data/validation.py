"""Strict option quote validation layer. Flags rows; never silently drops them."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

FLAG_COLS = ["f_nonpositive", "f_crossed", "f_duplicate", "f_stale", "f_zero_liq", "f_wide"]


@dataclass
class QuoteRules:
    max_spread_pct: float = 0.60     # reject spread/mid beyond this
    stale_snapshots: int = 12        # identical quote for N consecutive snapshots with no volume change
    min_mid: float = 0.10            # ignore sub-tick lottery tickets
    require_size: bool = True        # volume must be > 0 at some point in the day for that contract


def validate_quotes(df, rules=QuoteRules()):
    """Adds Mid, Spread, SpreadPct and boolean flags. `clean` == no flag set."""
    d = df.copy()
    d["mid"] = 0.5 * (d["bid"] + d["ask"])
    d["spread"] = d["ask"] - d["bid"]
    d["spread_pct"] = d["spread"] / d["mid"].where(d["mid"] > 0)

    d["f_nonpositive"] = ~((d["bid"] > 0) & (d["ask"] > 0)) | ~np.isfinite(d["bid"]) | ~np.isfinite(d["ask"])
    d["f_crossed"] = d["ask"] < d["bid"]
    d["f_duplicate"] = d.duplicated(["timestamp", "expiry", "strike", "cp"], keep="last")

    key = ["expiry", "strike", "cp"]
    d = d.sort_values(key + ["timestamp"])
    same = (d.groupby(key)["bid"].diff().eq(0) & d.groupby(key)["ask"].diff().eq(0)
            & (d.groupby(key)["volume"].diff().fillna(0) <= 0))
    grp = (~same).groupby([d[k] for k in key]).cumsum()
    run = same.groupby([d[k] for k in key] + [grp]).cumsum()
    d["f_stale"] = run >= rules.stale_snapshots

    traded = d.groupby(key)["volume"].transform("max").fillna(0) > 0
    d["f_zero_liq"] = (~traded) if rules.require_size else False
    d["f_wide"] = (d["spread_pct"] > rules.max_spread_pct) | (d["mid"] < rules.min_mid)
    d = d.sort_values(["timestamp", "expiry", "strike", "cp"]).reset_index(drop=True)
    d["clean"] = ~d[FLAG_COLS].any(axis=1)
    return d


def quote_report(d):
    """Counts per flag and overall retention."""
    rep = {c: int(d[c].sum()) for c in FLAG_COLS}
    rep["rows"] = int(len(d))
    rep["clean_rows"] = int(d["clean"].sum())
    rep["clean_frac"] = float(d["clean"].mean()) if len(d) else np.nan
    return pd.Series(rep)
