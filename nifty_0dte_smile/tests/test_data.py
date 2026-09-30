import numpy as np
import pandas as pd

from nifty_0dte_smile.data.loader import _cp, zero_dte
from nifty_0dte_smile.data.validation import validate_quotes


def _row(ts, bid, ask, vol=100, strike=24000, cp=1):
    return dict(timestamp=pd.Timestamp(ts), expiry=pd.Timestamp("2025-01-06 15:30"), strike=strike, cp=cp, bid=bid,
                ask=ask, ltp=bid, volume=vol, oi=1, oi_change=0)


def test_flags():
    df = pd.DataFrame([_row("2025-01-06 10:00", 10, 11), _row("2025-01-06 10:00", 0, 5, strike=24100),
                       _row("2025-01-06 10:00", 12, 10, strike=24200), _row("2025-01-06 10:00", 1, 9, strike=24300),
                       _row("2025-01-06 10:00", 10, 11), _row("2025-01-06 10:00", 5, 6, vol=0, strike=24500)])
    v = validate_quotes(df)
    by = lambda k: v[v.strike == k]
    assert by(24100).f_nonpositive.all() and by(24200).f_crossed.all() and by(24300).f_wide.all()
    assert by(24500).f_zero_liq.all()
    d0 = by(24000)
    assert d0.f_duplicate.sum() == 1 and d0.clean.sum() == 1          # later duplicate kept, earlier flagged
    assert np.isclose(d0.mid.iloc[0], 10.5) and np.isclose(d0.spread_pct.iloc[0], 1 / 10.5)


def test_stale_quote_flag():
    rows = [_row(f"2025-01-06 10:{m:02d}", 10, 11, vol=100) for m in range(0, 15)]
    v = validate_quotes(pd.DataFrame(rows))
    assert v.f_stale.iloc[-1] and not v.f_stale.iloc[0]


def test_cp_parsing_and_zero_dte():
    assert list(_cp(pd.Series(["CE", "PE", "c", "p"]))) == [1, -1, 1, -1]
    df = pd.DataFrame([_row("2025-01-06 10:00", 1, 2)])
    df2 = df.copy()
    df2["expiry"] = pd.Timestamp("2025-01-09")
    assert len(zero_dte(pd.concat([df, df2]))) == 1
