import numpy as np
import pandas as pd
import pytest

from nifty_0dte_smile.backtest.costs import CostModel
from nifty_0dte_smile.backtest.engine import BTParams, ChainStore, run_backtest
from nifty_0dte_smile.config import Config
from nifty_0dte_smile.data.synthetic import generate
from nifty_0dte_smile.hedging.delta_hedger import HedgeRule
from nifty_0dte_smile.pipeline import build
from nifty_0dte_smile.strategies.risk_reversal import RiskReversal
from nifty_0dte_smile.strategies.straddle import ATMStraddle


@pytest.fixture(scope="module")
def world():
    o, u = generate(n_days=2, freq_min=30, seed=3)
    cfg = Config(min_fit_points=6)
    res = build(o, u, cfg)
    f = res["features"]
    sig = pd.DataFrame({"F": f.F, "F_fut": f.F_fut, "fut_spread": f.fut_spread, "T": f["T"], "atm_iv": f.poly_atm_iv})
    z = np.zeros(len(sig))
    z[[2, 3, 4, 15, 16, 17]] = [3, 3, 3, -3, -3, -3]
    sig["H1_atm"] = z
    sig["H3_wing"] = z
    return res, sig, cfg


def test_pipeline_outputs(world):
    res, sig, cfg = world
    f = res["features"]
    assert f.poly_atm_iv.notna().mean() > 0.8 and (f["T"] > 0).all()
    assert (f.F_pcp.dropna() - f.F_fut.dropna()).abs().median() < 25


@pytest.mark.parametrize("strat", [ATMStraddle(lots=2), RiskReversal(lots=2)])
def test_accounting_identities_and_cost_ordering(world, strat):
    res, sig, cfg = world
    store, cm = ChainStore(res["chain"]), CostModel()
    out = {sc: run_backtest(store, sig, strat, BTParams(entry_thr=2.0, scenario=sc, hedge=HedgeRule("interval", 30)),
                            cm, cfg) for sc in ("mid", "half", "full", "full_slip")}
    t = out["full_slip"]
    assert len(t) >= 1
    assert np.allclose(t.gross_pnl, t.option_gross_pnl + t.hedge_pnl)
    assert np.allclose(t.net_pnl, t.gross_pnl + t.cost_pnl)
    comp = t[["delta_pnl", "gamma_pnl", "theta_pnl", "vega_level_pnl", "vol_surface_pnl", "unexplained_pnl"]].sum(axis=1)
    assert np.allclose(comp, t.option_gross_pnl)
    assert out["mid"].gross_pnl.sum() == pytest.approx(out["full_slip"].gross_pnl.sum(), rel=1e-6, abs=1e-6)
    assert out["mid"].net_pnl.sum() >= out["half"].net_pnl.sum() >= out["full"].net_pnl.sum() >= out["full_slip"].net_pnl.sum()
    assert (t.entry_ts < t.exit_ts).all()
    assert (t.exit_ts.dt.hour * 60 + t.exit_ts.dt.minute <= 15 * 60 + 30).all()
