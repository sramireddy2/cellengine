import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.stats import mannwhitneyu

from engine.markers import benjamini_hochberg, rank_genes
from engine.params import MarkerParams


def test_bh_hand_checked_values():
    p = np.array([0.01, 0.04, 0.03, 0.20, 0.50])
    q = benjamini_hochberg(p)
    # sorted p: .01,.03,.04,.20,.50 -> p*m/i: .05,.075,.0667,.25,.5
    # monotone from the top down: .05,.0667,.0667,.25,.5
    expected = np.array([0.05, 0.0667, 0.0667, 0.25, 0.5])
    np.testing.assert_allclose(q, expected, atol=1e-3)
    assert np.all(q >= p)          # correction never makes a p-value smaller
    assert np.all(q <= 1.0)


def test_bh_monotone_random():
    rng = np.random.default_rng(1)
    p = rng.uniform(size=5000)
    q = benjamini_hochberg(p)
    order = np.argsort(p)
    assert np.all(np.diff(q[order]) >= -1e-12)


def test_wilcoxon_matches_scipy_per_gene():
    """Vectorized rank-sum must agree with scipy mannwhitneyu (asymptotic, tie-corrected)."""
    rng = np.random.default_rng(2)
    n, g = 300, 40
    X = rng.poisson(0.5, size=(n, g)).astype(np.float32)
    X[:100, :10] += rng.poisson(3, size=(100, 10))      # planted markers for cluster 0
    labels = np.repeat([0, 1, 2], 100)
    lognorm = sp.csr_matrix(np.log1p(X))
    genes = np.array([f"G{j}" for j in range(g)])

    df = rank_genes(lognorm, genes, labels, MarkerParams(n_top=g, alpha=1.0))
    c0 = df[df.cluster == 0].set_index("gene")

    dense = np.log1p(X.astype(np.float64))   # scipy in float64 so the comparison is exact
    for j in range(g):
        a = dense[labels == 0, j]
        b = dense[labels != 0, j]
        res = mannwhitneyu(a, b, alternative="two-sided", method="asymptotic", use_continuity=False)
        if f"G{j}" in c0.index:      # only up-regulated genes are reported
            np.testing.assert_allclose(c0.loc[f"G{j}", "pval"], res.pvalue, rtol=1e-6)

    assert set(c0.index[:10]) == {f"G{j}" for j in range(10)}
    assert (c0["padj"] >= c0["pval"]).all()


def test_chunking_is_invisible():
    rng = np.random.default_rng(3)
    X = rng.poisson(0.7, size=(150, 95)).astype(np.float32)
    labels = rng.integers(0, 3, size=150)
    lognorm = sp.csr_matrix(np.log1p(X))
    genes = np.array([f"G{j}" for j in range(95)])
    a = rank_genes(lognorm, genes, labels, MarkerParams(n_top=95, alpha=1.0, chunk_size=7))
    b = rank_genes(lognorm, genes, labels, MarkerParams(n_top=95, alpha=1.0, chunk_size=10_000))
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-9)
