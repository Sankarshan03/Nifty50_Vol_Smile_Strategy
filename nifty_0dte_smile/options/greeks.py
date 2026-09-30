"""Black-76 Greeks w.r.t. the forward F (vega per 1.00 of vol, theta per year)."""
import numpy as np
from scipy.special import ndtr
from scipy.stats import norm

from .pricing import black76_price, d1d2


def greeks(F, K, T, sigma, cp, r=0.0):
    F, K, T, sigma, cp = [np.asarray(x, dtype=float) for x in np.broadcast_arrays(F, K, T, sigma, cp)]
    df = np.exp(-r * T)
    ok = (T > 0) & (sigma > 0)
    s = np.where(ok, sigma, 1.0)
    t = np.where(ok, T, 1.0)
    d1, _ = d1d2(F, K, t, s)
    pdf = norm.pdf(d1)
    delta = df * np.where(cp > 0, ndtr(d1), ndtr(d1) - 1.0)
    gamma = df * pdf / (F * s * np.sqrt(t))
    vega = df * F * pdf * np.sqrt(t)
    price = black76_price(F, K, t, s, cp, r)
    theta = r * price - df * F * pdf * s / (2 * np.sqrt(t))
    return {
        "delta": np.where(ok, delta, np.where(cp * (F - K) > 0, cp * df, 0.0)),
        "gamma": np.where(ok, gamma, 0.0),
        "vega": np.where(ok, vega, 0.0),
        "theta": np.where(ok, theta, 0.0),
    }
