"""Model C: raw SVI on total variance, w(k) = a + b(rho(k-m) + sqrt((k-m)^2 + s^2)).

Fitted in normalised coordinates (k' = k/sqrt(w_atm), w' = w/w_atm) because 0DTE total
variance is ~1e-5 and raw-unit least squares is badly conditioned. Normalised SVI is still
an SVI slice, so the butterfly check in arbitrage.py applies unchanged.
"""
import numpy as np
from scipy.optimize import least_squares

from .moneyness import SmileFit


def svi_w(kp, a, b, rho, m, s):
    return a + b * (rho * (kp - m) + np.sqrt((kp - m) ** 2 + s * s))


def fit_svi(k, iv, T, w=None):
    k, iv = np.asarray(k, float), np.asarray(iv, float)
    tv = iv ** 2 * T
    o = np.argsort(k)
    w_atm = float(np.interp(0.0, k[o], tv[o]))
    sc = np.sqrt(w_atm)
    kp, y = k / sc, tv / w_atm
    wt = np.ones_like(y) if w is None else np.sqrt(np.asarray(w, float) / np.mean(w))
    if len(k) < 6:
        return SmileFit("svi", lambda q: np.full_like(q, np.nan), T, k.min(), k.max(), {}, ok=False)

    lo = [-1.0, 1e-4, -0.999, -2.0, 1e-3]
    hi = [2.0, 5.0, 0.999, 2.0, 3.0]

    def resid(p):
        return wt * (svi_w(kp, *p) - y)

    best = None
    for p0 in ([0.5, 0.3, -0.3, 0.0, 0.5], [0.2, 0.6, -0.6, 0.1, 0.3], [0.8, 0.15, 0.0, -0.1, 1.0]):
        try:
            r = least_squares(resid, p0, bounds=(lo, hi), max_nfev=80, x_scale=[0.5, 0.3, 0.5, 0.3, 0.5])
        except Exception:
            continue
        if best is None or r.cost < best.cost:
            best = r
        if best.cost / len(y) < 2e-4:   # good enough: skip remaining starts
            break
    if best is None:
        return SmileFit("svi", lambda q: np.full_like(q, np.nan), T, k.min(), k.max(), {}, ok=False)
    a, b, rho, m, s = best.x

    def f(q):
        wq = np.maximum(svi_w(np.asarray(q, float) / sc, a, b, rho, m, s) * w_atm, 1e-14)
        return np.sqrt(wq / T)

    rmse = float(np.sqrt(np.mean((f(k) - iv) ** 2)))
    return SmileFit("svi", f, T, k.min(), k.max(),
                    {"a": a, "b": b, "rho": rho, "m": m, "sigma": s, "w_atm": w_atm}, ok=True, rmse=rmse)
