# Nifty50_Vol_Smile_Strategy

Research framework for **NIFTY 50 0DTE options**: build an intraday volatility smile, decompose it into
level / skew / curvature, test whether deviations from a *fair* smile are statistically predictable, and decide
(with realistic execution, hedging, costs, walk-forward and robustness tests) whether any structure is tradeable.

The framework is built to **report "no edge" when the data says so**. It never starts from a strategy and tunes
it to be profitable: signals come from residual statistics, strategies are the structures that isolate each signal,
and every choice (fair model, threshold, hedge rule) is frozen on train + validation days before the locked test
block is reported.

## Quick start

```bash
pip install -r requirements.txt
python -m pytest nifty_0dte_smile/tests -q                       # unit tests (Black-76, IV solver, smile, arbitrage, stats, engine)
python run_research.py --synthetic 40 --out reports_synth        # pipeline validation on synthetic data (NOT evidence)
python run_research.py --data-dir data/raw --out reports         # real data
```

### Real data format (`data/raw/`)
`options.csv|parquet` - `timestamp, expiry, strike, cp (CE/PE), bid, ask, ltp, volume, oi, oi_change`
`underlying.csv|parquet` - `timestamp, spot, fut, fut_bid, fut_ask, vix`
Common vendor column names are aliased (`nifty`, `open_interest`, `option_type`, ...). Prefer tick/1-minute
snapshots; 5-minute otherwise. Nothing is interpolated between snapshots. `volume` should be cumulative per day.
Only contracts expiring on the snapshot's own date (0DTE) are used; other expiries are ignored.

Outputs go to `reports/`: `RESEARCH_REPORT.md` (answers Q1-Q10 + A-E grades), `tables/*.csv`, `plots/*.png`.

## Pipeline

```
raw chain -> quote validation (flags, never silent drops) -> forward (futures / put-call parity / blend)
 -> Black-76 IV (bid/mid/ask) + Greeks -> 0DTE smile (poly | regularised spline | SVI) + static-arbitrage checks
 -> ATM / skew / RR / butterfly / curvature factors + PCA -> fair smile (hist | neighbour | ridge factor | GBM)
 -> residual + expanding-z signals -> mean-reversion / predictive / IC tests with BH-FDR
 -> strategies (straddle, risk reversal, butterfly 10/25/35d, vertical, cross-strike RV), vega-neutral, futures-delta-hedged
 -> event-driven backtest (next-snapshot fills, liquidity gate, 4 cost scenarios, Indian statutory costs)
 -> Greek P&L attribution -> chronological train / validation / locked test + walk-forward folds
 -> robustness (threshold, strike, time, hedge, 2x cost, slippage, regimes) -> edge vs model uncertainty -> grade A-E
```

| package | role |
|---|---|
| `data/` | `loader`, `validation` (quote flags), `cleaner` (raw vs cleaned, IV sanity), `synthetic` (pipeline test data) |
| `options/` | `timeutil` (intraday T conventions, never T=0), `pricing`/`greeks` (Black-76), `iv` (robust solver), `forward` |
| `smile/` | `moneyness` (strike / log-moneyness / delta), `polynomial`, `spline`, `svi` (fit on total variance), `arbitrage`, `delta_surface` |
| `factors/` | ATM, skew, curvature, dynamics (z-score/percentile), PCA (full-sample and causal expanding) |
| `signals/` | `residual` (4 fair-smile models), `mean_reversion` (AR(1), half-life, DF, clustered regression, IC + CI), `relative_value` |
| `strategies/` | one file per structure, each documents what it is actually betting on |
| `hedging/` | interval / threshold / gamma-aware (Whalley-Wilmott) futures delta hedging |
| `backtest/` | `engine`, `execution`, `costs`, `pnl` |
| `statistics/` | BH-FDR / Holm, permutation IC test, bootstrap, robustness & edge-vs-uncertainty |
| `report.py`, `research.py` | orchestration, walk-forward, grading; `visualization/` plots + report writer |

## Design decisions worth knowing

- **Time:** `T = remaining trading minutes / annual trading minutes` (248 x 375 by default), floored at one minute.
  `--time-convention` switches to 252 or calendar; the report shows the (exact) IV-level sensitivity.
- **Smile points:** only clean, OTM, solver-OK, tight-spread options with |delta| >= 0.02; bid/mid/ask IV kept for
  every option. Model choice is by hold-out and wing-extrapolation error plus arbitrage-violation rate, not by assumption.
- **No look-ahead:** fair models use earlier days only (historical, ridge, GBM refit daily); z-score scale is an
  expanding past-only std; entries are decided at snapshot *t* and filled at *t+1* quotes.
- **Execution:** fills need a two-sided clean quote and volume >= 20x size. Scenarios: mid, quarter-spread, full
  bid/ask, bid/ask + slippage. Gross (mid, zero fees) and net are reported separately.
- **Attribution:** delta, gamma, theta, vega-level (ATM IV), vol-surface (leg IV vs ATM IV), hedge, costs, unexplained.
- **Costs:** `backtest/costs.py` rates are configurable defaults (brokerage, STT, NSE charges, SEBI, GST, stamp duty).
  Statutory rates change - verify them before drawing real-money conclusions.
- **Multiple testing:** Benjamini-Hochberg across every (fair model, factor, horizon) test; permutation and bootstrap helpers included.

## Known limitations
- No quote sizes: liquidity is approximated by volume. Queue position and market impact are not modelled.
- Calendar-arbitrage checks are implemented and unit-tested but need multi-expiry data to run in the pipeline.
- One week of real data cannot support strong statistical claims; the report says so through wide intervals rather than hiding it.
- `--synthetic` data exists only to validate the machinery (its latent factors mean-revert by construction).
