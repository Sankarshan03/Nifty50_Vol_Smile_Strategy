from .base import Leg, Strategy, pick_delta


class RiskReversal(Strategy):
    name = "Risk Reversal"
    signal_col = "H3_wing"     # z of (IV25P - IV25C) vs fair: >0 = downside skew rich
    bet = ("Delta-hedged, vega-neutral 25-delta risk reversal: sells the rich put wing and buys the call wing "
           "(or the reverse). Net bet is on the SKEW (IV25P-IV25C) mean-reverting, not on direction or level.")

    def legs(self, snap, row, d):
        delta = self.params.get("delta", 0.25)
        p, c = pick_delta(snap, -1, delta), pick_delta(snap, 1, delta)
        if p is None or c is None or p.vega <= 0 or c.vega <= 0:
            return None
        qp = d * self.base_units                       # d=-1: short put (rich), long call
        qc = -qp * p.vega / c.vega                     # vega-neutral hedge ratio
        return [Leg(p.strike, -1, qp), Leg(c.strike, 1, qc)]
