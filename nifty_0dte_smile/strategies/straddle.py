from .base import Leg, Strategy, pick_atm


class ATMStraddle(Strategy):
    name = "ATM Straddle"
    signal_col = "H1_atm"
    bet = ("Short (long) ATM volatility when ATM IV is rich (cheap) vs fair; delta-hedged, so it is a bet on "
           "realised-vs-implied variance over the holding period plus the IV reversion (net vega and gamma/theta).")

    def legs(self, snap, row, d):
        c, p = pick_atm(snap, row["F"])
        if c is None:
            return None
        q = d * self.base_units           # d=+1 long straddle, d=-1 short straddle
        return [Leg(c.strike, 1, q), Leg(p.strike, -1, q)]
