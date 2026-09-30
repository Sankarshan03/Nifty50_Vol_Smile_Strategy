"""Executable fills. No impossible fills: a leg executes only if the liquidity condition holds."""
from dataclasses import dataclass
from typing import Optional

import numpy as np

SCENARIOS = ("mid", "half", "full", "full_slip")


@dataclass
class Fill:
    ts: object
    strike: float
    cp: int
    qty: float            # signed units (+buy / -sell)
    signal_mid: float     # theoretical price (mid) at decision time
    bid: float
    ask: float
    price: float          # executed price
    slippage: float       # (price - mid) * sign, per unit, positive = adverse


def can_fill(row, qty_units, min_volume_mult=20.0):
    """Liquidity gate: a two-sided, clean quote and traded volume well above our size."""
    if row is None:
        return False
    ok = bool(row["clean"]) and row["bid"] > 0 and row["ask"] > row["bid"]
    vol = row.get("volume", np.nan)
    if np.isfinite(vol):
        ok = ok and vol >= min_volume_mult * abs(qty_units)
    return ok


def exec_price(bid, ask, qty, scenario, costs):
    """Mid / half-spread / full bid-ask / bid-ask + slippage. Buy pays up, sell receives less."""
    mid = 0.5 * (bid + ask)
    sgn = 1.0 if qty > 0 else -1.0
    if scenario == "mid":
        return mid
    if scenario == "half":
        return mid + sgn * 0.25 * (ask - bid)       # cross half of the half-spread (partial)
    px = ask if qty > 0 else bid
    if scenario == "full":
        return px
    if scenario == "full_slip":
        return max(px + sgn * costs.slippage(mid), costs.tick)
    raise ValueError(scenario)


def make_fill(ts, strike, cp, qty, row, scenario, costs) -> Optional[Fill]:
    px = exec_price(row["bid"], row["ask"], qty, scenario, costs)
    mid = 0.5 * (row["bid"] + row["ask"])
    return Fill(ts, strike, cp, qty, mid, row["bid"], row["ask"], px, (px - mid) * np.sign(qty))
