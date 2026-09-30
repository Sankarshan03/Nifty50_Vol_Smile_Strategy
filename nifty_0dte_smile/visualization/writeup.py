"""Generate RESEARCH_REPORT.md: data-driven answers to the ten research questions and the A-E verdicts."""
import numpy as np
import pandas as pd

GRADE_TEXT = {"A": "Strong candidate", "B": "Interesting research signal", "C": "Weak signal", "D": "No evidence",
              "E": "False discovery"}


def md(df, floatfmt=4, index=True):
    if df is None or len(df) == 0:
        return "_(empty)_\n"
    d = df.copy()
    if index:
        d = d.reset_index()
    def f(x):
        if isinstance(x, (float, np.floating)):
            return "" if not np.isfinite(x) else (f"{x:.{floatfmt}g}" if abs(x) < 1e5 else f"{x:,.0f}")
        return str(x)
    head = "| " + " | ".join(map(str, d.columns)) + " |\n|" + "---|" * len(d.columns) + "\n"
    return head + "\n".join("| " + " | ".join(f(v) for v in r) + " |" for r in d.itertuples(index=False)) + "\n"


def _score(lb, folds):
    """Ranking score (NOT Sharpe alone): OOS net Sharpe, OOS consistency (folds), robustness, drawdown, turnover."""
    s = lb.copy()
    if not len(s) or "net_sharpe_test" not in s:
        return s
    rk = lambda x, asc=True: x.rank(pct=True, ascending=asc).fillna(0)
    fpos = folds.groupby("strategy").net_pnl.apply(lambda x: (x > 0).mean()) if len(folds) else pd.Series(dtype=float)
    s["oos_consistency"] = s.strategy.map(fpos).fillna(0)
    s["rank_score"] = (0.30 * rk(s.net_sharpe_test) + 0.20 * s.oos_consistency + 0.20 * s.robustness_oos.fillna(0)
                       + 0.10 * rk(s.max_dd_test, False) + 0.10 * rk(s.turnover, False) + 0.10 * rk(s.edge.fillna(-1e9)))
    return s.sort_values("rank_score", ascending=False)


def write_report(out, cfg, data_label, res, model, per, T, chosen, pa, pc, sf):
    feats, chain = res["features"], res["chain"]
    lb = T["leaderboard"]
    folds = T["walk_forward_folds"]
    lines = []
    w = lines.append
    w("# NIFTY 50 0DTE Volatility Smile: Research Report\n")
    w(f"**Dataset:** {data_label}  \n**Days:** {len(feats.date.unique())}  |  **Snapshots:** {len(feats)}  |  "
      f"**Primary smile model:** `{model}`  |  **Time convention:** `{cfg.time_convention}`\n")
    if "SYNTH" in data_label.upper():
        w("> **WARNING - synthetic data.** This run validates the pipeline only. Nothing below is evidence about the real "
          "NIFTY market. The generator's latent factors mean-revert by construction, quotes carry independent noise, and the "
          "spot path has no variance risk premium. Re-run on real option-chain data for research conclusions.\n")
    w("## Periods (chronological, locked test never used for any choice)\n")
    w(md(T["periods"]))

    # ---- verdict
    grades = lb.set_index("strategy")["grade"] if "grade" in lb else pd.Series(dtype=str)
    w("\n## Final decision\n")
    if (grades == "A").any():
        w("**At least one structure reached grade A** - see leaderboard. Treat as a candidate for paper trading only.\n")
    else:
        w("**NO RELIABLE TRADING EDGE FOUND.** No structure satisfied all of: FDR-significant predictability, positive "
          "out-of-sample net P&L after realistic costs, parameter/strike/time/cost robustness, and edge larger than model "
          "uncertainty.\n")
    if len(lb):
        w(md(lb[["strategy", "grade", "why"]], index=False))
        w("\nGrades: A strong candidate | B interesting research signal | C weak signal | D no evidence | E false discovery.\n")

    # ---- leaderboard
    w("\n## Strategy leaderboard (config chosen on train+validation only; metrics on locked test)\n")
    sc = _score(lb, folds)
    cols = ["strategy", "gross_sharpe_test", "net_sharpe_test", "sharpe_ci_test", "max_dd_test", "win_rate_test",
            "trades_test", "net_sharpe_all", "robustness_oos", "robustness_is", "edge", "model_uncertainty", "grade"]
    w(md(sc[[c for c in cols if c in sc]], 3, index=False))
    w("\nRanking is by a composite (30% OOS net Sharpe, 20% walk-forward fold consistency, 20% OOS robustness, 10% low "
      "drawdown, 10% low turnover, 10% edge), not by Sharpe alone. 'gross' = mid prices, zero fees; 'net' = full bid/ask + "
      "slippage + statutory costs.\n")
    w("\n### Net P&L by period for the locked configs\n")
    w(md(T["performance_by_period"][["strategy", "config", "period", "n_trades", "total_gross", "total_net", "net_sharpe",
                                      "max_dd", "win_rate", "profit_factor"]], 3, index=False))
    w("\n### Cost scenarios (locked test period)\n")
    w(md(T["cost_scenarios"], 3, index=False))
    w("\n### Walk-forward folds (config re-selected using only earlier days)\n")
    w(md(folds, 3, index=False))
    w("\n### P&L attribution (Rs)\n")
    w(md(T["pnl_attribution"], 0, index=False))
    w("\n`vega_level_pnl` = net vega x change in ATM IV; `vol_surface_pnl` = leg-level vega x (leg IV change - ATM IV change), "
      "i.e. the smile-shape component the strategies intend to harvest; `unexplained_pnl` is the discretisation residual.\n")

    # ---- Q1
    comp = T["smile_model_comparison"]
    q = chain[(chain.clean) & chain.iv_bid.notna() & chain.iv_ask.notna()]
    half = ((q.iv_ask - q.iv_bid) / 2).median()
    best = comp["cv_rmse"].idxmin()
    usable = (feats[f"{model}_ok"].fillna(False) & ~feats[f"{model}_arb"].fillna(True)).mean()
    w("\n## Answers to the research questions\n")
    w("### Q1. Can the 0DTE smile be modelled reliably?\n")
    w(md(comp, 4))
    w(f"\nBest hold-out model: `{best}` (cv RMSE {comp.loc[best, 'cv_rmse']:.4f} IV units vs typical quoted IV half-spread "
      f"{half:.4f}). Fit is {'inside' if comp.loc[best, 'cv_rmse'] < half else 'outside'} the quote noise band. "
      f"{usable:.0%} of snapshots give an arbitrage-free fit with the primary model. SVI is not assumed best; choice is by hold-out error.\n")
    w("\nTime-convention sensitivity (level shift only, exact under Black-76):\n")
    w(md(T["time_convention_sensitivity"], 4, index=False))

    # ---- Q2
    ev = pc["explained"]
    w("\n### Q2. Dominant smile factors\n")
    w(md(pd.DataFrame({"explained_var": ev, "label": pd.Series(pc["labels"])}), 3))
    w(f"\nFirst three PCs explain {ev.sum():.1%} of cross-sectional smile variance. Factor statistics:\n")
    w(md(T["factor_stats"], 3))

    # ---- Q3/4/5
    w("\n### Q3. Are the factors predictable?  Q4. Do residuals mean-revert?\n")
    sig_ = pa[pa.fdr_reject & pa.mean_reverting]
    w(f"{len(sig_)} of {len(pa)} (fair model, factor, horizon) tests show FDR-significant (BH, q=5%) mean-reverting predictive "
      f"power (negative IC and negative beta of the future smile change on the residual).\n")
    top = pa.sort_values("ic").head(12)[["fair", "factor", "h_min", "ic", "ic_lo", "ic_hi", "p_adj_bh", "beta_dchange", "phi",
                                          "half_life_min", "adf_t"]]
    w(md(top, 3, index=False))
    w("\nCaution: part of any measured reversion is quote noise / bid-ask bounce in the fitted smile, which is not capturable "
      "at executable prices. The backtest, not the IC, decides tradability.\n")
    hl = pa[(pa.phi > 0) & (pa.phi < 1)].groupby("fair").half_life_min.median()
    w("\n### Q5. Half-life of mispricing (median over factors, minutes)\n")
    w(md(hl.to_frame("half_life_min"), 3))
    w(f"\nAverage across fair models: **{hl.mean():.1f} min** (resolution is limited by the snapshot interval).\n")

    # ---- Q6
    w("\n### Q6. Which part of the smile carries the strongest signal?\n")
    per_factor = pa.groupby("factor").agg(min_ic=("ic", "min"), n_fdr=("fdr_reject", "sum"), best_p_adj=("p_adj_bh", "min"))
    w(md(per_factor.sort_values("min_ic"), 3))
    if len(sig_):
        bf = sig_.loc[sig_.ic.idxmin()]
        w(f"\nStrongest FDR-significant reversion signal: **{bf.factor}** (fair model `{bf.fair}`, h={bf.h_min:g} min, IC={bf.ic:.3f}).\n")
    else:
        w("\nNo factor survives FDR control; the lowest raw IC above is not a discovery.\n")

    # ---- Q7-10
    w("\n### Q7. Which structure best isolates the signal?\n")
    if len(sc):
        b = sc.iloc[0]
        w(f"Top composite rank: **{b.strategy}** ({b.config}); grade {b.grade}. Net test P&L Rs {b.net_pnl_test:,.0f}. "
          "The per-strategy statement of what each structure is betting on:\n")
    from ..strategies.butterfly import VolButterfly
    from ..strategies.risk_reversal import RiskReversal
    from ..strategies.smile_rv import SmileRV
    from ..strategies.straddle import ATMStraddle
    from ..strategies.vertical import VerticalSpread
    for s in (ATMStraddle, RiskReversal, VolButterfly, VerticalSpread, SmileRV):
        w(f"- **{s.name}**: {s.bet}")
    w("\n### Q8. Profitable after realistic costs?\n")
    sc_ = T["cost_scenarios"]
    if len(sc_):
        piv = sc_.pivot(index="strategy", columns="scenario", values="net_pnl_all")[["mid", "half", "full", "full_slip"]]
        w("Net P&L (all days, Rs) by execution scenario:\n")
        w(md(piv, 0))
        n_pos = int((piv["full_slip"] > 0).sum())
        w(f"\n{n_pos} of {len(piv)} structures are net-positive over all days at full bid/ask + slippage; see OOS below.\n")
    w("\n### Q9. Profitable out-of-sample?\n")
    pp = T["performance_by_period"]
    if len(pp):
        w(md(pp.pivot(index="strategy", columns="period", values="total_net")[["train", "val", "test"]], 0))
    w("\n### Q10. Survival under perturbations\n")
    w("Per-strategy perturbation tables (entry threshold 1.5-2.5, strike delta +-0.05, entry-time windows, hedge rule, 2x fees, "
      "2x/4x slippage) are in `tables/robustness__*.csv`; robustness score = half sign-retention, half gradual degradation:\n")
    w(md(lb[["strategy", "robustness_oos", "robustness_is", "edge", "model_uncertainty", "edge_gt_uncertainty"]], 3, index=False))
    w("\nRegime splits of OOS P&L (low/high VIX, rising/falling VIX, gap, realised vol, trend, dealer gamma sign) are in "
      "`tables/regimes_test__*.csv`; a strategy earning everything in one regime is not robust.\n")

    w("\n## Hypothesis signals and fair-smile models\n")
    w("Fair model chosen per hypothesis on train+val only (most negative IC of residual vs next-15-minute smile change):\n")
    w(md(T["chosen_fair_model"], 3))
    w("\nFair-model out-of-sample RMSE by factor (IV units):\n")
    w(md(T["fair_model_rmse"], 4))
    w("\n`hist` = same-time-of-day mean of earlier days (ATM VIX-scaled); `xs` = neighbour-implied (held-out polynomial); "
      "`ridge` = dynamic factor regression on VIX/tau/gamma/returns/realised vol/slow factor; `gbm` = gradient boosting. "
      "ML is only preferred if it beats the ridge model out-of-sample by a clear margin (compare columns above).\n")
    w("\n## Data-quality and forward checks\n")
    w(md(T["quote_quality"], 4)); w("\n"); w(md(T["iv_solver_status"], 4)); w("\n"); w(md(T["forward_comparison"], 4, index=False))
    w("\n## Expiry-day time buckets\n")
    w(md(T["time_of_day"], 4))
    w("\n## Plots\n")
    for p in ["smile_fits", "intraday_profile", "pca", "residual_ic", "equity_net", "attribution"]:
        w(f"![{p}](plots/{p}.png)")
    w("\n## Limitations\n- Fills assume one lot-multiple order fills at the next snapshot's bid/ask when volume >= 20x size; "
      "no quote sizes / queue modelling.\n- Statutory cost rates are configurable defaults in `backtest/costs.py`; verify before real use.\n"
      "- Short windows (one week of real data) make every statistic weak; the framework reports uncertainty rather than hiding it.\n"
      "- Regime tags use sample medians and are descriptive only.\n")
    (out / "RESEARCH_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
