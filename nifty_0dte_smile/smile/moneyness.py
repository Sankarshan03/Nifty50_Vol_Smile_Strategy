"""Moneyness representations: strike-space, log-moneyness k = ln(K/F), delta-space."""
from dataclasses import dataclass
from typing import Callable

import numpy as np

# Signed forward deltas of interest: puts negative, calls positive.
DELTA_GRID = [-0.05, -0.10, -0.15, -0.25, -0.35, 0.50, 0.35, 0.25, 0.15, 0.10, 0.05]
DELTA_NAMES = {-0.05: "p05", -0.10: "p10", -0.15: "p15", -0.25: "p25", -0.35: "p35", 0.50: "atm",
               0.35: "c35", 0.25: "c25", 0.15: "c15", 0.10: "c10", 0.05: "c05"}


def log_moneyness(K, F):
    return np.log(np.asarray(K, dtype=float) / np.asarray(F, dtype=float))


def strike_from_k(k, F):
    return F * np.exp(k)


@dataclass
class SmileFit:
    """Common interface returned by every smile model."""
    name: str
    iv_fn: Callable            # k -> implied vol
    T: float
    k_min: float               # data support
    k_max: float
    params: dict
    ok: bool = True
    rmse: float = np.nan

    def iv(self, k):
        return np.asarray(self.iv_fn(np.asarray(k, dtype=float)), dtype=float)

    def total_var(self, k):
        return self.iv(k) ** 2 * self.T

    def extrapolated(self, k):
        k = np.asarray(k, dtype=float)
        return (k < self.k_min) | (k > self.k_max)


def standardise(k, T, atm_guess):
    """Scale log-moneyness by the ATM one-sigma move so fits are well conditioned."""
    return np.asarray(k) / (atm_guess * np.sqrt(T))
