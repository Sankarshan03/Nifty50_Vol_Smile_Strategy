"""Intraday time-to-expiry conventions for 0DTE options (never T = 0)."""
import numpy as np
import pandas as pd

OPEN_MIN = 9 * 60 + 15
CLOSE_MIN = 15 * 60 + 30
SESSION_MINUTES = CLOSE_MIN - OPEN_MIN  # 375

CONVENTIONS = {
    # name: annual denominator in minutes
    "trading248": 248 * SESSION_MINUTES,
    "trading252": 252 * SESSION_MINUTES,
    "calendar": 365 * 24 * 60,
}


def minute_of_day(ts):
    ts = pd.DatetimeIndex(ts)
    return np.asarray(ts.hour * 60 + ts.minute)


def minutes_since_open(ts):
    return minute_of_day(ts) - OPEN_MIN


def trading_minutes_remaining(ts, floor=1.0):
    """Trading minutes until the 15:30 expiry on the same day (floored so T > 0)."""
    rem = CLOSE_MIN - minute_of_day(ts)
    return np.maximum(np.asarray(rem, dtype=float), floor)


def year_fraction(ts, convention="trading248", floor=1.0):
    """T in years. 'calendar' uses the same remaining minutes over a 365-day year."""
    return trading_minutes_remaining(ts, floor) / CONVENTIONS[convention]
