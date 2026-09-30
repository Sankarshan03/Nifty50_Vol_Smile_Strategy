"""Black-76 pricing. cp: +1 call, -1 put. Forward F is undiscounted."""
import numpy as np
from scipy.special import ndtr
from scipy.stats import norm


def d1d2(F, K, T, sigma):
    sq = sigma * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sigma * sigma * T) / sq
    return d1, d1 - sq


def intrinsic(F, K, cp, T=0.0, r=0.0):
    F, K, cp, T = [np.asarray(x, dtype=float) for x in np.broadcast_arrays(F, K, cp, T)]
    return np.exp(-r * T) * np.maximum(cp * (F - K), 0.0)


def black76_price(F, K, T, sigma, cp, r=0.0):
    F, K, T, sigma, cp = [np.asarray(x, dtype=float) for x in np.broadcast_arrays(F, K, T, sigma, cp)]
    df = np.exp(-r * T)
    ok = (T > 0) & (sigma > 0)
    s = np.where(ok, sigma, 1.0)
    t = np.where(ok, T, 1.0)
    d1, d2 = d1d2(F, K, t, s)
    p = df * cp * (F * ndtr(cp * d1) - K * ndtr(cp * d2))
    return np.where(ok, p, df * np.maximum(cp * (F - K), 0.0))
