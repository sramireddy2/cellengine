"""Load a count matrix from the two formats researchers actually have.

Both loaders return an AnnData whose .X is a scipy CSR matrix. Keeping X sparse
is the single most important memory decision in this project: PBMC3k is
2,700 cells x 32,738 genes. Dense float32 would be ~350 MB; as CSR with ~2.3M
non-zeros it is ~28 MB. We stay sparse until PCA, which is the first step that
genuinely needs a dense (cells x HVG) matrix -- and by then HVG selection has
cut the columns from 32k to 2k.
"""
from __future__ import annotations

import os
import tarfile
import tempfile
from pathlib import Path

import anndata as ad
import scanpy as sc
import scipy.sparse as sp


class UnsupportedFormat(ValueError):
    pass


def load_counts(path: str | os.PathLike) -> ad.AnnData:
    """Dispatch on extension: .h5ad, a 10x directory, or a .tar.gz of one."""
    p = Path(path)
    if p.suffix == ".h5ad":
        adata = ad.read_h5ad(p)
    elif p.is_dir():
        adata = _read_10x_dir(p)
    elif p.name.endswith((".tar.gz", ".tgz")):
        adata = _read_10x_tarball(p)
    else:
        raise UnsupportedFormat(f"Cannot load {p.name}: expected .h5ad, a 10x directory, or .tar.gz")

    if not sp.issparse(adata.X):
        adata.X = sp.csr_matrix(adata.X)
    elif not sp.isspmatrix_csr(adata.X):
        adata.X = adata.X.tocsr()
    adata.var_names_make_unique()
    adata.obs_names_make_unique()
    return adata


def _read_10x_dir(p: Path) -> ad.AnnData:
    # 10x v2 ships genes.tsv, v3 ships features.tsv.gz; scanpy handles both.
    return sc.read_10x_mtx(p, var_names="gene_symbols", cache=False)


def _read_10x_tarball(p: Path) -> ad.AnnData:
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(p) as tf:
            tf.extractall(tmp, filter="data")
        # Find the directory that actually holds matrix.mtx(.gz).
        for root, _dirs, files in os.walk(tmp):
            if any(f.startswith("matrix.mtx") for f in files):
                return _read_10x_dir(Path(root))
    raise UnsupportedFormat(f"{p.name} contains no matrix.mtx")
