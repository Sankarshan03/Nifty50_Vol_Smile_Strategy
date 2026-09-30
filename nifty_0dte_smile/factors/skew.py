"""Skew / risk-reversal factors (IV units)."""


def skew_factors(features, model):
    g = lambda n: features[f"{model}_{n}"]
    atm = g("atm_iv")
    return {"put_skew25": g("p25") - atm, "call_skew25": g("c25") - atm, "rr25": g("c25") - g("p25"),
            "wing_spread": g("p25") - g("c25"), "put_skew10": g("p10") - atm, "call_skew10": g("c10") - atm,
            "rr10": g("c10") - g("p10"), "slope": g("slope")}
