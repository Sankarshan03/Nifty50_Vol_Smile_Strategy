import pandas as pd

from .atm import atm_factors
from .curvature import curvature_factors
from .skew import skew_factors


def factor_table(features, model):
    """All smile factors for one smile model, indexed like `features`."""
    d = {}
    for f in (atm_factors, skew_factors, curvature_factors):
        d.update(f(features, model))
    return pd.DataFrame(d, index=features.index)
