"""Central configuration. Every tunable lives here so it can be frozen before the locked test."""
from dataclasses import dataclass, field

from .data.validation import QuoteRules


@dataclass
class Config:
    r: float = 0.065                     # risk-free rate (RBI repo-ish; sensitivity is tiny intraday)
    time_convention: str = "trading248"  # trading248 | trading252 | calendar
    quote_rules: QuoteRules = field(default_factory=QuoteRules)
    min_fit_points: int = 8
    smile_models: tuple = ("poly", "spline", "svi")
    primary_model: str = "poly"          # overwritten by model comparison (lowest CV error)
    min_delta_abs: float = 0.02
    max_fit_spread_pct: float = 0.35
    lot_size: int = 75                   # NIFTY lot; verify against current NSE circular
    capital: float = 1_000_000.0         # notional capital used only to annualise returns
    trading_days: int = 248
    entry_start_min: int = 9 * 60 + 30   # no entries before (opening auction noise)
    entry_end_min: int = 14 * 60 + 30
    flat_by_min: int = 15 * 60 + 10      # forced exit: no expiry settlement risk modelled
    # walk-forward day fractions: train / validation / locked test
    split_fracs: tuple = (0.4, 0.3, 0.3)
    seed: int = 7
