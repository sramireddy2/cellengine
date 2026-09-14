import numpy as np
import pytest
import anndata as ad
import scipy.sparse as sp


@pytest.fixture(scope="session")
def toy_adata():
    """Synthetic 600-cell x 400-gene dataset with 3 planted cell types.

    Each type over-expresses its own block of 30 genes. Small enough that the
    whole pipeline runs in a couple of seconds, structured enough that Leiden
    should recover the three groups and Wilcoxon should find the planted markers.
    """
    rng = np.random.default_rng(0)
    n_per, n_types, n_genes = 200, 3, 400
    rows = []
    for t in range(n_types):
        base = rng.poisson(0.3, size=(n_per, n_genes))
        block = slice(t * 30, (t + 1) * 30)
        base[:, block] += rng.poisson(5.0, size=(n_per, 30))
        rows.append(base)
    X = np.vstack(rows).astype(np.float32)
    adata = ad.AnnData(sp.csr_matrix(X))
    adata.obs_names = [f"cell{i}" for i in range(adata.n_obs)]
    adata.var_names = [f"G{j}" for j in range(n_genes)]
    adata.obs["truth"] = np.repeat(np.arange(n_types), n_per)
    return adata
