"""Futures delta-hedging rules: fixed interval, delta threshold, and gamma-aware (Whalley-Wilmott band)."""
from dataclasses import dataclass

import numpy as np


@dataclass
class HedgeRule:
    kind: str = "interval"      # interval | threshold | ww | none
    param: float = 5.0          # minutes (interval) / units of delta (threshold) / risk aversion (ww)
    lot: int = 75               # hedge in futures lots

    def label(self):
        return f"{self.kind}:{self.param:g}"

    def should_hedge(self, net_delta, net_gamma, F, T, minutes_since_hedge, cost_per_unit, r=0.0):
        if self.kind == "none":
            return False
        if self.kind == "interval":
            return minutes_since_hedge >= self.param
        if self.kind == "threshold":
            return abs(net_delta) > self.param
        if self.kind == "ww":
            # Whalley-Wilmott half-band: (3/2 * exp(-rT) * c * F * Gamma^2 / A)^(1/3); gamma-aware:
            # the band widens when gamma is large and costs are high, tightens as risk-aversion A grows.
            g = abs(net_gamma)
            band = (1.5 * np.exp(-r * T) * cost_per_unit * F * g ** 2 / max(self.param, 1e-9)) ** (1 / 3) if g > 0 else 0.0
            return abs(net_delta) > max(band, 0.5 * self.lot)
        raise ValueError(self.kind)

    def target_units(self, net_delta):
        """Futures units to hold to neutralise the option delta (rounded to lots)."""
        return -np.round(net_delta / self.lot) * self.lot
