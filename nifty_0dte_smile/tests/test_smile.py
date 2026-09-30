import numpy as np
import pytest

from nifty_0dte_smile.smile.arbitrage import calendar_check, check_smile
from nifty_0dte_smile.smile.delta_surface import iv_at_delta
from nifty_0dte_smile.smile.moneyness import SmileFit
from nifty_0dte_smile.smile.polynomial import fit_polynomial
from nifty_0dte_smile.smile.spline import fit_spline
from nifty_0dte_smile.smile.svi import fit_svi, svi_w

T = 120 / 93000


def _svi_data(n=25):
    sc = 0.15 * np.sqrt(T)
    k = np.linspace(-3.5, 3.5, n) * sc
    w = svi_w(k / sc, 1.0, 0.25, -0.4, 0.05, 0.6) * (0.15 ** 2 * T)
    return k, np.sqrt(w / T)


def test_svi_recovers_svi_smile():
    k, iv = _svi_data()
    fit = fit_svi(k, iv, T)
    assert fit.ok and np.max(np.abs(fit.iv(k) - iv)) < 2e-4


@pytest.mark.parametrize("fn", [fit_polynomial, fit_spline, fit_svi])
def test_models_fit_smooth_smile(fn):
    k, iv = _svi_data()
    fit = fn(k, iv, T)
    assert fit.ok and fit.rmse < (6e-3 if fn is fit_polynomial else 2e-3)   # cubic cannot follow an SVI kink
    assert np.all(np.isfinite(fit.iv(np.array([-0.002, 0.0, 0.002]))))


def test_flat_smile_is_arbitrage_free_and_delta_inversion():
    flat = SmileFit("flat", lambda k: np.full_like(k, 0.16), T, -0.02, 0.02, {})
    assert not check_smile(flat, 24000.0, T, 0.065)["any"]
    iv, k, ext = iv_at_delta(flat, 0.25)
    # analytic: call delta N(d1)=0.25 -> d1=-0.6745 ; k = -d1*sigma*sqrtT + 0.5 sigma^2 T
    s = 0.16 * np.sqrt(T)
    assert k == pytest.approx(0.6745 * s + 0.5 * s * s, abs=2e-5)
    assert iv_at_delta(flat, -0.25)[1] < 0 < iv_at_delta(flat, 0.25)[1]


def test_butterfly_violation_detected():
    wavy = SmileFit("wavy", lambda k: 0.16 + 0.05 * np.sin(k / (0.16 * np.sqrt(T)) * 9), T, -0.03, 0.03, {})
    assert check_smile(wavy, 24000.0, T, 0.065)["any"]


def test_calendar_arbitrage():
    near = SmileFit("n", lambda k: np.full_like(k, 0.2), 0.01, -0.1, 0.1, {})
    far_ok = SmileFit("f", lambda k: np.full_like(k, 0.2), 0.02, -0.1, 0.1, {})
    far_bad = SmileFit("f", lambda k: np.full_like(k, 0.1), 0.02, -0.1, 0.1, {})
    kk = np.linspace(-0.05, 0.05, 11)
    assert not calendar_check(near, far_ok, kk) and calendar_check(near, far_bad, kk)
