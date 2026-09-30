"""PCA of the cross-sectional smile (delta pillars): level / slope / curvature."""
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA

PILLARS = ["p10", "p15", "p25", "p35", "atm", "c35", "c25", "c15", "c10"]


def pillar_matrix(features, model):
    cols = [f"{model}_{p}" for p in PILLARS]
    m = features[cols].copy()
    m.columns = PILLARS
    ext = features[[f"{model}_{p}_ext" for p in PILLARS]].fillna(True).astype(bool).any(axis=1)
    return m[~ext].dropna()


def smile_pca(features, model, n=3):
    """Full-sample PCA (descriptive). Returns dict(explained, loadings, scores, labels)."""
    X = pillar_matrix(features, model)
    pca = PCA(n_components=min(n, X.shape[1])).fit(X.values - X.values.mean(0))
    scores = pd.DataFrame(pca.transform(X.values - X.values.mean(0)), index=X.index,
                          columns=[f"pc{i + 1}" for i in range(pca.n_components_)])
    load = pd.DataFrame(pca.components_.T, index=PILLARS, columns=scores.columns)
    labels = {}
    for c in load:
        v = load[c].values
        if np.all(np.sign(v) == np.sign(v[0])):
            labels[c] = "level"
        elif np.corrcoef(v, np.linspace(-1, 1, len(v)))[0, 1] ** 2 > 0.6:
            labels[c] = "skew/slope"
        else:
            labels[c] = "curvature"
    return {"explained": pd.Series(pca.explained_variance_ratio_, index=scores.columns),
            "loadings": load, "scores": scores, "labels": labels}


def causal_pc_scores(features, model, min_days=2, n=3):
    """Expanding-window PCA: day d is scored by a PCA fitted only on days < d (no look-ahead)."""
    X = pillar_matrix(features, model)
    days = X.index.normalize()
    out = []
    for d in sorted(days.unique())[min_days:]:
        tr, te = X[days < d], X[days == d]
        if len(tr) < 30:
            continue
        mu = tr.values.mean(0)
        p = PCA(n_components=n).fit(tr.values - mu)
        comp = p.components_.copy()
        ref = smile_ref_signs(comp)
        s = (te.values - mu) @ comp.T * ref
        out.append(pd.DataFrame(s, index=te.index, columns=[f"pc{i + 1}" for i in range(n)]))
    return pd.concat(out) if out else pd.DataFrame(columns=[f"pc{i + 1}" for i in range(n)])


def smile_ref_signs(comp):
    """Fix sign convention: pc1 positive on ATM-ish loads, pc2 positive = calls up vs puts (rr), pc3 = wings up."""
    idx = {p: i for i, p in enumerate(PILLARS)}
    sg = np.ones(comp.shape[0])
    sg[0] = 1 if comp[0].sum() >= 0 else -1
    if comp.shape[0] > 1:
        sg[1] = 1 if comp[1][idx["c10"]] - comp[1][idx["p10"]] >= 0 else -1
    if comp.shape[0] > 2:
        sg[2] = 1 if comp[2][idx["c10"]] + comp[2][idx["p10"]] - 2 * comp[2][idx["atm"]] >= 0 else -1
    return sg
