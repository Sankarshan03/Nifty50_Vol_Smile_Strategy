import numpy as np
import pandas as pd
import pytest

from nifty_0dte_smile.backtest.costs import CostModel
from nifty_0dte_smile.backtest.execution import exec_price
from nifty_0dte_smile.hedging.delta_hedger import HedgeRule
from nifty_0dte_smile.signals.mean_reversion import ar1, ols_cluster
from nifty_0dte_smile.signals.relative_value import expanding_z
from nifty_0dte_smile.statistics.tests import benjamini_hochberg, holm


def test_bh_and_holm():
    p = np.array([0.001, 0.008, 0.039, 0.041, 0.6])
    adj, rej = benjamini_hochberg(p, 0.05)
    assert rej.tolist() == [True, True, False, False, False] and np.all(adj >= p - 1e-12)
    assert holm(p)[1].sum() <= rej.sum()


def test_ar1_half_life_recovered():
    rng = np.random.default_rng(0)
    idx = pd.date_range("2025-01-01 09:15", periods=75 * 40, freq="5min")
    x = np.zeros(len(idx))
    for i in range(1, len(x)):
        x[i] = 0.9 * x[i - 1] + rng.normal()
    s = pd.Series(x, index=idx)
    r = ar1(s, pd.Series(np.repeat(np.arange(40), 75), index=idx))
    assert r["phi"] == pytest.approx(0.9, abs=0.03)
    assert r["half_life_steps"] == pytest.approx(np.log(2) / -np.log(0.9), rel=0.3) and r["adf_reject_5pct"]


def test_ols_cluster_slope():
    rng = np.random.default_rng(1)
    x = rng.normal(size=2000)
    g = np.repeat(np.arange(40), 50)
    y = -0.5 * x + rng.normal(size=2000)
    r = ols_cluster(y, x, g)
    assert r["beta"] == pytest.approx(-0.5, abs=0.08) and r["p"] < 1e-6


def test_expanding_z_has_no_lookahead():
    base = pd.Series(np.sin(np.arange(60) * 0.7))
    z_trunc = expanding_z(base)
    z_with = expanding_z(pd.concat([base, pd.Series([1000.0])], ignore_index=True))
    assert np.allclose(z_with.iloc[:60].dropna(), z_trunc.dropna())   # a later outlier cannot change past z


def test_execution_price_ordering_and_fees():
    c = CostModel()
    bid, ask = 100.0, 101.0
    buys = [exec_price(bid, ask, 1, s, c) for s in ("mid", "half", "full", "full_slip")]
    sells = [exec_price(bid, ask, -1, s, c) for s in ("mid", "half", "full", "full_slip")]
    assert buys == sorted(buys) and sells == sorted(sells, reverse=True)
    assert buys[2] == ask and sells[2] == bid
    assert c.fees(100, 750, False) > c.fees(100, 750, True)           # STT on the sell side only
    assert CostModel(fee_mult=2).fees(100, 750, True) == pytest.approx(2 * c.fees(100, 750, True))


def test_hedger_rules():
    h = HedgeRule("threshold", 100, 75)
    assert h.should_hedge(150, 0, 24000, 0.002, 0, 0.3) and not h.should_hedge(50, 0, 24000, 0.002, 0, 0.3)
    assert HedgeRule("interval", 5).should_hedge(0, 0, 24000, 0.002, 5, 0.3)
    assert not HedgeRule("none").should_hedge(9e9, 1, 1, 1, 9, 1)
    assert h.target_units(152) == -150
    w = HedgeRule("ww", 1.0, 75)
    assert w.should_hedge(10_000, 5.0, 24000, 0.002, 0, 0.3) and not w.should_hedge(1, 5.0, 24000, 0.002, 0, 0.3)
