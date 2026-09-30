import numpy as np

from .base import Leg, Strategy, pick_delta


class VerticalSpread(Strategy):
    """Wing vertical: short the rich inner option, long the outer option, vega-neutral, delta-hedged."""
    name = "Vertical"
    signal_col = "H2_skew"     # z of put skew (IV25P - ATM) vs fair; the call wing uses H2c_skew if present
    bet = ("Delta-hedged, vega-neutral put (or call) vertical: bets that the 25d wing IV is rich/cheap relative "
           "to the 10d wing, i.e. on the wing's SLOPE rather than level or direction.")

    def __init__(self, inner=0.25, outer=0.10, **kw):
        super().__init__(inner=inner, outer=outer, **kw)
        self.inner, self.outer = inner, outer

    def legs(self, snap, row, d):
        z_put = row.get("H2_skew", np.nan)
        z_call = row.get("H2c_skew", np.nan)
        cp = -1 if (not np.isfinite(z_call) or abs(z_put) >= abs(z_call)) else 1
        a, b = pick_delta(snap, cp, self.inner), pick_delta(snap, cp, self.outer)
        if a is None or b is None or a.strike == b.strike or a.vega <= 0 or b.vega <= 0:
            return None
        qa = d * self.base_units          # d=-1: sell the rich inner wing, buy the outer wing
        qb = -qa * a.vega / b.vega
        return [Leg(a.strike, cp, qa), Leg(b.strike, cp, qb)]

    def z(self, row):
        zp, zc = row.get("H2_skew", np.nan), row.get("H2c_skew", np.nan)
        cands = [v for v in (zp, zc) if np.isfinite(v)]
        return max(cands, key=abs) if cands else np.nan
