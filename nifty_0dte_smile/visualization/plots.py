"""Diagnostic plots (matplotlib, headless)."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..pipeline import FITTERS, select_smile_points, smile_weights
from ..config import Config


def _save(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def make_plots(out, res, model, sf, pa, pc, eq, per, T):
    feats, chain = res["features"], res["chain"]
    cfg = Config()
    # 1. smile fits at a mid-session timestamp
    ts = feats.index[(feats.n_fit >= 10) & (feats.index.hour == 11)]
    if len(ts):
        ts = ts[len(ts) // 2]
        g = chain[chain.timestamp == ts]
        pts = select_smile_points(g, cfg)
        fig, ax = plt.subplots(1, 2, figsize=(11, 4))
        kk = np.linspace(pts.k.min() * 1.1, pts.k.max() * 1.1, 200)
        for name, fn in FITTERS.items():
            fit = fn(pts.k.values, pts.iv_mid.values, pts["T"].iloc[0], smile_weights(pts))
            if fit.ok:
                ax[0].plot(kk, fit.iv(kk), label=name)
                ax[1].plot(kk, fit.total_var(kk) * 1e6, label=name)
        ax[0].errorbar(pts.k, pts.iv_mid, yerr=[pts.iv_mid - pts.iv_bid, pts.iv_ask - pts.iv_mid], fmt="k.", alpha=.5,
                       label="mid (bid/ask IV bars)")
        ax[0].set(title=f"IV vs log-moneyness {ts}", xlabel="k = ln(K/F)", ylabel="IV"); ax[0].legend()
        ax[1].set(title="Total variance w(k)=IV^2 T (x1e6)", xlabel="k"); ax[1].legend()
        _save(fig, out / "smile_fits.png")
    # 2. intraday factor profile
    tod = T["time_of_day"]
    fig, ax = plt.subplots(1, 3, figsize=(13, 3.5))
    tod["atm_iv"].plot(ax=ax[0], marker="o", title="Mean ATM IV"); tod["put_skew25"].plot(ax=ax[1], marker="o", title="Mean 25d put skew")
    tod["bf25"].plot(ax=ax[2], marker="o", title="Mean 25d butterfly")
    for a in ax: a.tick_params(axis="x", rotation=45)
    _save(fig, out / "intraday_profile.png")
    # 3. PCA
    fig, ax = plt.subplots(1, 2, figsize=(11, 3.8))
    pc["explained"].plot.bar(ax=ax[0], title="PCA explained variance (smile pillars)")
    pc["loadings"].plot(ax=ax[1], marker="o", title="Loadings by delta pillar")
    _save(fig, out / "pca.png")
    # 4. IC by horizon
    fig, ax = plt.subplots(figsize=(7, 4))
    for (fair, f), g in pa[pa.fair.isin(["xs", "hist"])].groupby(["fair", "factor"]):
        if f in ("atm_iv", "wing_spread", "bf25", "put_skew25"):
            ax.plot(g.h_min, g.ic, marker="o", label=f"{f}/{fair}")
    ax.axhline(0, c="k", lw=.5); ax.set(xlabel="horizon (min)", ylabel="IC(resid, future change)", title="Residual predictive IC"); ax.legend(fontsize=6)
    _save(fig, out / "residual_ic.png")
    # 5. equity curves
    if eq:
        fig, ax = plt.subplots(figsize=(9, 4.5))
        for k, s in eq.items():
            ax.plot(range(len(s)), s.values, label=k)
        ax.axvline(len(per["train"]), c="gray", ls="--"); ax.axvline(len(per["train"]) + len(per["val"]), c="r", ls="--")
        ax.set(title="Cumulative NET P&L (full bid/ask + slippage). Dashed: train|val, red = locked test start", xlabel="day index")
        ax.legend(fontsize=6)
        _save(fig, out / "equity_net.png")
    # 6. attribution
    at = T.get("pnl_attribution")
    if at is not None and len(at):
        a = at[at.period == "all"].set_index("strategy").drop(columns=["period", "net_pnl"])
        fig, ax = plt.subplots(figsize=(10, 4.5))
        a.plot.bar(stacked=True, ax=ax, title="P&L attribution (all days, Rs)")
        _save(fig, out / "attribution.png")
