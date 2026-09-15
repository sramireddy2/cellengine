"""Parameter sets for the pipeline.

The split into PreprocessParams vs ClusterParams IS the cache boundary.

    QC filter -> normalize -> HVG -> PCA -> kNN graph -> UMAP   (PreprocessParams, ~40 s)
                                                     -> Leiden (ClusterParams,   ~1 s)

Everything on the left is a pure function of (dataset, PreprocessParams) and is
cached. Leiden is a pure function of (kNN graph, ClusterParams) and is cheap, so
we recompute it every time. UMAP lives on the cached side because it depends on
the neighbor graph, not on the cluster labels: changing resolution recolors the
same scatter, it does not move the points.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class PreprocessParams:
    # QC
    min_genes_per_cell: int = 200      # drop cells with fewer detected genes (empty droplets)
    min_cells_per_gene: int = 3        # drop genes seen in fewer cells (pure noise)
    max_pct_mito: float = 5.0          # drop cells with >X% mitochondrial reads (dying cells)
    # Normalization
    target_sum: float = 1e4            # counts-per-10k, then log1p
    # Feature selection
    n_top_genes: int = 2000            # highly variable genes kept for PCA
    # Dimensionality reduction
    n_pcs: int = 50
    # Neighbor graph
    n_neighbors: int = 15
    # Reproducibility
    random_state: int = 0

    def cache_key(self, dataset_id: str) -> str:
        """Stable key: same dataset + same params => same PCA/kNN/UMAP."""
        payload = json.dumps(
            {"dataset_id": dataset_id, "params": asdict(self)},
            sort_keys=True,
            separators=(",", ":"),
        )
        return "preproc:" + hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class ClusterParams:
    resolution: float = 1.0            # higher => more, smaller clusters
    random_state: int = 0


@dataclass(frozen=True)
class MarkerParams:
    n_top: int = 25                    # genes reported per cluster
    alpha: float = 0.05                # BH-adjusted p-value cutoff
    min_log2fc: float = 0.5            # effect-size floor: with 500+ cells per cluster, a 1.3x
                                       # shift in a housekeeping gene is "significant" but useless
    chunk_size: int = 4000             # genes per block; peak memory ~ non-zeros in one block


def params_from_dict(d: dict | None) -> tuple[PreprocessParams, ClusterParams, MarkerParams]:
    """Build the three param sets from one flat dict (what the API receives)."""
    d = dict(d or {})
    pre_fields = PreprocessParams.__dataclass_fields__
    clu_fields = ClusterParams.__dataclass_fields__
    mk_fields = MarkerParams.__dataclass_fields__
    unknown = set(d) - set(pre_fields) - set(clu_fields) - set(mk_fields)
    if unknown:
        raise ValueError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
    for k, v in d.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"parameter {k} must be a number")
    pre = PreprocessParams(**{k: v for k, v in d.items() if k in pre_fields})
    clu = ClusterParams(**{k: v for k, v in d.items() if k in clu_fields})
    mk = MarkerParams(**{k: v for k, v in d.items() if k in mk_fields})
    return pre, clu, mk
