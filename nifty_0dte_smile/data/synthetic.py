"""Synthetic 0DTE NIFTY option-chain generator.

FOR PIPELINE VALIDATION ONLY. It produces data in the canonical loader schema with
latent mean-reverting smile factors, noisy/quantised bid-ask quotes, wide wings and
stale far-OTM quotes. `mispricing_vol` optionally plants a transient ATM mispricing
(a known positive control); with 0 the generator is a realistic *null* whose mean
reversion is mostly un-tradeable quote noise. Nothing here says anything about the
real NIFTY market.
"""
import numpy as np
import pandas as pd

from ..options.pricing import black76_price
from ..options.timeutil import CLOSE_MIN, CONVENTIONS, OPEN_MIN


def _ou(n, phi, sigma, rng, x0=0.0):
    x = np.empty(n)
    x[0] = x0
    e = rng.normal(0, sigma, n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + e[i]
    return x


def generate(n_days=30, freq_min=5, seed=0, start="2025-01-06", mispricing_vol=0.0,
             mispricing_halflife_min=10.0, r=0.065, spot0=24000.0, noise_iv=0.004, tick=0.05, vrp=1.0,
             convention="trading248"):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, periods=n_days)
    minutes = np.arange(OPEN_MIN, CLOSE_MIN + 1, freq_min)
    n = len(minutes)
    opts, und = [], []
    spot = spot0
    vix = 14.0
    for day in days:
        gap = rng.normal(0, 0.004)
        spot *= 1 + gap
        vix = float(np.clip(vix + rng.normal(0, 0.8) + 0.1 * (14 - vix), 9, 35))
        base_iv = vix / 100 * rng.uniform(0.85, 1.25)
        rem = (CLOSE_MIN - minutes).astype(float)
        T = np.maximum(rem, 1.0) / CONVENTIONS[convention]
        phi = 0.5 ** (freq_min / 30.0)
        atm_f = _ou(n, phi, 0.006, rng)
        skew_f = _ou(n, phi, 0.05, rng, 0)
        curv_f = _ou(n, phi, 0.03, rng, 0)
        plant = _ou(n, 0.5 ** (freq_min / mispricing_halflife_min), 1.0, rng) * mispricing_vol \
            if mispricing_vol > 0 else np.zeros(n)
        # spot: GBM, realised vol = vrp x implied (vrp=1 -> no variance risk premium, a clean null)
        step_sd = vrp * base_iv * (1 + 0.6 * np.exp(-rem / 40.0)) * np.sqrt(freq_min / CONVENTIONS[convention])
        rets = rng.normal(0, step_sd, n)
        rets[0] = 0.0
        path = spot * np.exp(np.cumsum(rets))
        for i, m in enumerate(minutes):
            ts = day + pd.Timedelta(minutes=int(m))
            S = float(path[i])
            basis = S * (np.exp(r * T[i]) - 1) + rng.normal(0, 0.5)
            F = S + basis
            atm = base_iv * (1 + 0.6 * np.exp(-rem[i] / 40.0)) + atm_f[i] + plant[i]
            atm = max(atm, 0.05)
            step = 50.0
            ks = np.arange(np.round(F / step) * step - 1500, np.round(F / step) * step + 1501, step)
            sd = atm * np.sqrt(T[i])
            x = np.log(ks / F) / sd
            iv = atm + (-0.08 + skew_f[i]) * atm * x * 0.5 + (0.05 + curv_f[i]) * atm * x ** 2 * 0.25
            iv = np.clip(iv, 0.03, 3.0)
            for cp in (1, -1):
                otm_wt = np.abs(x)
                iv_n = np.clip(iv + rng.normal(0, noise_iv, len(ks)), 0.03, 3.0)
                mid = black76_price(F, ks, T[i], iv_n, cp, r)
                half = np.maximum(tick, 0.004 * mid + 0.15 * (1 + 0.3 * otm_wt) * rng.uniform(0.5, 1.5, len(ks))) / 2
                half = np.round(half / tick) * tick
                bid = np.maximum(np.round((mid - half) / tick) * tick, 0.0)
                ask = np.round((mid + half) / tick) * tick
                ask = np.maximum(ask, bid + tick)
                vol = (rng.poisson(200 * np.exp(-0.5 * x ** 2) + 2, len(ks)) * 75 * (i + 1)).astype(float)
                opts.append(pd.DataFrame({
                    "timestamp": ts, "expiry": day + pd.Timedelta(minutes=CLOSE_MIN), "strike": ks,
                    "cp": cp, "bid": bid, "ask": ask, "ltp": mid, "volume": vol,
                    "oi": np.round(rng.uniform(1e4, 2e6, len(ks)) * np.exp(-0.3 * x ** 2)),
                    "oi_change": rng.normal(0, 5e4, len(ks)).round(),
                }))
            fs = 0.5 * 0.05 + 0.5 * rng.uniform(0, 0.5)
            und.append({"timestamp": ts, "spot": S, "fut": F, "fut_bid": F - fs, "fut_ask": F + fs,
                        "vix": vix * (1 + rng.normal(0, 0.003))})
        spot = float(path[-1])
    return pd.concat(opts, ignore_index=True), pd.DataFrame(und)
