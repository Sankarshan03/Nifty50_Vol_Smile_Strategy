"""Raw option chain -> cleaned quotes -> forward -> IV -> 0DTE smile fits -> per-timestamp features."""
import numpy as np
import pandas as pd

from .config import Config
from .data.cleaner import add_price_sanity, clean_chain
from .data.loader import zero_dte
from .options.forward import forward_from_futures, forward_from_pcp, stable_forward
from .options.greeks import greeks
from .options.timeutil import minute_of_day, year_fraction
from .smile.arbitrage import check_smile
from .smile.delta_surface import delta_smile
from .smile.moneyness import DELTA_NAMES
from .smile.polynomial import fit_polynomial
from .smile.spline import fit_spline
from .smile.svi import fit_svi
from .signals.residual import cross_sectional_fair

FITTERS = {"poly": fit_polynomial, "spline": fit_spline, "svi": fit_svi}


def select_smile_points(g, cfg):
    """OTM, clean, solver-OK, reasonably tight, non-deep-wing options for one timestamp."""
    otm = ((g.cp > 0) & (g.strike >= g.F)) | ((g.cp < 0) & (g.strike < g.F))
    m = (g.clean & ~g.f_iv_fail & ~g.f_below_intrinsic & otm & (g.spread_pct <= cfg.max_fit_spread_pct)
         & (g.delta.abs() >= cfg.min_delta_abs))
    return g[m]


def smile_weights(p):
    width = np.maximum((p.iv_ask - p.iv_bid).fillna(p.iv_mid * 0.1).values, 0.002)
    return 1.0 / width ** 2


def _features(fit, prefix, T):
    f = {}
    atm = float(fit.iv(0.0))
    sd = atm * np.sqrt(T)
    h = 0.5 * sd
    ivp, ivm = float(fit.iv(h)), float(fit.iv(-h))
    f[f"{prefix}atm_iv"] = atm
    f[f"{prefix}slope"] = (ivp - ivm) / (2 * h) * sd          # dIV per one ATM sigma-move
    f[f"{prefix}curv"] = (ivp + ivm - 2 * atm) / 0.25          # d2IV/dx2, x = k/sd
    sm = delta_smile(fit)
    for name, (iv, k, ext) in sm.items():
        f[f"{prefix}{name}"] = iv
        f[f"{prefix}{name}_ext"] = ext
        f[f"{prefix}{name}_k"] = k
    return f


def process_day_snapshot(ts, g, und, cfg):
    """One timestamp: returns (enriched chain rows, feature dict)."""
    T = float(year_fraction([ts], cfg.time_convention)[0])
    f_fut = forward_from_futures(und.get("fut_bid"), und.get("fut_ask"), und.get("fut"))
    cl = g[g.clean]
    ref = und["spot"] if np.isfinite(und["spot"]) else f_fut
    f_pcp = forward_from_pcp(cl, T, cfg.r, ref=ref)
    F, src = stable_forward(f_fut, f_pcp, und["spot"], T, cfg.r)
    g = g.copy()
    g["F"], g["T"] = F, T
    g["k"] = np.log(g.strike / F)
    g = add_price_sanity(g, np.full(len(g), F), np.full(len(g), T), cfg.r)
    iv_for_greeks = g["iv_mid"].fillna(0.0).values
    gr = greeks(F, g.strike.values, T, np.where(iv_for_greeks > 0, iv_for_greeks, np.nan), g.cp.values, cfg.r)
    for kx, v in gr.items():
        g[kx] = np.where(g["iv_mid"].notna(), v, np.nan)
    feat = {"timestamp": ts, "F": F, "F_fut": f_fut, "F_pcp": f_pcp, "F_src": src, "T": T, "spot": und["spot"],
            "vix": und.get("vix", np.nan), "fut_spread": (und["fut_ask"] - und["fut_bid"]) if np.isfinite(und.get("fut_ask", np.nan)) else np.nan, "n_clean": int(g.clean.sum()), "n_rows": len(g)}
    pts = select_smile_points(g, cfg)
    feat["n_fit"] = len(pts)
    if len(pts) >= cfg.min_fit_points:
        k, iv, w = pts.k.values, pts.iv_mid.values, smile_weights(pts)
        for name in cfg.smile_models:
            fit = FITTERS[name](k, iv, T, w)
            feat[f"{name}_ok"] = fit.ok
            feat[f"{name}_rmse"] = fit.rmse
            if not fit.ok:
                continue
            arb = check_smile(fit, F, T, cfg.r)
            feat[f"{name}_arb"] = arb["any"]
            feat.update(_features(fit, f"{name}_", T))
            feat.update({f"xs_{name}_{k[3:]}": v for k, v in cross_sectional_fair(pts, fit, F, T).items()})
    return g, feat


def _process_day(args):
    day_raw, u_day, cfg = args
    chains, feats = [], []
    for ts, g in day_raw.groupby("timestamp"):
        ts = pd.Timestamp(ts)
        if ts not in u_day.index:
            continue
        gg, feat = process_day_snapshot(ts, g[~g.f_duplicate], u_day.loc[ts], cfg)
        chains.append(gg)
        feats.append(feat)
    return chains, feats


def build(options, underlying, cfg=None, n_jobs=1):
    """Full pipeline. Returns dict(raw, chain, features). Only information at each timestamp is used.
    Days are independent, so they can be processed in parallel (n_jobs>1)."""
    cfg = cfg or Config()
    o = zero_dte(options)
    raw, _ = clean_chain(o, cfg.quote_rules)
    u = underlying.set_index("timestamp")
    jobs = [(d, u[u.index.normalize() == day], cfg) for day, d in raw.groupby(raw.timestamp.dt.normalize())]
    if n_jobs > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(n_jobs) as ex:
            res = list(ex.map(_process_day, jobs))
    else:
        res = [_process_day(j) for j in jobs]
    chains = [c for r in res for c in r[0]]
    feats = [f for r in res for f in r[1]]
    chain = pd.concat(chains, ignore_index=True)
    features = pd.DataFrame(feats).set_index("timestamp").sort_index()
    features["date"] = features.index.normalize()
    features["mod"] = minute_of_day(features.index)
    return {"raw": raw, "chain": chain, "features": features}
