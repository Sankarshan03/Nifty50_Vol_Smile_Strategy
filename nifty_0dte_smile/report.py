"""End-to-end research run: produces tables, plots and RESEARCH_REPORT.md with explicit A-E verdicts."""
import copy
import pickle
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest.costs import CostModel
from .backtest.engine import BTParams, ChainStore, run_backtest
from .backtest.pnl import attribution, metrics, pnl_by
from .config import Config
from .data.cleaner import iv_failure_breakdown
from .data.validation import quote_report
from .factors.dynamics import add_dynamics
from .factors.pca import causal_pc_scores, smile_pca
from .hedging.delta_hedger import HedgeRule
from .research import (FACTOR_COLS, HYP_FACTOR, assemble_sig, build_signal_frames, choose_fair_per_hypothesis,
                       compare_models, cfg_key, default_grid, fair_model_rmse, predictability_all, run_trade_book,
                       select_config, slice_days, split_days, strategy_zoo, time_convention_sensitivity)
from .signals.mean_reversion import predictability_table
from .signals.relative_value import pca_factor_signals
from .statistics.bootstrap import bootstrap_sharpe
from .statistics.robustness import (edge_vs_uncertainty, perturbation_table, regime_table, regime_tags,
                                    robustness_score)
from .statistics.tests import benjamini_hochberg
from .strategies.butterfly import VolButterfly
from .strategies.risk_reversal import RiskReversal
from .strategies.vertical import VerticalSpread

STRAT_HYP = {"ATM Straddle": "atm_iv", "Risk Reversal 25d": "wing_spread", "Risk Reversal 10d": "wing_spread",
             "Butterfly 10d": "bf25", "Butterfly 25d": "bf25", "Butterfly 35d (ATM-ish)": "bf25",
             "Vertical 25/10d": "put_skew25", "Smile RV": None}


def _params_from_key(key, grid):
    for c in grid:
        if cfg_key(c) == key:
            return c
    raise KeyError(key)


def _rebuild(strategy, **kw):
    """Fresh strategy instance with perturbed delta parameters (strike perturbation)."""
    s = copy.deepcopy(strategy)
    if "delta" in kw:
        if isinstance(s, VolButterfly):
            s.delta = kw["delta"]
        elif isinstance(s, RiskReversal):
            s.params["delta"] = kw["delta"]
        elif isinstance(s, VerticalSpread):
            s.inner = kw["delta"]
    return s


def grade(pred_ok, pred_raw, insample_ok, oos_m, rob, edge, min_trades=30):
    """A strong candidate ... E false discovery. Conservative: A requires every pillar to hold."""
    oos_net = oos_m.get("total_net", 0.0)
    oos_sh = oos_m.get("net_sharpe", np.nan)
    n_oos = oos_m.get("n_trades", 0)
    if insample_ok and (not (oos_net > 0) or rob < 0.3):
        return "E", "profitable in-sample (train+val) but fails out-of-sample and/or robustness"
    fails = []
    if not (oos_net > 0):
        fails.append("OOS net P&L <= 0")
    if not (np.isfinite(oos_sh) and oos_sh > 1.0):
        fails.append("OOS net Sharpe <= 1")
    if rob < 0.6:
        fails.append(f"robustness {rob:.2f} < 0.6")
    if not edge.get("passes"):
        fails.append("edge <= model uncertainty")
    if n_oos < min_trades:
        fails.append(f"only {n_oos} OOS trades (< {min_trades})")
    if pred_ok and not fails:
        return "A", "FDR-significant predictability, positive OOS net Sharpe>1, robust, edge > model uncertainty"
    if pred_ok:
        return "B", "FDR-significant predictive relationship, but: " + "; ".join(fails)
    if pred_raw:
        return "C", "nominally significant only before multiple-testing correction / unstable"
    return "D", "no statistically significant predictive relationship"


def run_all(options, underlying, out_dir="reports", cfg=None, n_jobs=1, data_label="UNSPECIFIED", progress=True,
            cache=True, horizons=(5, 10, 15, 30, 60)):
    warnings.filterwarnings("ignore")
    from .pipeline import build
    cfg = cfg or Config()
    out = Path(out_dir)
    (out / "tables").mkdir(parents=True, exist_ok=True)
    (out / "plots").mkdir(parents=True, exist_ok=True)
    log = (lambda *a: print(*a, flush=True)) if progress else (lambda *a: None)

    # ---------------- 1. pipeline
    cp = out / "pipeline_cache.pkl"
    if cache and cp.exists():
        res = pickle.load(open(cp, "rb"))
        log("loaded cached pipeline")
    else:
        log("building smile pipeline ...")
        res = build(options, underlying, cfg, n_jobs=n_jobs)
        if cache:
            pickle.dump(res, open(cp, "wb"))
    feats, chain, raw = res["features"], res["chain"], res["raw"]
    days_all = sorted(feats.date.unique())
    T = {}

    # ---------------- 2. data quality
    T["quote_quality"] = quote_report(raw).to_frame("value")
    T["iv_solver_status"] = iv_failure_breakdown(chain).to_frame("rows")
    fw = feats[["F", "F_fut", "F_pcp"]].dropna()
    T["forward_comparison"] = pd.DataFrame({
        "pcp_minus_fut_mean": [(fw.F_pcp - fw.F_fut).mean()], "pcp_minus_fut_std": [(fw.F_pcp - fw.F_fut).std()],
        "spot_minus_fut_mean": [(feats.spot - feats.F_fut).mean()], "final_minus_fut_std": [(fw.F - fw.F_fut).std()],
        "pcp_available_frac": [feats.F_pcp.notna().mean()]})
    T["forward_source"] = feats.F_src.value_counts().to_frame("timestamps")

    # ---------------- 3. smile model comparison -> primary model
    log("comparing smile models ...")
    comp = compare_models(res, cfg)
    T["smile_model_comparison"] = comp
    cfg.primary_model = comp["cv_rmse"].idxmin()
    model = cfg.primary_model
    T["time_convention_sensitivity"] = time_convention_sensitivity(feats, model)
    log("  primary smile model:", model)

    # ---------------- 4. periods (chronological; test is locked)
    per = split_days(days_all, cfg.split_fracs)
    sel_days = list(per["train"]) + list(per["val"])
    T["periods"] = pd.DataFrame({k: [len(v), v[0] if len(v) else None, v[-1] if len(v) else None]
                                 for k, v in per.items()}, index=["days", "first", "last"]).T

    # ---------------- 5. factors, fair smile, residual predictability
    log("fair-smile models and residual tests ...")
    sf = build_signal_frames(res, cfg, model, sel_days)
    obs, ctx, freq = sf["obs"], sf["ctx"], sf["freq"]
    T["fair_model_rmse"] = fair_model_rmse(obs, sf["fairs"]).pivot(index="factor", columns="fair", values="rmse")
    pa = predictability_all(sf, horizons)
    pc = smile_pca(feats, model)
    pcs = causal_pc_scores(feats, model)
    if len(pcs):
        dev = pcs - pcs.groupby(pcs.index.normalize()).transform(lambda x: x.rolling(12, min_periods=4).mean().shift(1))
        h5 = predictability_table(dev, pcs, freq, horizons)
        if len(h5):
            h5.insert(0, "fair", "pca_trailing_mean")
            pa = pd.concat([pa, h5], ignore_index=True)
            pa["p_adj_bh"], pa["fdr_reject"] = benjamini_hochberg(pa["ic_p"].values)
            pa["mean_reverting"] = (pa["ic"] < 0) & (pa["beta_dchange"] < 0)
    T["predictability"] = pa
    T["pca_explained"] = pc["explained"].to_frame("explained_var")
    T["pca_loadings"] = pc["loadings"]
    fac_stats = {}
    spot_ret = np.log(feats.F).groupby(feats.date).diff()
    for c in pc["scores"]:
        s = pc["scores"][c]
        fac_stats[c] = dict(label=pc["labels"][c], ac1=s.groupby(s.index.normalize()).apply(lambda x: x.autocorr(1)).mean(),
                            corr_spot_ret=s.diff().corr(spot_ret), corr_rv=s.corr(ctx.rv30), corr_vix=s.corr(ctx.vix),
                            corr_tau=s.corr(ctx.tau))
    for c in ["atm_iv", "put_skew25", "bf25"]:
        s = obs[c]
        fac_stats[c] = dict(label="raw", ac1=s.groupby(s.index.normalize()).apply(lambda x: x.autocorr(1)).mean(),
                            corr_spot_ret=s.diff().corr(spot_ret), corr_rv=s.corr(ctx.rv30), corr_vix=s.corr(ctx.vix),
                            corr_tau=s.corr(ctx.tau))
    T["factor_stats"] = pd.DataFrame(fac_stats).T
    dyn = add_dynamics(obs[["atm_iv", "put_skew25", "call_skew25", "bf25", "curv", "slope"]].join(
        feats[[f"{model}_p10", f"{model}_p25", f"{model}_c25", f"{model}_c10"]].rename(columns=lambda x: x.replace(f"{model}_", "iv_"))),
        ["atm_iv", "put_skew25", "bf25", "curv"])
    T["dynamics_summary"] = dyn.describe().T
    bucket = feats.index.hour * 60 + feats.index.minute
    tb = pd.cut(bucket, [555, 600, 660, 720, 780, 840, 900, 931], right=False,
                labels=["09:15-10:00", "10:00-11:00", "11:00-12:00", "12:00-13:00", "13:00-14:00", "14:00-15:00", "15:00-15:30"])
    xs_res = sf["resid"]["xs"]
    T["time_of_day"] = pd.DataFrame({"atm_iv": obs.atm_iv, "put_skew25": obs.put_skew25, "bf25": obs.bf25,
                                     "atm_xs_resid_std": xs_res.atm_iv, "bf25_xs_resid_std": xs_res.bf25,
                                     "skew_xs_resid_std": xs_res.put_skew25}).groupby(tb).agg(
        {"atm_iv": "mean", "put_skew25": "mean", "bf25": "mean", "atm_xs_resid_std": "std", "bf25_xs_resid_std": "std",
         "skew_xs_resid_std": "std"})

    # ---------------- 6. signals (fair model chosen on selection days only)
    chosen = choose_fair_per_hypothesis(sf, sel_days)
    T["chosen_fair_model"] = pd.DataFrame({h: {"fair_model": v[0], "sel_IC": v[1]} for h, v in chosen.items()}).T
    sig = assemble_sig(res, sf, chosen, cfg, model)

    # ---------------- 7. strategy grid on all days (selection later slices by day => no test leakage)
    log("running strategy grid ...")
    store, costs = ChainStore(chain), CostModel()
    zoo, grid = strategy_zoo(cfg), default_grid(cfg)
    book = run_trade_book(store, sig, zoo, grid, cfg, costs, "full_slip", days_all, progress=False)

    # ---------------- 8. select -> lock -> report by period; walk-forward folds
    tags = regime_tags(feats, ctx, chain)
    leader, perf_rows, scen_rows, attr_rows, fold_rows, rob_tabs, reg_tabs, eq = [], [], [], [], [], {}, {}, {}
    n_after = len(days_all) - len(per["train"])
    blocks = np.array_split(np.array(days_all[len(per["train"]):]), 3) if n_after >= 3 else []
    fac_rmse = T["fair_model_rmse"]
    for sname, strat in zoo.items():
        key = select_config(book, sname, sel_days, cfg)
        if key is None:
            leader.append(dict(strategy=sname, grade="D", why="too few trades to evaluate", config=None))
            continue
        c = _params_from_key(key, grid)
        tr = book[(sname, key)]
        m_ = {p: metrics(slice_days(tr, per[p]), per[p], cfg, p) for p in per}
        m_all = metrics(tr, days_all, cfg, "all")
        for p in per:
            perf_rows.append(dict(strategy=sname, config=key, period=p, **{k: v for k, v in m_[p].items() if k != "label"}))
        eq[sname] = tr.groupby("day").net_pnl.sum().reindex(days_all, fill_value=0.0).cumsum()

        # cost scenarios on the locked config
        for scen in ("mid", "half", "full", "full_slip"):
            t2 = run_backtest(store, sig, strat, BTParams(entry_thr=c["entry_thr"], hedge=c["hedge"], scenario=scen),
                              costs, cfg, days=days_all)
            mm = metrics(slice_days(t2, per["test"]), per["test"], cfg)
            ma = metrics(t2, days_all, cfg)
            scen_rows.append(dict(strategy=sname, scenario=scen, gross_pnl_test=mm.get("total_gross"),
                                  net_pnl_test=mm.get("total_net"), net_sharpe_test=mm.get("net_sharpe"),
                                  net_pnl_all=ma.get("total_net"), net_sharpe_all=ma.get("net_sharpe"), trades_all=ma["n_trades"]))
        # attribution (test and all)
        for p, t3 in (("test", slice_days(tr, per["test"])), ("all", tr)):
            a = attribution(t3)
            attr_rows.append(dict(strategy=sname, period=p, **a.to_dict()))
        # walk-forward folds: config re-selected using ONLY days before each block
        for bi, blk in enumerate(blocks):
            before = [d for d in days_all if d < blk[0]]
            k2 = select_config(book, sname, before, cfg)
            if k2 is None:
                continue
            mb = metrics(slice_days(book[(sname, k2)], blk), list(blk), cfg)
            fold_rows.append(dict(strategy=sname, fold=bi + 1, first=blk[0], last=blk[-1], config=k2,
                                  n_trades=mb["n_trades"], net_pnl=mb.get("total_net", 0.0), net_sharpe=mb.get("net_sharpe")))

        # robustness (measured on the OOS test block; in-sample reported alongside)
        def run_fn(pdict, _strat=strat, _c=c):
            st = _rebuild(_strat, delta=pdict["delta"]) if "delta" in pdict and pdict["delta"] is not None else _strat
            hedge = pdict.get("hedge", _c["hedge"])
            cm = CostModel(fee_mult=pdict.get("fee_mult", 1.0), slip_mult=pdict.get("slip_mult", 1.0))
            es, ee = pdict.get("entry_start"), pdict.get("entry_end")
            t2 = run_backtest(store, sig, st, BTParams(entry_thr=pdict["entry_thr"], hedge=hedge, scenario="full_slip",
                                                       entry_start=es, entry_end=ee), cm, cfg, days=days_all)
            out_ = {}
            for nm, dd in (("oos", per["test"]), ("is", sel_days)):
                mm = metrics(slice_days(t2, dd), dd, cfg)
                out_[f"total_net_{nm}"] = mm.get("total_net", 0.0)
                out_[f"net_sharpe_{nm}"] = mm.get("net_sharpe", np.nan)
            out_["n_trades"] = len(t2)
            return out_

        base_p = dict(entry_thr=c["entry_thr"], hedge=c["hedge"], delta=getattr(strat, "delta", None) or
                      strat.params.get("delta") or getattr(strat, "inner", None))
        grids = {"entry_thr": [t for t in (1.5, 1.75, 2.0, 2.25, 2.5) if t != c["entry_thr"]],
                 "fee_mult": [2.0], "slip_mult": [2.0, 4.0],
                 "entry_start": [cfg.entry_start_min + 30, cfg.entry_start_min + 90],
                 "entry_end": [cfg.entry_end_min - 60, cfg.entry_end_min - 120],
                 "hedge": [h for h in (HedgeRule("interval", 5, cfg.lot_size), HedgeRule("interval", 15, cfg.lot_size),
                                       HedgeRule("threshold", 4 * cfg.lot_size, cfg.lot_size), HedgeRule("none"))
                           if h.label() != c["hedge"].label()]}
        if base_p["delta"]:
            grids["delta"] = [round(base_p["delta"] + dd, 2) for dd in (-0.05, 0.05) if base_p["delta"] + dd > 0.03]
        rows = [dict(param="base", value="", **run_fn(base_p))]
        for k, vals in grids.items():
            for v in vals:
                pp = {**base_p, k: v}
                rows.append(dict(param=k, value=(v.label() if isinstance(v, HedgeRule) else v), **run_fn(pp)))
        rt = pd.DataFrame(rows)
        rob_oos = robustness_score(rt.rename(columns={"total_net_oos": "total_net"}))
        rob_is = robustness_score(rt.rename(columns={"total_net_is": "total_net"}))
        rob_tabs[sname] = rt
        reg_tabs[sname] = regime_table(slice_days(tr, per["test"]), tags)
        T[f"pnl_by_month__{sname}"] = pnl_by(tr, "month")
        T[f"pnl_by_tod__{sname}"] = pnl_by(tr, "tod")
        T[f"pnl_by_day__{sname}"] = pnl_by(tr, "day")

        # edge vs model uncertainty (OOS trades)
        fc = STRAT_HYP.get(sname)
        rm = np.nan
        if fc is not None:
            r_ = chosen.get({"atm_iv": "H1_atm", "wing_spread": "H3_wing", "bf25": "H4_curv", "put_skew25": "H2_skew"}[fc], (None,))[0]
            if r_ in fac_rmse.columns:
                rm = float(fac_rmse.loc[fc, r_])
        tt = slice_days(tr, per["test"])
        edge = edge_vs_uncertainty(tt, rm, float(tt.entry_gross_vega.mean()) if len(tt) else np.nan)

        # predictive evidence for the signal this strategy trades
        if fc is not None:
            r_ = chosen[{"atm_iv": "H1_atm", "wing_spread": "H3_wing", "bf25": "H4_curv", "put_skew25": "H2_skew"}[fc]][0]
            pp_ = pa[(pa.factor == fc) & (pa.fair == r_)]
            pred_ok = bool(((pp_.fdr_reject) & (pp_.mean_reverting)).any())
            pred_raw = bool((((pp_.ic_p < 0.05)) & pp_.mean_reverting).any())
        else:   # Smile RV: its premise is neighbour-implied fair; use the xs residual tests of the pillars' factors
            pp_ = pa[(pa.fair == "xs") & (pa.factor.isin(["atm_iv", "put_skew25", "call_skew25", "bf25"]))]
            pred_ok = bool(((pp_.fdr_reject) & (pp_.mean_reverting)).any())
            pred_raw = bool((((pp_.ic_p < 0.05)) & pp_.mean_reverting).any())
        is_m = metrics(slice_days(tr, sel_days), sel_days, cfg)
        insample_ok = bool(is_m.get("total_net", 0) > 0 and (is_m.get("net_sharpe") or 0) > 0.5)
        rob = rob_oos if np.isfinite(rob_oos) else 0.0
        g, why = grade(pred_ok, pred_raw, insample_ok, m_["test"], rob, edge)
        bs = bootstrap_sharpe(slice_days(tr, per["test"]).groupby("day").net_pnl.sum().reindex(per["test"], fill_value=0.0).values,
                              cfg.trading_days)
        gross_m = metrics(slice_days(book_gross(store, sig, strat, c, costs, cfg, days_all), per["test"]), per["test"], cfg)
        leader.append(dict(strategy=sname, config=key, gross_sharpe_test=gross_m.get("net_sharpe"),
                           net_sharpe_test=m_["test"].get("net_sharpe"), net_sharpe_all=m_all.get("net_sharpe"),
                           max_dd_test=m_["test"].get("max_dd"), win_rate_test=m_["test"].get("win_rate"),
                           trades_test=m_["test"]["n_trades"], trades_all=m_all["n_trades"],
                           net_pnl_test=m_["test"].get("total_net"), net_pnl_is=is_m.get("total_net"),
                           sharpe_ci_test=f"[{bs['lo']:.1f}, {bs['hi']:.1f}]", avg_hold_min=m_all.get("avg_hold_min"),
                           turnover=m_all.get("turnover"), robustness_oos=rob_oos, robustness_is=rob_is,
                           edge=edge["observed_edge"], model_uncertainty=edge["model_uncertainty"],
                           edge_gt_uncertainty=edge["passes"], predictive_fdr=pred_ok, grade=g, why=why))
        log(f"  {sname:28s} {key:28s} test net {m_['test'].get('total_net', 0):>10.0f}  grade {g}")

    T["performance_by_period"] = pd.DataFrame(perf_rows)
    T["cost_scenarios"] = pd.DataFrame(scen_rows)
    T["pnl_attribution"] = pd.DataFrame(attr_rows)
    T["walk_forward_folds"] = pd.DataFrame(fold_rows)
    T["leaderboard"] = pd.DataFrame(leader)
    for k, v in rob_tabs.items():
        T[f"robustness__{k}"] = v
    for k, v in reg_tabs.items():
        T[f"regimes_test__{k}"] = v
    for k, v in T.items():
        if isinstance(v, (pd.DataFrame, pd.Series)):
            v.to_csv(out / "tables" / f"{k.replace('/', '_').replace(' ', '_')}.csv")
    # trade logs (with execution detail) for the locked configs
    logs = []
    for (sname, key), t in book.items():
        if any(l.get("strategy") == sname and l.get("config") == key for l in leader):
            logs.append(t.assign(strategy=sname).drop(columns=["fills"], errors="ignore"))
    if logs:
        pd.concat(logs).to_csv(out / "tables" / "trade_log_locked_configs.csv", index=False)

    from .visualization.plots import make_plots
    make_plots(out / "plots", res, model, sf, pa, pc, eq, per, T)
    from .visualization.writeup import write_report
    write_report(out, cfg, data_label, res, model, per, T, chosen, pa, pc, sf)
    return dict(tables=T, res=res, sig=sig, book=book, periods=per, sf=sf)


def book_gross(store, sig, strat, c, costs, cfg, days):
    """Same locked config at mid prices and zero fees: the 'gross' leg of the leaderboard."""
    z = CostModel(brokerage_per_order=0, stt_opt_sell=0, stt_fut_sell=0, txn_opt=0, txn_fut=0, sebi=0, gst=0,
                  stamp_opt_buy=0, stamp_fut_buy=0)
    return run_backtest(store, sig, strat, BTParams(entry_thr=c["entry_thr"], hedge=c["hedge"], scenario="mid"), z, cfg,
                        days=days)
