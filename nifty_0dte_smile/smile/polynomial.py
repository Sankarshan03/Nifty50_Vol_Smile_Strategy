"""Model A: IV(m) = a + b m + c m^2 + d m^3 (weighted LS, m standardised by ATM sigma-move)."""
import numpy as np

from .moneyness import SmileFit, standardise


def fit_polynomial(k, iv, T, w=None, degree=3):
    k, iv = np.asarray(k, float), np.asarray(iv, float)
    atm0 = float(np.interp(0.0, k[np.argsort(k)], iv[np.argsort(k)]))
    x = standardise(k, T, atm0)
    if len(k) < degree + 2:
        return SmileFit("poly", lambda q: np.full_like(q, np.nan), T, k.min(), k.max(), {}, ok=False)
    coef = np.polyfit(x, iv, degree, w=None if w is None else np.sqrt(w))
    scale = atm0 * np.sqrt(T)

    def f(q):
        return np.maximum(np.polyval(coef, np.asarray(q) / scale), 1e-4)

    rmse = float(np.sqrt(np.mean((f(k) - iv) ** 2)))
    return SmileFit("poly", f, T, k.min(), k.max(),
                    {"coef_high_to_low": coef.tolist(), "scale": scale}, ok=True, rmse=rmse)
