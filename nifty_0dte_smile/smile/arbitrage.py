"""Static-arbitrage checks on a fitted smile: butterfly, vertical, price bounds, calendar."""
import numpy as np

from ..options.pricing import black76_price


def check_smile(fit, F, T, r=0.0, n=241, span_sd=4.0, tol_rel=1e-7):
    """Checks the slice on a uniform strike grid spanning +-span_sd ATM sigma-moves.

    Returns dict of bool flags (True = violation) plus the worst offenders.
    """
    atm = float(fit.iv(0.0))
    if not np.isfinite(atm) or not fit.ok:
        return {"butterfly": True, "vertical": True, "bounds": True, "g_neg": True, "any": True, "min_butterfly": np.nan}
    kmax = span_sd * atm * np.sqrt(T)
    K = np.linspace(F * np.exp(-kmax), F * np.exp(kmax), n)
    k = np.log(K / F)
    iv = fit.iv(k)
    df = np.exp(-r * T)
    C = black76_price(F, K, T, iv, 1, r)
    tol = tol_rel * F
    dK = K[1] - K[0]
    slope = np.diff(C) / dK
    vertical = bool(np.any(slope > tol / dK) or np.any(slope < -df - tol / dK))
    bfly = C[:-2] - 2 * C[1:-1] + C[2:]
    butterfly = bool(np.any(bfly < -tol))
    bounds = bool(np.any(C < df * np.maximum(F - K, 0) - tol) or np.any(C > df * F + tol))
    # Gatheral density condition g(k) >= 0 on total variance
    w = iv ** 2 * T
    dk = k[1] - k[0]
    kk = k[1:-1]
    wp = (w[2:] - w[:-2]) / (2 * dk)
    wpp = (w[2:] - 2 * w[1:-1] + w[:-2]) / dk ** 2
    wm = w[1:-1]
    g = (1 - kk * wp / (2 * wm)) ** 2 - wp ** 2 / 4 * (1 / wm + 0.25) + wpp / 2
    g_neg = bool(np.any(g < -1e-6))
    res = {"butterfly": butterfly, "vertical": vertical, "bounds": bounds, "g_neg": g_neg,
           "min_butterfly": float(bfly.min()), "min_g": float(g.min())}
    res["any"] = butterfly or vertical or bounds or g_neg
    return res


def put_call_bounds_ok(F, K, T, call_price, put_price, r=0.0, tol=1e-6):
    """Price bounds for both sides: max(F-K,0)<=C<=F and max(K-F,0)<=P<=K (discounted)."""
    df = np.exp(-r * T)
    c_ok = (call_price >= df * np.maximum(F - K, 0) - tol) & (call_price <= df * F + tol)
    p_ok = (put_price >= df * np.maximum(K - F, 0) - tol) & (put_price <= df * K + tol)
    return c_ok & p_ok


def calendar_check(fit_near, fit_far, k_grid, tol=1e-12):
    """Calendar arbitrage: total variance must be non-decreasing in maturity at every k."""
    return bool(np.any(fit_far.total_var(k_grid) < fit_near.total_var(k_grid) - tol))
