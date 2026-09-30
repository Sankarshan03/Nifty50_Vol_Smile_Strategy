"""Research orchestration: data -> smile -> factors -> fair smile -> residual tests -> strategies -> verdict.

The sequence follows the brief: nothing here starts from a desired strategy. Signals are built from
residual statistics; strategies are then the structures that isolate each signal.
Every choice (fair model, threshold, hedge rule) is made on TRAIN+VALIDATION days only and then frozen;
the locked test block is only ever used to *report*.
"""
import numpy as np
import pandas as pd

from .backtest.costs import CostModel
from .backtest.engine import BTParams, ChainStore, run_backtest
from .backtest.pnl import metrics
from .config import Config
from .factors import factor_table
from .factors.pca import causal_pc_scores, smile_pca
from .hedging.delta_hedger import HedgeRule
from .options.timeutil import CONVENTIONS
from .pipeline import FITTERS, select_smile_points, smile_weights
from .signals.mean_reversion import future_change, ols_cluster, predictability_table
from .signals.relative_value import HYPOTHESES, hypothesis_signals, pillar_rv_signal, pca_factor_signals
from .signals.residual import (FACTOR_COLS, context_frame, fair_historical, fair_ml, infer_freq_minutes, residuals,
                               xs_fair_table)
from .smile.arbitrage import check_smile
from .smile.delta_surface import cv_errors
from .statistics.tests import benjamini_hochberg
from .strategies.butterfly import VolButterfly
from .strategies.risk_reversal import RiskReversal
from .strategies.smile_rv import SmileRV
from .strategies.straddle import ATMStraddle
from .strategies.vertical import VerticalSpread

FAIR_MODELS = ["hist", "xs", "ridge", "gbm"]
HYP_FACTOR = {"H1_atm": "atm_iv", "H2_skew": "put_skew25", "H2c_skew": "call_skew25", "H3_wing": "wing_spread",
              "H4_curv": "bf25"}


# ---------------------------------------------------------------- periods
def split_days(days, fracs):
    days = sorted(pd.DatetimeIndex(days).unique())
    n = len(days)
    a, b = int(round(n * fracs[0])), int(round(n * (fracs[0] + fracs[1])))
    return {"train": days[:a], "val": days[a:b], "test": days[b:]}


# ---------------------------------------------------------------- smile model comparison
def compare_models(res, cfg, n_samples=120, seed=0):
    """Fit error, hold-out error, wing-extrapolation error, arbitrage-violation rate and step-to-step
    parameter stability per smile model, on a random sample of timestamps."""
    chain, feats = res["chain"], res["features"]
    stamps = feats.index[feats["n_fit"] >= cfg.min_fit_points]
    rng = np.random.default_rng(seed)
    pick = pd.DatetimeIndex(rng.choice(stamps, min(n_samples, len(stamps)), replace=False))
    g = {ts: d for ts, d in chain[chain.timestamp.isin(pick)].groupby("timestamp")}
    rows = []
    for ts in pick:
        pts = select_smile_points(g[pd.Timestamp(ts)], cfg)
        if len(pts) < cfg.min_fit_points:
            continue
        k, iv, w = pts.k.values, pts.iv_mid.values, smile_weights(pts)
        T, F = pts["T"].iloc[0], pts["F"].iloc[0]
        for name, fn in FITTERS.items():
            fit = fn(k, iv, T, w)
            if not fit.ok:
                continue
            cv, ext = cv_errors(fn, k, iv, T, w)
            arb = check_smile(fit, F, T, cfg.r)
            rows.append(dict(model=name, rmse=fit.rmse, cv_rmse=cv, wing_extrap_rmse=ext, arb=arb["any"],
                             butterfly=arb["butterfly"], total_var_rmse=np.sqrt(np.mean(
                                 (fit.total_var(k) - iv ** 2 * T) ** 2)) / np.mean(iv ** 2 * T)))
    df = pd.DataFrame(rows)
    tab = df.groupby("model").agg(fit_rmse=("rmse", "median"), cv_rmse=("cv_rmse", "median"),
                                  wing_extrap_rmse=("wing_extrap_rmse", "median"), arb_violation_rate=("arb", "mean"),
                                  butterfly_violation_rate=("butterfly", "mean"),
                                  total_var_rel_rmse=("total_var_rmse", "median"))
    # parameter stability: median absolute step-to-step change of ATM IV and 25d skew, relative to the noise
    stab = {}
    for m in cfg.smile_models:
        x = feats.get(f"{m}_atm_iv")
        y = feats.get(f"{m}_p25")
        if x is not None:
            d = x.groupby(feats.index.normalize()).diff().abs().median()
            dy = (y - x).groupby(feats.index.normalize()).diff().abs().median()
            stab[m] = dict(atm_step_abs=d, skew25_step_abs=dy)
    tab = tab.join(pd.DataFrame(stab).T)
    return tab


def time_convention_sensitivity(feats, model):
    """Black-76 depends on sigma*sqrt(T) only, so IV scales as sqrt(T_ref/T_alt) exactly (rates ~ negligible)."""
    base = CONVENTIONS["trading248"]
    rows = []
    for name, denom in CONVENTIONS.items():
        scale = np.sqrt(base / denom)       # T_alt = rem/denom -> sigma_alt = sigma_ref*sqrt(T_ref/T_alt)
        rows.append(dict(convention=name, mean_atm_iv=(feats[f"{model}_atm_iv"] * scale).mean(),
                         vs_trading248=scale))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- signals
def build_signal_frames(res, cfg, model, sel_days):
    feats = res["features"]
    freq = infer_freq_minutes(feats.index)
    ctx = context_frame(feats, freq)
    obs = factor_table(feats, model)
    fairs = {"hist": fair_historical(obs, ctx), "xs": xs_fair_table(feats, model),
             "ridge": fair_ml(obs, ctx, kind="ridge", freq_min=freq), "gbm": fair_ml(obs, ctx, kind="gbm", freq_min=freq)}
    resid = {k: residuals(obs, v) for k, v in fairs.items()}
    return dict(freq=freq, ctx=ctx, obs=obs, fairs=fairs, resid=resid)


def fair_model_rmse(obs, fairs):
    rows = []
    for k, f in fairs.items():
        for c in FACTOR_COLS:
            m = f[c].notna() & obs[c].notna()
            if m.sum() > 50:
                rows.append(dict(fair=k, factor=c, rmse=float(np.sqrt(((obs[c] - f[c])[m] ** 2).mean())), n=int(m.sum())))
    return pd.DataFrame(rows)


def predictability_all(sf, horizons=(5, 10, 15, 30, 60)):
    """Predictive regression / IC / AR(1) half-life for every (fair model, factor, horizon), plus BH-FDR."""
    frames = []
    for k, r in sf["resid"].items():
        t = predictability_table(r, sf["obs"], sf["freq"], horizons)
        if len(t):
            t.insert(0, "fair", k)
            frames.append(t)
    t = pd.concat(frames, ignore_index=True)
    t["p_adj_bh"], t["fdr_reject"] = benjamini_hochberg(t["ic_p"].values)
    t["mean_reverting"] = (t["ic"] < 0) & (t["beta_dchange"] < 0)
    return t


def choose_fair_per_hypothesis(sf, sel_days, h_min=15):
    """Pick, per hypothesis, the fair model whose residual most negatively predicts the subsequent
    smile move, using selection (train+val) days ONLY."""
    from .signals.mean_reversion import information_coefficient
    h = max(int(round(h_min / sf["freq"])), 1)
    out = {}
    sel = pd.DatetimeIndex(sel_days)
    for hyp, col in HYP_FACTOR.items():
        best, best_ic = None, np.inf
        for k, r in sf["resid"].items():
            if col not in r or r[col].notna().sum() < 100:
                continue
            fc = future_change(sf["obs"][col], h)
            m = r.index.normalize().isin(sel)
            ic = information_coefficient(r[col][m], fc[m], r.index.normalize()[m], n_boot=30)["ic"]
            if np.isfinite(ic) and ic < best_ic:
                best, best_ic = k, ic
        out[hyp] = (best, best_ic)
    return out


def assemble_sig(res, sf, chosen, cfg, model):
    feats = res["features"]
    sig = pd.DataFrame(index=feats.index)
    sig["F"], sig["F_fut"], sig["fut_spread"], sig["T"] = feats["F"], feats["F_fut"], feats["fut_spread"], feats["T"]
    sig["atm_iv"] = feats[f"{model}_atm_iv"]
    for hyp, (fair, _) in chosen.items():
        if fair is None:
            continue
        z = hypothesis_signals(sf["resid"][fair].rename(columns={HYP_FACTOR[hyp]: HYP_FACTOR[hyp]}))
        if hyp in z:
            sig[hyp] = z[hyp]
    sig = sig.join(pillar_rv_signal(feats, model))
    return sig


# ---------------------------------------------------------------- strategy grid
def strategy_zoo(cfg):
    L, lot = 10, cfg.lot_size
    return {
        "ATM Straddle": ATMStraddle(lots=L, lot_size=lot),
        "Risk Reversal 25d": RiskReversal(lots=L, lot_size=lot, delta=0.25),
        "Risk Reversal 10d": RiskReversal(lots=L, lot_size=lot, delta=0.10),
        "Butterfly 10d": VolButterfly(delta=0.10, lots=L, lot_size=lot),
        "Butterfly 25d": VolButterfly(delta=0.25, lots=L, lot_size=lot),
        "Butterfly 35d (ATM-ish)": VolButterfly(delta=0.35, lots=L, lot_size=lot),
        "Vertical 25/10d": VerticalSpread(lots=L, lot_size=lot),
        "Smile RV": SmileRV(lots=L, lot_size=lot),
    }


def default_grid(cfg):
    hedges = [HedgeRule("interval", 5, cfg.lot_size), HedgeRule("interval", 15, cfg.lot_size),
              HedgeRule("threshold", 4 * cfg.lot_size, cfg.lot_size), HedgeRule("ww", 1.0, cfg.lot_size)]
    return [dict(entry_thr=t, hedge=h) for t in (1.5, 2.0, 2.5) for h in hedges]


def cfg_key(c):
    return f"thr={c['entry_thr']:g}|hedge={c['hedge'].label()}"


def run_trade_book(store, sig, strategies, grid, cfg, costs, scenario="full_slip", days=None, progress=False):
    book = {}
    for sname, strat in strategies.items():
        for c in grid:
            p = BTParams(entry_thr=c["entry_thr"], hedge=c["hedge"], scenario=scenario)
            if progress:
                print(f"    {sname} {cfg_key(c)}")
            book[(sname, cfg_key(c))] = run_backtest(store, sig, strat, p, costs, cfg, days=days)
    return book


def slice_days(trades, days):
    if not len(trades):
        return trades
    return trades[trades.day.isin(pd.DatetimeIndex(days))]


def sharpe_of(trades, days, cfg):
    return metrics(slice_days(trades, days), days, cfg).get("net_sharpe", np.nan)


def select_config(book, sname, days, cfg, min_trades=10):
    """Best config by NET Sharpe on `days` only (ties/NaN ignored)."""
    best, best_s = None, -np.inf
    for (s, key), t in book.items():
        if s != sname:
            continue
        tt = slice_days(t, days)
        if len(tt) < min_trades:
            continue
        m = metrics(tt, days, cfg)
        if np.isfinite(m["net_sharpe"]) and m["net_sharpe"] > best_s:
            best, best_s = key, m["net_sharpe"]
    return best
