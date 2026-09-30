"""Curvature / butterfly factors."""


def curvature_factors(features, model):
    g = lambda n: features[f"{model}_{n}"]
    atm = g("atm_iv")
    return {"bf25": 0.5 * (g("p25") + g("c25")) - atm, "bf10": 0.5 * (g("p10") + g("c10")) - atm,
            "bf35": 0.5 * (g("p35") + g("c35")) - atm, "curv": g("curv")}
