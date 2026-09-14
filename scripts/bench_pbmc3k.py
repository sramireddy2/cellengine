"""End-to-end timing on PBMC3k: proves the cache boundary claim with numbers."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import cache
from engine.io import load_counts
from engine.markers import rank_genes
from engine.params import ClusterParams, MarkerParams, PreprocessParams
from engine.pipeline import cluster, preprocess

path = Path(__file__).resolve().parent.parent / "data" / "pbmc3k_filtered_gene_bc_matrices.tar.gz"

t0 = time.perf_counter()
adata = load_counts(path)
X = adata.X
print(f"load        {time.perf_counter()-t0:6.2f}s  {adata.n_obs} cells x {adata.n_vars} genes, "
      f"nnz={X.nnz:,} ({X.nnz/(adata.n_obs*adata.n_vars):.1%} dense), "
      f"CSR={(X.data.nbytes+X.indices.nbytes)/1e6:.0f}MB vs dense {adata.n_obs*adata.n_vars*4/1e6:.0f}MB")

t0 = time.perf_counter()
pre = preprocess(adata, PreprocessParams())
print(f"preprocess  {time.perf_counter()-t0:6.2f}s  -> {pre.n_cells} cells x {pre.n_genes} genes; "
      + ", ".join(f"{k}={v:.1f}s" for k, v in pre.timings.items()))

g, e = cache.serialize_graph(pre), cache.serialize_expr(pre)
print(f"cache blobs          graph={len(g)/1e6:.2f}MB  expr={len(e)/1e6:.2f}MB")

for res in (0.5, 1.0, 2.0):
    t0 = time.perf_counter()
    labels = cluster(pre, ClusterParams(resolution=res))
    t1 = time.perf_counter()
    df = rank_genes(pre.lognorm, pre.gene_names, labels, MarkerParams())
    t2 = time.perf_counter()
    print(f"resolution={res:<4} leiden {t1-t0:5.2f}s -> {labels.max()+1:2d} clusters | markers {t2-t1:5.2f}s")

print()
print("Top 5 markers per cluster at resolution=1.0:")
labels = cluster(pre, ClusterParams(resolution=1.0))
df = rank_genes(pre.lognorm, pre.gene_names, labels, MarkerParams(n_top=5))
for c, grp in df.groupby("cluster"):
    print(f"  {c:2d} (n={int((labels==c).sum()):4d}): " + ", ".join(grp.gene))
