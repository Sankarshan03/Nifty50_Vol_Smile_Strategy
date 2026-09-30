import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from nifty_0dte_smile.options.greeks import greeks
from nifty_0dte_smile.options.iv import BELOW_INTRINSIC, OK, implied_vol
from nifty_0dte_smile.options.pricing import black76_price
from nifty_0dte_smile.options.timeutil import year_fraction


def test_atm_price_closed_form():
    F, T, s = 100.0, 0.25, 0.2
    assert black76_price(F, F, T, s, 1) == pytest.approx(F * (2 * norm.cdf(s * np.sqrt(T) / 2) - 1), rel=1e-12)


def test_put_call_parity():
    F, K, T, s, r = 24000.0, 24200.0, 0.002, 0.15, 0.065
    c, p = black76_price(F, K, T, s, 1, r), black76_price(F, K, T, s, -1, r)
    assert c - p == pytest.approx(np.exp(-r * T) * (F - K), abs=1e-8)


@pytest.mark.parametrize("T", [1 / 93000, 30 / 93000, 375 / 93000, 0.05])
@pytest.mark.parametrize("cp", [1, -1])
def test_iv_roundtrip(T, cp):
    F = 24000.0
    K = F * np.exp(np.linspace(-0.03, 0.03, 13))
    for sigma in (0.08, 0.15, 0.6):
        p = black76_price(F, K, T, sigma, cp, 0.065)
        iv, st = implied_vol(p, F, K, T, cp, 0.065)
        ok = st == OK
        assert np.allclose(iv[ok], sigma, atol=1e-5)
        # ATM-ish options always have invertible prices
        assert ok[5:8].all() or T < 2 / 93000


def test_below_intrinsic_rejected():
    iv, st = implied_vol(np.array([9.0]), 110.0, 100.0, 0.01, 1)
    assert st[0] == BELOW_INTRINSIC and np.isnan(iv[0])


def test_nan_and_bad_inputs():
    iv, st = implied_vol(np.array([np.nan, 5.0]), 100.0, np.array([100.0, -1.0]), 0.01, 1)
    assert np.isnan(iv).all() and (st != OK).all()


def test_greeks_match_finite_differences():
    F, K, T, s = 24000.0, 24100.0, 0.004, 0.14
    g = greeks(F, K, T, s, 1)
    h = 1.0
    d = (black76_price(F + h, K, T, s, 1) - black76_price(F - h, K, T, s, 1)) / (2 * h)
    gm = (black76_price(F + h, K, T, s, 1) - 2 * black76_price(F, K, T, s, 1) + black76_price(F - h, K, T, s, 1)) / h ** 2
    v = (black76_price(F, K, T, s + 1e-4, 1) - black76_price(F, K, T, s - 1e-4, 1)) / 2e-4
    th = -(black76_price(F, K, T + 1e-7, s, 1) - black76_price(F, K, T - 1e-7, s, 1)) / 2e-7
    assert g["delta"] == pytest.approx(d, rel=1e-4)
    assert g["gamma"] == pytest.approx(gm, rel=1e-4)
    assert g["vega"] == pytest.approx(v, rel=1e-5)
    assert g["theta"] == pytest.approx(th, rel=1e-4)


def test_time_is_never_zero_and_conventions_ordered():
    ts = pd.DatetimeIndex(["2025-01-06 15:30", "2025-01-06 09:15"])
    t = year_fraction(ts)
    assert (t > 0).all() and t[1] > t[0]
    assert year_fraction(ts, "calendar")[1] < year_fraction(ts, "trading248")[1]
