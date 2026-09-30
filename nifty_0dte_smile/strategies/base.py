"""Strategy interface. A strategy converts a signal into option legs; the engine does the rest.

Sign convention: z > 0 means the signalled object is RICH vs fair. direction d = -sign(z):
d = -1 sells the rich object (short vol / short the expensive wing), d = +1 buys the cheap one.
"""
from dataclasses import dataclass
from typing import List, Optional

import numpy as np


@dataclass
class Leg:
    strike: float
    cp: int
    qty: float      # signed option units (multiples of the lot)


class Strategy:
    name = "base"
    signal_col = ""
    bet = ""        # plain-English statement of what the structure is actually betting on

    def __init__(self, lots=10, lot_size=75, **params):
        self.lots, self.lot_size, self.params = lots, lot_size, params

    # --- signal handling -------------------------------------------------
    def z(self, row):
        v = row.get(self.signal_col, np.nan)
        return float(v) if np.isfinite(v) else np.nan

    def direction(self, z, thr):
        return 0 if (not np.isfinite(z) or abs(z) < thr) else (-1 if z > 0 else 1)

    def edge_remaining(self, z, d):
        """Positive while the mispricing persists in the traded direction."""
        return -d * z

    # --- structure ------------------------------------------------------
    def legs(self, snap, row, d) -> Optional[List[Leg]]:
        raise NotImplementedError

    @property
    def base_units(self):
        return self.lots * self.lot_size


# ---------- helpers shared by structures ----------
def pick_delta(snap, cp, target_abs, min_quality=True):
    """Listed option of type cp whose |delta| is closest to target; needs a clean, valid-IV quote."""
    s = snap[(snap.cp == cp) & snap.clean & snap.iv_mid.notna() & snap.delta.notna()]
    if min_quality:
        s = s[(s.bid > 0) & (s.ask > s.bid)]
    if s.empty:
        return None
    i = (s.delta.abs() - target_abs).abs().values.argmin()
    return s.iloc[i]


def pick_atm(snap, F):
    s = snap[snap.clean & snap.iv_mid.notna() & (snap.bid > 0)]
    if s.empty:
        return None, None
    k = s.strike.values[np.abs(s.strike.values - F).argmin()]
    c = s[(s.strike == k) & (s.cp > 0)]
    p = s[(s.strike == k) & (s.cp < 0)]
    if c.empty or p.empty:
        return None, None
    return c.iloc[0], p.iloc[0]
