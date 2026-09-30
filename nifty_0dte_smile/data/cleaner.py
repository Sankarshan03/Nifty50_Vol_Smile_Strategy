"""Raw -> cleaned observations. Both are kept; LTP is never used as the valuation price."""
import numpy as np
import pandas as pd

from ..options.iv import OK, STATUS_NAMES, implied_vol
from ..options.pricing import intrinsic
from .validation import QuoteRules, quote_report, validate_quotes


def clean_chain(raw, rules=QuoteRules()):
    """Returns (raw_flagged, cleaned)."""
    flagged = validate_quotes(raw, rules)
    return flagged, flagged[flagged["clean"]].copy()


def add_price_sanity(d, F, T, r=0.0):
    """Flag impossible prices (below intrinsic / above bound) and IV solver failures.

    `F`, `T` are per-row arrays aligned with d. Adds iv_bid/iv_mid/iv_ask, iv_status, f_iv_fail.
    """
    cp, K = d["cp"].values, d["strike"].values
    intr = intrinsic(F, K, cp, T, r)
    d["f_below_intrinsic"] = (d["mid"].values < intr - 1e-9) | (d["ask"].values < intr - 1e-9)
    for name, col in [("iv_bid", "bid"), ("iv_mid", "mid"), ("iv_ask", "ask")]:
        iv, st = implied_vol(d[col].values, F, K, T, cp, r)
        d[name] = iv
        if name == "iv_mid":
            d["iv_status"] = st
    d["f_iv_fail"] = d["iv_status"] != OK
    return d


def iv_failure_breakdown(d):
    return d["iv_status"].map(lambda s: STATUS_NAMES[int(s)]).value_counts()


__all__ = ["clean_chain", "add_price_sanity", "iv_failure_breakdown", "quote_report"]
