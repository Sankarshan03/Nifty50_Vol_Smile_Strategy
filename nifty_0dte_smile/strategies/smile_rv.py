import numpy as np

from .base import Leg, Strategy, pick_atm, pick_delta

PILLAR_DELTA = {"p10": (-1, 0.10), "p25": (-1, 0.25), "c25": (1, 0.25), "c10": (1, 0.10)}


class SmileRV(Strategy):
    """Cross-strike RV: short the richest pillar vs neighbours, long the cheapest, vega-neutral, delta-hedged."""
    name = "Smile RV"
    signal_col = "H6_rv"       # z(rich) - z(cheap) >= 2*threshold
    bet = ("Isolates pillar-vs-neighbour smile dislocation: short the pillar whose IV is richest relative to what "
           "its neighbours imply, long the cheapest, vega-neutral and delta-hedged. Exposure is to the "
           "dislocation closing, not to the smile level or spot.")

    def direction(self, z, thr):
        return -1 if (np.isfinite(z) and z >= 2 * thr) else 0

    def edge_remaining(self, z, d):
        return z / 2.0     # score is z(rich)-z(cheap); halve so entry/exit thresholds share units

    def _opt(self, snap, row, name):
        if name == "atm":
            c, p = pick_atm(snap, row["F"])
            return c if c is not None else None     # ATM leg = call only (vega-comparable, keeps it simple)
        cp, dl = PILLAR_DELTA[name]
        return pick_delta(snap, cp, dl)

    def legs(self, snap, row, d):
        rich, cheap = row.get("rv_rich"), row.get("rv_cheap")
        if not isinstance(rich, str) or not isinstance(cheap, str) or rich == cheap:
            return None
        a, b = self._opt(snap, row, rich), self._opt(snap, row, cheap)
        if a is None or b is None or a.vega <= 0 or b.vega <= 0 or (a.strike == b.strike and a.cp == b.cp):
            return None
        qa = -self.base_units
        qb = -qa * a.vega / b.vega
        return [Leg(a.strike, int(a.cp), qa), Leg(b.strike, int(b.cp), qb)]
