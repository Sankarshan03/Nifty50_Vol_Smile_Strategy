"""Event-driven intraday backtester (one position at a time, flat by the close, no look-ahead).

At each snapshot t the engine only sees rows up to t:
  1 load chain/features(t)  2-4 smile, fair smile, residual signal are PRECOMPUTED CAUSALLY in `sig`
  5 signal -> 6 execution check -> 7 entry (at the NEXT snapshot's quotes, latency_steps=1)
  8 hedge by rule -> 9 mark-to-market + Greek P&L attribution -> 10 exit by pre-set rules.
"""
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from ..config import Config
from ..hedging.delta_hedger import HedgeRule
from .costs import CostModel
from .execution import can_fill, make_fill


class ChainStore:
    """timestamp -> chain snapshot (DataFrame) with cached (strike, cp) row lookup."""

    def __init__(self, chain):
        self._g = {ts: g for ts, g in chain.groupby("timestamp")}
        self._idx = {}

    def snap(self, ts):
        return self._g.get(ts)

    def row(self, ts, strike, cp):
        if ts not in self._g:
            return None
        if ts not in self._idx:
            g = self._g[ts]
            self._idx[ts] = {(s, c): i for i, (s, c) in enumerate(zip(g.strike.values, g.cp.values))}
        i = self._idx[ts].get((strike, cp))
        return None if i is None else self._g[ts].iloc[i]


@dataclass
class BTParams:
    entry_thr: float = 2.0
    exit_thr: float = 0.25
    max_hold_min: int = 60
    stop_loss: Optional[float] = None       # Rs, on gross (mid) P&L of the open trade
    scenario: str = "full_slip"             # mid | half | full | full_slip
    hedge: HedgeRule = field(default_factory=lambda: HedgeRule("interval", 5))
    entry_start: Optional[int] = None       # minute-of-day window for entries (defaults from Config)
    entry_end: Optional[int] = None
    latency_steps: int = 1


COMPONENTS = ["delta_pnl", "gamma_pnl", "theta_pnl", "vega_level_pnl", "vol_surface_pnl", "unexplained_pnl"]


def _valid_quote(r):
    return r is not None and r["bid"] > 0 and r["ask"] > r["bid"]


def _leg_state(r, prev=None):
    if r is not None and _valid_quote(r):
        return dict(mid=0.5 * (r["bid"] + r["ask"]), bid=r["bid"], ask=r["ask"],
                    iv=r["iv_mid"] if np.isfinite(r["iv_mid"]) else (prev or {}).get("iv", np.nan),
                    delta=r["delta"] if np.isfinite(r["delta"]) else (prev or {}).get("delta", 0.0),
                    gamma=r["gamma"] if np.isfinite(r["gamma"]) else (prev or {}).get("gamma", 0.0),
                    vega=r["vega"] if np.isfinite(r["vega"]) else (prev or {}).get("vega", 0.0),
                    theta=r["theta"] if np.isfinite(r["theta"]) else (prev or {}).get("theta", 0.0),
                    stale=False)
    if prev is None:
        return None
    p = dict(prev)
    p["stale"] = True
    return p


def run_backtest(store: ChainStore, sig: pd.DataFrame, strategy, p: BTParams, costs: CostModel, cfg: Config,
                 atm_col="atm_iv", days=None):
    """`sig` is indexed by timestamp and holds F, F_fut, fut_spread, T, atm_col and the strategy signal columns."""
    days = days if days is not None else sorted(sig.index.normalize().unique())
    es = p.entry_start if p.entry_start is not None else cfg.entry_start_min
    ee = p.entry_end if p.entry_end is not None else cfg.entry_end_min
    trades, rejected = [], 0
    lot = cfg.lot_size
    for day in days:
        d_sig = sig[sig.index.normalize() == day]
        stamps = list(d_sig.index)
        rows = d_sig.to_dict("index")
        pos = None            # open position
        pend_entry = None     # (legs, d, z, decided_ts)
        pend_exit = None
        for i, ts in enumerate(stamps):
            row = rows[ts]
            mod = ts.hour * 60 + ts.minute
            snap = store.snap(ts)
            last_step = i == len(stamps) - 1
            f_fut = row["F_fut"] if np.isfinite(row.get("F_fut", np.nan)) else row["F"]
            fut_half = 0.5 * (row["fut_spread"] if np.isfinite(row.get("fut_spread", np.nan)) else 1.0)

            # ---- mark-to-market + attribution (position carried from previous step) ----
            if pos is not None:
                dF, dT = row["F"] - pos["F"], pos["T"] - row["T"]
                d_atm = row[atm_col] - pos["atm"] if np.isfinite(row[atm_col]) and np.isfinite(pos["atm"]) else 0.0
                opt_pnl = dlt = gam = th = vlev = vsurf = 0.0
                for leg in pos["legs"]:
                    ns = _leg_state(store.row(ts, leg["strike"], leg["cp"]), leg["st"])
                    if ns is None:
                        continue
                    q, o = leg["qty"], leg["st"]
                    opt_pnl += q * (ns["mid"] - o["mid"])
                    dlt += q * o["delta"] * dF
                    gam += 0.5 * q * o["gamma"] * dF ** 2
                    th += q * o["theta"] * dT
                    d_iv = (ns["iv"] - o["iv"]) if np.isfinite(ns["iv"]) and np.isfinite(o["iv"]) else 0.0
                    vlev += q * o["vega"] * d_atm
                    vsurf += q * o["vega"] * (d_iv - d_atm)
                    leg["st"] = ns
                hedge_pnl = pos["hedge"] * (f_fut - pos["Ffut"])
                pos["opt_gross"] += opt_pnl
                pos["hedge_pnl"] += hedge_pnl
                for k, v in zip(COMPONENTS, [dlt, gam, th, vlev, vsurf, opt_pnl - (dlt + gam + th + vlev + vsurf)]):
                    pos[k] += v
                pos["F"], pos["T"], pos["Ffut"], pos["atm"] = row["F"], row["T"], f_fut, row[atm_col]
                pos["steps"] += 1

            # ---- execute pending orders (decided at the previous snapshot) ----
            if pend_exit and pos is not None:
                trades.append(_close(pos, ts, snap, store, row, f_fut, fut_half, p, costs, lot, pos.get("exit_reason") or "exit"))
                pos, pend_exit = None, None
            if pend_entry is not None and pos is None and snap is not None:
                legs, d, z = pend_entry
                pos = _open(legs, d, z, ts, snap, store, row, f_fut, fut_half, p, costs, cfg, strategy)
                if pos is None:
                    rejected += 1
                pend_entry = None

            # ---- hedge ----
            if pos is not None and p.hedge.kind != "none":
                nd = sum(l["qty"] * l["st"]["delta"] for l in pos["legs"])
                ng = sum(l["qty"] * l["st"]["gamma"] for l in pos["legs"])
                pos["mins_since_hedge"] += (ts - pos["last_ts"]).total_seconds() / 60 if pos["last_ts"] is not None else 0
                net = nd + pos["hedge"]
                if p.hedge.should_hedge(net, ng, row["F"], row["T"], pos["mins_since_hedge"],
                                        fut_half + costs.tick, cfg.r):
                    tgt = p.hedge.target_units(nd)
                    dq = tgt - pos["hedge"]
                    if dq != 0:
                        px, slip = _fut_px(f_fut, fut_half, dq, p.scenario, costs)
                        pos["hedge_cost"] += slip * abs(dq)
                        pos["fees"] += costs.fees(px, abs(dq), dq > 0, "fut")
                        pos["hedge"] = tgt
                        pos["n_hedges"] += 1
                    pos["mins_since_hedge"] = 0.0
            if pos is not None:
                pos["last_ts"] = ts

            # ---- exit decision ----
            if pos is not None and not pend_exit:
                z = strategy.z(row)
                gross = pos["opt_gross"] + pos["hedge_pnl"]
                held = (ts - pos["entry_ts"]).total_seconds() / 60
                reason = None
                if mod >= cfg.flat_by_min or last_step:
                    reason = "time"
                elif held >= p.max_hold_min:
                    reason = "max_hold"
                elif p.stop_loss is not None and gross <= -p.stop_loss:
                    reason = "stop"
                elif np.isfinite(z) and strategy.edge_remaining(z, pos["dir"]) <= p.exit_thr:
                    reason = "reverted"
                if reason:
                    pos["exit_reason"] = reason
                    if p.latency_steps == 0 or reason == "time" or last_step or i + 1 >= len(stamps):
                        trades.append(_close(pos, ts, snap, store, row, f_fut, fut_half, p, costs, lot, reason))
                        pos = None
                    else:
                        pend_exit = True

            # ---- entry decision ----
            if pos is None and pend_entry is None and es <= mod <= ee and not last_step and snap is not None:
                z = strategy.z(row)
                d = strategy.direction(z, p.entry_thr)
                if d != 0:
                    legs = strategy.legs(snap, {**row, **{"F": row["F"]}}, d)
                    if legs:
                        if p.latency_steps == 0:
                            pos = _open(legs, d, z, ts, snap, store, row, f_fut, fut_half, p, costs, cfg, strategy)
                            rejected += pos is None
                        else:
                            pend_entry = (legs, d, z)
    t = pd.DataFrame(trades)
    t.attrs["rejected_entries"] = rejected
    return t


def _fut_px(f, half, dq, scenario, costs):
    sgn = 1.0 if dq > 0 else -1.0
    adverse = {"mid": 0.0, "half": 0.5 * half, "full": half,
               "full_slip": half + costs.slip_mult * costs.slippage_ticks * costs.tick}[scenario]
    return f + sgn * adverse, adverse


def _open(legs, d, z, ts, snap, store, row, f_fut, fut_half, p, costs, cfg, strategy):
    fills, exec_cost, fees = [], 0.0, 0.0
    for lg in legs:
        r = store.row(ts, lg.strike, lg.cp)
        if not can_fill(r, lg.qty):
            return None
        fills.append(make_fill(ts, lg.strike, lg.cp, lg.qty, r, p.scenario, costs))
    sleg = []
    for lg, f in zip(legs, fills):
        r = store.row(ts, lg.strike, lg.cp)
        st = _leg_state(r)
        exec_cost += f.slippage * abs(f.qty)
        fees += costs.fees(f.price, abs(f.qty), f.qty > 0, "opt")
        sleg.append(dict(strike=lg.strike, cp=lg.cp, qty=lg.qty, st=st, fill=f))
    pos = dict(legs=sleg, dir=d, z_entry=z, entry_ts=ts, F=row["F"], T=row["T"], Ffut=f_fut, atm=np.nan,
               opt_gross=0.0, hedge_pnl=0.0, hedge=0.0, hedge_cost=0.0, fees=fees, entry_exec_cost=exec_cost,
               mins_since_hedge=1e9, last_ts=ts, steps=0, n_hedges=0, exit_reason=None, **{c: 0.0 for c in COMPONENTS})
    pos["atm"] = row.get("atm_iv", np.nan)
    g = lambda k: sum(l["qty"] * l["st"][k] for l in sleg)
    sd = np.sqrt(row["T"]) * max(pos["atm"] if np.isfinite(pos["atm"]) else 0.15, 1e-3)
    pos["entry_greeks"] = dict(delta=g("delta"), gross_vega=sum(abs(l["qty"] * l["st"]["vega"]) for l in sleg), gamma=g("gamma"), vega=g("vega"), theta=g("theta"),
                               skew_exp=sum(l["qty"] * l["st"]["vega"] * np.log(l["strike"] / row["F"]) / sd for l in sleg),
                               curv_exp=sum(l["qty"] * l["st"]["vega"] * (np.log(l["strike"] / row["F"]) / sd) ** 2
                                            for l in sleg))
    pos["entry_fills"] = fills
    return pos


def _close(pos, ts, snap, store, row, f_fut, fut_half, p, costs, lot, reason):
    exec_cost, fees, exit_fills = pos["entry_exec_cost"], pos["fees"], []
    for leg in pos["legs"]:
        r = store.row(ts, leg["strike"], leg["cp"])
        st = _leg_state(r, leg["st"])       # falls back to last valid quote if none now
        q = -leg["qty"]
        row_like = {"bid": st["bid"], "ask": st["ask"], "clean": True, "volume": np.nan}
        f = make_fill(ts, leg["strike"], leg["cp"], q, row_like, p.scenario, costs)
        # last mark to the quote actually used (mid-to-mid accounting stays consistent)
        if not st["stale"]:
            pass
        exec_cost += f.slippage * abs(q)
        fees += costs.fees(f.price, abs(q), q > 0, "opt")
        exit_fills.append(f)
    if pos["hedge"] != 0:
        dq = -pos["hedge"]
        px, slip = _fut_px(f_fut, fut_half, dq, p.scenario, costs)
        pos["hedge_cost"] += slip * abs(dq)
        fees += costs.fees(px, abs(dq), dq > 0, "fut")
    total_exec = exec_cost + pos["hedge_cost"]
    gross = pos["opt_gross"] + pos["hedge_pnl"]
    net = gross - total_exec - fees
    out = dict(entry_ts=pos["entry_ts"], exit_ts=ts, day=ts.normalize(), dir=pos["dir"], z_entry=pos["z_entry"],
               exit_reason=reason, hold_min=(ts - pos["entry_ts"]).total_seconds() / 60,
               n_legs=len(pos["legs"]), n_hedges=pos["n_hedges"],
               gross_pnl=gross, option_gross_pnl=pos["opt_gross"], hedge_pnl=pos["hedge_pnl"],
               spread_slippage_cost=exec_cost, hedge_exec_cost=pos["hedge_cost"], fees=fees, cost_pnl=-(total_exec + fees),
               net_pnl=net, entry_mod=pos["entry_ts"].hour * 60 + pos["entry_ts"].minute,
               premium_traded=sum(abs(f.qty * f.price) for f in pos["entry_fills"] + exit_fills),
               legs=";".join(f"{l['cp']:+d}@{l['strike']:.0f}x{l['qty']:.0f}" for l in pos["legs"]),
               **{c: pos[c] for c in COMPONENTS}, **{f"entry_{k}": v for k, v in pos["entry_greeks"].items()})
    out["fills"] = [(f.ts, f.strike, f.cp, f.qty, f.signal_mid, f.bid, f.ask, f.price, f.slippage)
                    for f in pos["entry_fills"] + exit_fills]
    return out
