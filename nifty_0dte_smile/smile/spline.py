"""Model B: regularised (penalised cubic) smoothing spline with linear wing extrapolation."""
import numpy as np
from scipy.interpolate import make_smoothing_spline

from .moneyness import SmileFit, standardise


def fit_spline(k, iv, T, w=None, lam=None):
    k, iv = np.asarray(k, float), np.asarray(iv, float)
    o = np.argsort(k)
    k, iv = k[o], iv[o]
    w = None if w is None else np.asarray(w, float)[o]
    # collapse duplicate abscissae (CE/PE at same strike) by averaging
    uk, inv = np.unique(k, return_inverse=True)
    ui = np.bincount(inv, iv) / np.bincount(inv)
    uw = None if w is None else np.bincount(inv, w) / np.bincount(inv)
    if len(uk) < 5:
        return SmileFit("spline", lambda q: np.full_like(q, np.nan), T, k.min(), k.max(), {}, ok=False)
    atm0 = float(np.interp(0.0, uk, ui))
    x = standardise(uk, T, atm0)
    scale = atm0 * np.sqrt(T)
    spl = make_smoothing_spline(x, ui, w=None if uw is None else np.sqrt(uw), lam=lam)
    x0, x1 = x[0], x[-1]
    y0, y1 = float(spl(x0)), float(spl(x1))
    s0, s1 = float(spl(x0, 1)), float(spl(x1, 1))

    def f(q):
        xq = np.asarray(q, float) / scale
        out = np.where(xq < x0, y0 + s0 * (xq - x0), np.where(xq > x1, y1 + s1 * (xq - x1), spl(np.clip(xq, x0, x1))))
        return np.maximum(out, 1e-4)

    rmse = float(np.sqrt(np.mean((f(uk) - ui) ** 2)))
    return SmileFit("spline", f, T, uk[0], uk[-1], {"lam": lam}, ok=True, rmse=rmse)
