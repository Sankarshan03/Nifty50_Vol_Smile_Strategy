"""Delta-space view of a fitted smile + smile-model comparison utilities."""
import numpy as np
from scipy.stats import norm

from .moneyness import DELTA_NAMES, SmileFit


def _grid(fit, n=801, sd=7.0):
    atm = float(fit.iv(0.0))
    kmax = sd * atm * np.sqrt(fit.T)
    k = np.linspace(-kmax, kmax, n)
    iv = np.clip(fit.iv(k), 0.01, 5.0)
    d1 = (-k + 0.5 * iv ** 2 * fit.T) / (iv * np.sqrt(fit.T))
    return k, iv, norm.cdf(d1)  # call-equivalent forward delta (decreasing in k)


def k_at_delta(fit, signed_delta):
    """Log-moneyness at which the option has the given signed forward delta.
    Put delta -0.25 <-> call-equivalent delta 0.75. Returns NaN if off-grid."""
    k, iv, cd = _grid(fit)
    target = signed_delta if signed_delta > 0 else 1.0 + signed_delta
    if not (cd.min() <= target <= cd.max()):
        return np.nan
    return float(np.interp(target, cd[::-1], k[::-1]))


def iv_at_delta(fit, signed_delta):
    kq = k_at_delta(fit, signed_delta)
    if not np.isfinite(kq):
        return np.nan, np.nan, True
    return float(fit.iv(kq)), kq, bool(fit.extrapolated(kq))


def delta_smile(fit, deltas=tuple(DELTA_NAMES)):
    """{name: (iv, k, extrapolated)} for each delta pillar."""
    return {DELTA_NAMES[d]: iv_at_delta(fit, d) for d in deltas}


def cv_errors(fit_fn, k, iv, T, w=None, folds=3, wing_n=2):
    """Out-of-sample IV errors for a model: (a) interleaved K-fold hold-out (interpolation),
    (b) wing extrapolation (fit on inner points, predict the `wing_n` outermost each side)."""
    k, iv = np.asarray(k), np.asarray(iv)
    o = np.argsort(k)
    k, iv = k[o], iv[o]
    w = None if w is None else np.asarray(w)[o]
    n = len(k)
    cv = []
    for f in range(folds):
        test = np.arange(n) % folds == f
        test[0] = test[-1] = False  # keep end points in training
        if test.sum() == 0 or (~test).sum() < 7:
            continue
        m = fit_fn(k[~test], iv[~test], T, None if w is None else w[~test])
        if m.ok:
            cv.append(m.iv(k[test]) - iv[test])
    ext = []
    if n >= 12 + 2 * wing_n:
        tr = slice(wing_n, n - wing_n)
        m = fit_fn(k[tr], iv[tr], T, None if w is None else w[tr])
        if m.ok:
            te = np.r_[0:wing_n, n - wing_n:n]
            ext = m.iv(k[te]) - iv[te]
    c = np.concatenate(cv) if cv else np.array([np.nan])
    return float(np.sqrt(np.nanmean(c ** 2))), float(np.sqrt(np.nanmean(np.asarray(ext) ** 2))) if len(ext) else np.nan
