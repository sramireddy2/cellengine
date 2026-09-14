"""The pipeline, split at the cache boundary.

    preprocess(adata, PreprocessParams) -> PreprocessResult     expensive, cacheable
    cluster(PreprocessResult, ClusterParams) -> labels           cheap, always rerun

PreprocessResult is deliberately plain numpy/scipy so it can be pickled into
Redis without dragging AnnData along. We rebuild a minimal AnnData only where a
scanpy function insists on one.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import anndata as ad
import numpy as np
import scanpy as sc
import scipy.sparse as sp

from .params import ClusterParams, PreprocessParams

sc.settings.verbosity = 0


@dataclass
class PreprocessResult:
    cell_barcodes: np.ndarray          # (n_cells,) str, after QC
    gene_names: np.ndarray             # (n_genes,) str, after gene filter
    pca: np.ndarray                    # (n_cells, n_pcs) float32
    connectivities: sp.csr_matrix      # (n_cells, n_cells) kNN weights, symmetric
    distances: sp.csr_matrix           # (n_cells, n_cells) kNN distances
    umap: np.ndarray                   # (n_cells, 2) float32
    lognorm: sp.csr_matrix             # (n_cells, n_genes) log1p(CP10k), all genes -- for markers
    hvg_mask: np.ndarray               # (n_genes,) bool
    params: PreprocessParams
    timings: dict = field(default_factory=dict)

    @property
    def n_cells(self) -> int:
        return self.pca.shape[0]

    @property
    def n_genes(self) -> int:
        return self.lognorm.shape[1]


def preprocess(adata: ad.AnnData, p: PreprocessParams) -> PreprocessResult:
    """QC -> normalize -> HVG -> PCA -> kNN -> UMAP.

    Memory profile on PBMC3k: adata.X stays CSR the whole way through. The only
    dense allocation is the (n_cells x n_top_genes) HVG slice handed to PCA:
    2,638 x 2,000 x 4 bytes = ~21 MB.
    """
    t: dict[str, float] = {}
    adata = adata.copy()  # never mutate the caller's object

    # --- QC ------------------------------------------------------------------
    t0 = time.perf_counter()
    sc.pp.filter_cells(adata, min_genes=p.min_genes_per_cell)
    sc.pp.filter_genes(adata, min_cells=p.min_cells_per_gene)
    # Mitochondrial fraction: high => cell membrane broke, cytoplasmic mRNA leaked out.
    adata.var["mt"] = adata.var_names.str.upper().str.startswith("MT-")
    sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], percent_top=None, log1p=False, inplace=True)
    adata = adata[adata.obs["pct_counts_mt"] < p.max_pct_mito].copy()
    t["qc"] = time.perf_counter() - t0

    # --- Normalize ------------------------------------------------------------
    t0 = time.perf_counter()
    sc.pp.normalize_total(adata, target_sum=p.target_sum)   # per-cell depth correction
    sc.pp.log1p(adata)                                       # variance stabilization
    lognorm = sp.csr_matrix(adata.X, dtype=np.float32)       # keep for marker genes
    t["normalize"] = time.perf_counter() - t0

    # --- HVG ------------------------------------------------------------------
    t0 = time.perf_counter()
    sc.pp.highly_variable_genes(adata, n_top_genes=p.n_top_genes, flavor="seurat")
    hvg_mask = adata.var["highly_variable"].to_numpy()
    t["hvg"] = time.perf_counter() - t0

    # --- PCA ------------------------------------------------------------------
    t0 = time.perf_counter()
    sub = adata[:, hvg_mask].copy()
    sc.pp.scale(sub, max_value=10)  # densifies: this is the intended dense allocation
    n_pcs = min(p.n_pcs, sub.n_obs - 1, sub.n_vars - 1)
    sc.tl.pca(sub, n_comps=n_pcs, random_state=p.random_state)
    pca = np.ascontiguousarray(sub.obsm["X_pca"], dtype=np.float32)
    t["pca"] = time.perf_counter() - t0

    # --- kNN graph ------------------------------------------------------------
    t0 = time.perf_counter()
    sc.pp.neighbors(sub, n_neighbors=p.n_neighbors, n_pcs=n_pcs, random_state=p.random_state)
    connectivities = sp.csr_matrix(sub.obsp["connectivities"], dtype=np.float32)
    distances = sp.csr_matrix(sub.obsp["distances"], dtype=np.float32)
    t["knn"] = time.perf_counter() - t0

    # --- UMAP (depends on the graph only, so it is cached with it) ------------
    t0 = time.perf_counter()
    sc.tl.umap(sub, random_state=p.random_state)
    umap = np.ascontiguousarray(sub.obsm["X_umap"], dtype=np.float32)
    t["umap"] = time.perf_counter() - t0

    return PreprocessResult(
        cell_barcodes=adata.obs_names.to_numpy().astype(str),
        gene_names=adata.var_names.to_numpy().astype(str),
        pca=pca,
        connectivities=connectivities,
        distances=distances,
        umap=umap,
        lognorm=lognorm,
        hvg_mask=hvg_mask,
        params=p,
        timings=t,
    )


def cluster(pre: PreprocessResult, c: ClusterParams) -> np.ndarray:
    """Leiden on the cached graph. Returns int32 labels, 0..k-1, sized by cluster (0 = largest)."""
    # Minimal AnnData: scanpy's leiden wants .obsp["connectivities"] + .uns["neighbors"].
    n = pre.n_cells
    tmp = ad.AnnData(obs={"i": np.arange(n)})
    tmp.obsp["connectivities"] = pre.connectivities
    tmp.obsp["distances"] = pre.distances
    tmp.uns["neighbors"] = {
        "connectivities_key": "connectivities",
        "distances_key": "distances",
        "params": {"method": "umap", "n_neighbors": pre.params.n_neighbors},
    }
    sc.tl.leiden(
        tmp,
        resolution=c.resolution,
        random_state=c.random_state,
        flavor="igraph",
        n_iterations=2,
        directed=False,
    )
    raw = tmp.obs["leiden"].astype(int).to_numpy()
    # Relabel so cluster 0 is the biggest: stable, human-friendly ordering.
    order = np.argsort(-np.bincount(raw))
    remap = np.empty_like(order)
    remap[order] = np.arange(len(order))
    return remap[raw].astype(np.int32)
