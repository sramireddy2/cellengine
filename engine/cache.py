"""Serialize the cacheable half of the pipeline to bytes (for Redis) and back.

Two entries per (dataset, PreprocessParams):

  graph:<key>    PCA + kNN + UMAP + barcodes + HVG mask     ~2 MB at 3k cells
  expr:<key>     log-normalized expression (CSR, all genes)  ~20 MB at 3k cells

Split on purpose: recoloring the scatter after a resolution change needs only
`graph`. Marker ranking needs `expr`. Nobody should pay to deserialize 20 MB to
recolor 3k dots.

Known boundary: past ~50k cells the `expr` blob crosses ~300 MB and belongs on
disk with only the path cached. We do not build that here; we name it.
"""
from __future__ import annotations

import io

import numpy as np
import scipy.sparse as sp

from .params import PreprocessParams
from .pipeline import PreprocessResult

FORMAT_VERSION = 1


def _csr_to_arrays(m: sp.csr_matrix, prefix: str) -> dict:
    m = sp.csr_matrix(m)
    return {
        f"{prefix}_data": m.data,
        f"{prefix}_indices": m.indices,
        f"{prefix}_indptr": m.indptr,
        f"{prefix}_shape": np.asarray(m.shape),
    }


def _csr_from_arrays(z, prefix: str) -> sp.csr_matrix:
    return sp.csr_matrix(
        (z[f"{prefix}_data"], z[f"{prefix}_indices"], z[f"{prefix}_indptr"]),
        shape=tuple(z[f"{prefix}_shape"]),
    )


def _npz_bytes(**arrays) -> bytes:
    buf = io.BytesIO()
    np.savez(buf, **arrays)   # uncompressed: float32 embeddings barely compress, speed matters more
    return buf.getvalue()


def serialize_graph(r: PreprocessResult) -> bytes:
    return _npz_bytes(
        version=np.int32(FORMAT_VERSION),
        cell_barcodes=r.cell_barcodes,
        gene_names=r.gene_names,
        pca=r.pca,
        umap=r.umap,
        hvg_mask=r.hvg_mask,
        timings_keys=np.array(list(r.timings.keys())),
        timings_vals=np.array(list(r.timings.values()), dtype=np.float64),
        **_csr_to_arrays(r.connectivities, "conn"),
        **_csr_to_arrays(r.distances, "dist"),
    )


def serialize_expr(r: PreprocessResult) -> bytes:
    return _npz_bytes(version=np.int32(FORMAT_VERSION), **_csr_to_arrays(r.lognorm, "expr"))


def deserialize(graph_blob: bytes, expr_blob: bytes | None, params: PreprocessParams) -> PreprocessResult:
    """Rebuild a PreprocessResult. expr_blob may be None if only the graph is needed."""
    g = np.load(io.BytesIO(graph_blob), allow_pickle=False)
    if int(g["version"]) != FORMAT_VERSION:
        raise ValueError(f"cache format {int(g['version'])} != {FORMAT_VERSION}")
    if expr_blob is not None:
        e = np.load(io.BytesIO(expr_blob), allow_pickle=False)
        lognorm = _csr_from_arrays(e, "expr")
    else:
        n, m = len(g["cell_barcodes"]), len(g["gene_names"])
        lognorm = sp.csr_matrix((n, m), dtype=np.float32)
    return PreprocessResult(
        cell_barcodes=g["cell_barcodes"],
        gene_names=g["gene_names"],
        pca=g["pca"],
        connectivities=_csr_from_arrays(g, "conn"),
        distances=_csr_from_arrays(g, "dist"),
        umap=g["umap"],
        lognorm=lognorm,
        hvg_mask=g["hvg_mask"],
        params=params,
        timings=dict(zip(g["timings_keys"].tolist(), g["timings_vals"].tolist())),
    )
