import logging
import os

from django.apps import AppConfig

log = logging.getLogger(__name__)


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"

    def ready(self):
        # RQ forks one child ("work horse") per job so a job's memory dies with
        # it. The price: anything imported lazily inside the job is re-imported
        # in every child. Importing the science stack here, in the worker
        # PARENT, means each fork inherits it copy-on-write (measured: ~2 s of
        # scanpy import per job otherwise). Web processes never set this.
        if os.environ.get("CELLENGINE_ROLE") != "worker":
            return
        import time
        t = time.perf_counter()
        import engine.io  # noqa: F401
        import engine.markers  # noqa: F401
        import engine.pipeline  # noqa: F401
        log.info("worker: science stack imported in %.1fs", time.perf_counter() - t)

        # Optional: pay the numba JIT cost once at boot on a toy matrix, so the
        # first real upload does not eat the ~30-50 s warmup. Forked children
        # inherit the compiled code.
        if os.environ.get("CELLENGINE_WARMUP", "1") == "1":
            t = time.perf_counter()
            _jit_warmup()
            log.info("worker: JIT warmup done in %.1fs", time.perf_counter() - t)


def _jit_warmup():
    import anndata as ad
    import numpy as np
    import scipy.sparse as sp

    from engine.markers import rank_genes
    from engine.params import ClusterParams, MarkerParams, PreprocessParams
    from engine.pipeline import cluster, preprocess

    rng = np.random.default_rng(0)
    X = sp.csr_matrix(rng.poisson(0.3, size=(300, 400)).astype(np.float32))
    adata = ad.AnnData(X)
    adata.var_names = [f"G{j}" for j in range(400)]
    p = PreprocessParams(min_genes_per_cell=1, min_cells_per_gene=1, max_pct_mito=100,
                         n_top_genes=100, n_pcs=10, n_neighbors=10)
    pre = preprocess(adata, p)
    labels = cluster(pre, ClusterParams())
    rank_genes(pre.lognorm, pre.gene_names, labels, MarkerParams(n_top=5))
