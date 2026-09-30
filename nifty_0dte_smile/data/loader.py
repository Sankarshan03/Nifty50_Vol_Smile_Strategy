"""Load option-chain and underlying data into the canonical schema.

Options  : timestamp, expiry, strike, cp (+1 CE / -1 PE), bid, ask, ltp, volume, oi, oi_change
Underlying: timestamp, spot, fut, fut_bid, fut_ask, vix

Files may be CSV or parquet; common vendor column names are aliased. Nothing is
interpolated between snapshots (a missing snapshot stays missing).
"""
from pathlib import Path

import numpy as np
import pandas as pd

OPT_ALIASES = {
    "datetime": "timestamp", "time": "timestamp", "date_time": "timestamp",
    "expiry_date": "expiry", "strike_price": "strike", "option_type": "cp", "type": "cp", "right": "cp",
    "best_bid": "bid", "best_ask": "ask", "bid_price": "bid", "ask_price": "ask", "last_price": "ltp", "close": "ltp",
    "open_interest": "oi", "change_in_oi": "oi_change", "chg_in_oi": "oi_change", "oi_chg": "oi_change",
}
UND_ALIASES = {
    "datetime": "timestamp", "time": "timestamp", "date_time": "timestamp",
    "nifty": "spot", "nifty_spot": "spot", "index": "spot", "underlying": "spot",
    "future": "fut", "futures": "fut", "fut_ltp": "fut", "india_vix": "vix", "vix_close": "vix",
    "futures_bid": "fut_bid", "futures_ask": "fut_ask",
}
OPT_COLS = ["timestamp", "expiry", "strike", "cp", "bid", "ask", "ltp", "volume", "oi", "oi_change"]
UND_COLS = ["timestamp", "spot", "fut", "fut_bid", "fut_ask", "vix"]


def _read(path):
    path = Path(path)
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _norm_cols(df, aliases):
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df.rename(columns={k: v for k, v in aliases.items() if k in df.columns and v not in df.columns})


def _cp(s):
    if pd.api.types.is_numeric_dtype(s):
        return np.sign(s).astype(int)
    return s.astype(str).str.upper().str[0].map({"C": 1, "P": -1}).astype("Int64")


def load_options(path):
    df = _norm_cols(_read(path), OPT_ALIASES)
    missing = [c for c in ["timestamp", "strike", "cp", "bid", "ask"] if c not in df.columns]
    if missing:
        raise ValueError(f"options file missing required columns: {missing}")
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["expiry"] = pd.to_datetime(df["expiry"]) if "expiry" in df.columns else df["timestamp"].dt.normalize()
    df["cp"] = _cp(df["cp"]).astype(int)
    for c in OPT_COLS:
        if c not in df.columns:
            df[c] = np.nan
    return df[OPT_COLS].sort_values(["timestamp", "expiry", "strike", "cp"]).reset_index(drop=True)


def load_underlying(path):
    df = _norm_cols(_read(path), UND_ALIASES)
    if "timestamp" not in df.columns or "spot" not in df.columns:
        raise ValueError("underlying file needs timestamp and spot columns")
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    for c in UND_COLS:
        if c not in df.columns:
            df[c] = np.nan
    return df[UND_COLS].drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)


def load_dataset(data_dir):
    """Expects options.(csv|parquet) and underlying.(csv|parquet) in data_dir."""
    d = Path(data_dir)
    pick = lambda stem: next((d / f"{stem}{e}" for e in (".parquet", ".csv") if (d / f"{stem}{e}").exists()), None)
    o, u = pick("options"), pick("underlying")
    if o is None or u is None:
        raise FileNotFoundError(f"need options.csv|parquet and underlying.csv|parquet in {d}")
    return load_options(o), load_underlying(u)


def zero_dte(options):
    """Keep only contracts expiring on the snapshot's own calendar date."""
    m = options["expiry"].dt.normalize() == options["timestamp"].dt.normalize()
    return options[m].copy()
