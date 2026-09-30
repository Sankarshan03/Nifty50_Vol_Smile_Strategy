"""ATM volatility factor."""


def atm_factors(features, model):
    return {"atm_iv": features[f"{model}_atm_iv"],
            "atm_var": features[f"{model}_atm_iv"] ** 2 * features["T"]}   # total variance w = sigma^2 T
