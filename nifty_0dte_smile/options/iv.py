"""Robust vectorised Black-76 implied volatility (bracketed bisection + Newton polish)."""
import numpy as np

from .greeks import greeks
from .pricing import black76_price, intrinsic

OK, BELOW_INTRINSIC, ABOVE_UPPER, NO_BRACKET, LOW_VEGA, BAD_INPUT = range(6)
STATUS_NAMES = ["ok", "below_intrinsic", "above_upper_bound", "no_bracket", "low_vega", "bad_input"]

SIGMA_LO, SIGMA_HI = 1e-4, 10.0


def implied_vol(price, F, K, T, cp, r=0.0, tol_abs=1e-9, min_vega=1e-10):
    """Return (iv, status). iv is NaN whenever status != OK.

    The price must lie strictly inside (intrinsic, upper bound) or no IV exists.
    Bisection is used because it is stable for tiny T, deep ITM/OTM and vanishing
    vega; a few safeguarded Newton steps then polish the root.
    """
    price, F, K, T, cp = [np.atleast_1d(np.asarray(x, dtype=float)) for x in np.broadcast_arrays(price, F, K, T, cp)]
    iv = np.full(price.shape, np.nan)
    status = np.full(price.shape, OK, dtype=int)
    df = np.exp(-r * T)

    bad = ~(np.isfinite(price) & np.isfinite(F) & np.isfinite(K) & np.isfinite(T)) | (F <= 0) | (K <= 0) | (T <= 0)
    lo_b = intrinsic(F, K, cp, T, r)
    hi_b = np.where(cp > 0, df * F, df * K)
    status[bad] = BAD_INPUT
    below = ~bad & (price <= lo_b + tol_abs)
    above = ~bad & (price >= hi_b - tol_abs)
    status[below] = BELOW_INTRINSIC
    status[above & ~below] = ABOVE_UPPER

    todo = status == OK
    if todo.any():
        Fs, Ks, Ts, cps, ps = F[todo], K[todo], T[todo], cp[todo], price[todo]
        lo = np.full(Fs.shape, SIGMA_LO)
        hi = np.full(Fs.shape, SIGMA_HI)
        nob = (ps < black76_price(Fs, Ks, Ts, lo, cps, r)) | (ps > black76_price(Fs, Ks, Ts, hi, cps, r))
        for _ in range(52):
            mid = 0.5 * (lo + hi)
            up = black76_price(Fs, Ks, Ts, mid, cps, r) < ps
            lo = np.where(up, mid, lo)
            hi = np.where(up, hi, mid)
        sol = 0.5 * (lo + hi)
        for _ in range(3):
            g = greeks(Fs, Ks, Ts, sol, cps, r)
            err = black76_price(Fs, Ks, Ts, sol, cps, r) - ps
            good = g["vega"] > 1e-8
            step = np.where(good, err / np.where(good, g["vega"], 1.0), 0.0)
            cand = np.clip(sol - step, SIGMA_LO, SIGMA_HI)
            better = np.abs(black76_price(Fs, Ks, Ts, cand, cps, r) - ps) < np.abs(err)
            sol = np.where(better, cand, sol)
        vega = greeks(Fs, Ks, Ts, sol, cps, r)["vega"]
        st = np.where(nob, NO_BRACKET, np.where(vega < min_vega, LOW_VEGA, OK))
        iv[todo] = np.where(st == OK, sol, np.nan)
        status[todo] = st
    return iv, status
