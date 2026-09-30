"""Performance metrics and P&L attribution."""
import numpy as np
import pandas as pd

ATTR_COLS = ["delta_pnl", "gamma_pnl", "theta_pnl", "vega_level_pnl", "vol_surface_pnl", "unexplained_pnl",
             "hedge_pnl", "cost_pnl"]


def daily_pnl(trades, days, col="net_pnl"):
    s = pd.Series(0.0, index=pd.DatetimeIndex(days))
    if len(trades):
        g = trades.groupby("day")[col].sum()
        s.loc[g.index] = g.values
    return s


def _sharpe(x, ann):
    sd = x.std(ddof=1)
    return float(x.mean() / sd * np.sqrt(ann)) if len(x) > 2 and sd > 0 else np.nan


def metrics(trades, days, cfg, label=""):
    """Full strategy scorecard. Gross = before execution costs and fees (mid-to-mid); Net = after."""
    ann = cfg.trading_days
    net_d, gross_d = daily_pnl(trades, days, "net_pnl"), daily_pnl(trades, days, "gross_pnl")
    n = len(trades)
    out = {"label": label, "n_trades": n, "n_days": len(days)}
    if n == 0:
        return {**out, "total_gross": 0.0, "total_net": 0.0, "net_sharpe": np.nan, "gross_sharpe": np.nan}
    eq = net_d.cumsum()
    dd = (eq.cummax() - eq).max()
    ann_ret = net_d.mean() * ann / cfg.capital
    wins, losses = trades.net_pnl[trades.net_pnl > 0].sum(), -trades.net_pnl[trades.net_pnl < 0].sum()
    down = net_d[net_d < 0].std(ddof=1)
    out.update({
        "total_gross": trades.gross_pnl.sum(), "total_net": trades.net_pnl.sum(),
        "ann_return": ann_ret, "gross_sharpe": _sharpe(gross_d, ann), "net_sharpe": _sharpe(net_d, ann),
        "sortino": float(net_d.mean() / down * np.sqrt(ann)) if down and down > 0 else np.nan,
        "max_dd": float(dd), "calmar": float(ann_ret / (dd / cfg.capital)) if dd > 0 else np.nan,
        "win_rate": float((trades.net_pnl > 0).mean()), "profit_factor": float(wins / losses) if losses > 0 else np.inf,
        "avg_trade": trades.net_pnl.mean(), "median_trade": trades.net_pnl.median(),
        "worst_trade": trades.net_pnl.min(), "best_trade": trades.net_pnl.max(),
        "turnover": trades.premium_traded.sum() / cfg.capital, "avg_hold_min": trades.hold_min.mean(),
        "cost_per_trade": -trades.cost_pnl.mean(), "gross_edge": trades.gross_pnl.mean(), "net_edge": trades.net_pnl.mean(),
    })
    return out


def attribution(trades):
    """Sum of P&L components. delta+gamma+theta+vega+surface+unexplained = option gross; plus hedge and costs = net."""
    if not len(trades):
        return pd.Series(0.0, index=ATTR_COLS + ["net_pnl"])
    s = trades[ATTR_COLS + ["net_pnl"]].sum()
    return s


def pnl_by(trades, key):
    if not len(trades):
        return pd.DataFrame()
    t = trades.copy()
    if key == "month":
        t["k"] = t.day.dt.to_period("M").astype(str)
    elif key == "tod":
        t["k"] = (t.entry_mod // 60).astype(int).astype(str) + ":00"
    elif key == "expiry" or key == "day":
        t["k"] = t.day.dt.strftime("%Y-%m-%d")
    else:
        t["k"] = t[key]
    return t.groupby("k").agg(trades=("net_pnl", "size"), gross=("gross_pnl", "sum"), net=("net_pnl", "sum"))
