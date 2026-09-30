from .base import Leg, Strategy, pick_atm, pick_delta


class VolButterfly(Strategy):
    """Wings (put+call at `delta`) vs ATM straddle, vega-neutral. delta: 0.10, 0.25, or 0.35 ('ATM-ish' fly)."""
    name = "Butterfly"
    signal_col = "H4_curv"     # z of BF = (IV_p + IV_c)/2 - IV_atm; >0 = wings rich
    bet = ("Vega-neutral, delta-hedged long/short wings vs ATM straddle. Net bet is on smile CURVATURE "
           "(butterfly IV) reverting; it still carries gamma/theta mismatch between wings and body.")

    def __init__(self, delta=0.25, **kw):
        super().__init__(delta=delta, **kw)
        self.delta = delta
        self.name = f"Butterfly {int(delta * 100)}d"

    def legs(self, snap, row, d):
        p, c = pick_delta(snap, -1, self.delta), pick_delta(snap, 1, self.delta)
        ac, ap = pick_atm(snap, row["F"])
        if p is None or c is None or ac is None or ac.vega <= 0 or ap.vega <= 0:
            return None
        # d=+1 long butterfly (long wings, short ATM body); d=-1 sells the rich wings
        qw = d * self.base_units
        body = qw * (p.vega + c.vega) / (ac.vega + ap.vega)
        return [Leg(p.strike, -1, qw), Leg(c.strike, 1, qw), Leg(ac.strike, 1, -body), Leg(ap.strike, -1, -body)]
