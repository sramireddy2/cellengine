"""Marker gene ranking: which genes distinguish each cluster from all other cells?

Implemented by hand (no scanpy.tl.rank_genes_groups) so the statistics are ours
to explain:

  1. Wilcoxon rank-sum (= Mann-Whitney U), one test per (cluster, gene).
     Non-parametric: single-cell expression is zero-inflated and skewed, so a
     t-test normality assumption is wrong. Ranks do not care about the shape.

  2. Benjamini-Hochberg FDR correction per cluster. With ~14k genes per cluster,
     an uncorrected p < 0.05 would hand you ~700 false positives per cluster.
     BH controls the *expected fraction* of false discoveries among the genes
     you call significant, which is the right guarantee for a ranked list.

The implementation never densifies the expression matrix. ~93% of entries are
zero, and every zero in a gene column is one tie group: all n0 zeros share the
average rank (n0 + 1) / 2 and sit below every non-zero. So we

  - sort only the non-zeros, once, with a single lexsort keyed by (gene, value);
  - assign average ranks to tie groups among the non-zeros;
  - add the zero group analytically per (cluster, gene);
  - accumulate rank sums, counts and means per (cluster, gene) with np.bincount.

Cost is O(nnz log nnz) time and O(nnz) memory instead of O(cells x genes).
Genes are still processed in column blocks so peak memory is bounded by the
non-zeros in one block, not the whole matrix.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.stats import norm

from .params import MarkerParams


def benjamini_hochberg(p: np.ndarray) -> np.ndarray:
    """BH-adjusted p-values (q-values). Monotone, clipped to [0, 1]."""
    p = np.asarray(p, dtype=np.float64)
    m = p.size
    if m == 0:
        return p
    order = np.argsort(p)
    ranked = p[order] * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]   # enforce monotonicity
    out = np.empty(m)
    out[order] = np.clip(q, 0, 1)
    return out


def _block_stats(X: sp.csc_matrix, labels: np.ndarray, k: int, n1: np.ndarray):
    """Wilcoxon inputs for one column block of a CSC matrix with strictly positive entries.

    Returns per-(cluster, gene) rank sums R1, tie terms per gene, non-zero counts
    per (cluster, gene), sum of expm1 per (cluster, gene) and per gene.
    """
    n, g = X.shape
    indptr = X.indptr
    nnz_per_col = np.diff(indptr)
    n0 = (n - nnz_per_col).astype(np.float64)               # zeros per gene
    data = X.data.astype(np.float64)
    col = np.repeat(np.arange(g), nnz_per_col)               # gene of each non-zero
    nnz = data.size

    if nnz == 0:
        z = np.zeros((k, g))
        return z, n0**3 - n0, z.copy(), z.copy(), np.zeros(g), nnz_per_col, n0

    # One sort: by gene, then by value. Column segments stay aligned with indptr.
    order = np.lexsort((data, col))
    sd, scol = data[order], col[order]
    srow = X.indices[order]

    # Tie groups among the non-zeros of each gene.
    new = np.empty(nnz, dtype=bool)
    new[0] = True
    new[1:] = (scol[1:] != scol[:-1]) | (sd[1:] != sd[:-1])
    gid = np.cumsum(new) - 1
    gsize = np.bincount(gid).astype(np.float64)
    pos = np.arange(nnz) - indptr[scol]                      # 0-based position within the gene
    gstart = pos[new]                                        # first position of each group
    # Average rank (1-based) of a non-zero: zeros below it + its group midpoint.
    rank_nz = n0[scol] + gstart[gid] + (gsize[gid] + 1) / 2.0

    # sum(t^3 - t) over tie groups: non-zero groups + the zero group.
    tie_nz = np.bincount(scol[new], weights=gsize**3 - gsize, minlength=g)
    ties = tie_nz + (n0**3 - n0)

    # Segment sums per (cluster, gene) via bincount on a flattened key.
    key = labels[srow] * g + scol
    r1_nz = np.bincount(key, weights=rank_nz, minlength=k * g).reshape(k, g)
    cnt_in = np.bincount(key, minlength=k * g).reshape(k, g).astype(np.float64)
    zeros_in = n1[:, None] - cnt_in
    r1 = r1_nz + zeros_in * ((n0 + 1) / 2.0)[None, :]        # zero group contributes analytically

    e = np.expm1(sd)                                         # back to linear scale for fold change
    sum_in = np.bincount(key, weights=e, minlength=k * g).reshape(k, g)
    tot = np.bincount(scol, weights=e, minlength=g)
    return r1, ties, cnt_in, sum_in, tot, nnz_per_col.astype(np.float64), n0


def rank_genes(
    lognorm: sp.spmatrix,
    gene_names: np.ndarray,
    labels: np.ndarray,
    mp: MarkerParams = MarkerParams(),
) -> pd.DataFrame:
    """One-vs-rest Wilcoxon for every cluster on log-normalized (non-negative) expression.

    Returns a long table of the top genes per cluster with columns:
    cluster, rank, gene, score (z), log2fc, pct_in, pct_out, pval, padj
    """
    X = sp.csc_matrix(lognorm)
    X.eliminate_zeros()
    X.sum_duplicates()
    if X.nnz and X.data.min() <= 0:
        raise ValueError("rank_genes expects non-negative log-normalized expression (no scaled data)")
    n, g = X.shape
    labels = np.asarray(labels)
    k = int(labels.max()) + 1
    n1 = np.bincount(labels, minlength=k).astype(np.float64)    # cells per cluster
    n2 = n - n1
    mean_u = n1 * n2 / 2.0
    base_var = n1 * n2 / 12.0

    z_all = np.empty((k, g)); p_all = np.empty((k, g)); lfc_all = np.empty((k, g))
    pin_all = np.empty((k, g)); pout_all = np.empty((k, g))

    for start in range(0, g, mp.chunk_size):
        stop = min(start + mp.chunk_size, g)
        r1, ties, cnt_in, sum_in, tot, nnz_col, _ = _block_stats(X[:, start:stop], labels, k, n1)

        u1 = r1 - n1[:, None] * (n1[:, None] + 1) / 2.0
        var_u = base_var[:, None] * ((n + 1) - ties[None, :] / (n * (n - 1)))
        var_u = np.where(var_u <= 0, np.nan, var_u)          # constant gene => undefined
        z = np.nan_to_num((u1 - mean_u[:, None]) / np.sqrt(var_u), nan=0.0)
        p = 2.0 * norm.sf(np.abs(z))

        mean_in = sum_in / n1[:, None]
        mean_out = (tot[None, :] - sum_in) / n2[:, None]
        lfc = np.log2(mean_in + 1e-9) - np.log2(mean_out + 1e-9)

        z_all[:, start:stop] = z
        p_all[:, start:stop] = p
        lfc_all[:, start:stop] = lfc
        pin_all[:, start:stop] = cnt_in / n1[:, None]
        pout_all[:, start:stop] = (nnz_col[None, :] - cnt_in) / n2[:, None]

    frames = []
    for c in range(k):
        padj = benjamini_hochberg(p_all[c])
        keep = np.where((z_all[c] > 0) & (padj < mp.alpha))[0]   # up in-cluster AND significant
        top = keep[np.argsort(-z_all[c, keep])][: mp.n_top]
        frames.append(pd.DataFrame({
            "cluster": c,
            "rank": np.arange(1, len(top) + 1),
            "gene": gene_names[top],
            "score": z_all[c, top],
            "log2fc": lfc_all[c, top],
            "pct_in": pin_all[c, top],
            "pct_out": pout_all[c, top],
            "pval": p_all[c, top],
            "padj": padj[top],
        }))
    return pd.concat(frames, ignore_index=True)
