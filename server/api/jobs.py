"""Worker-side job functions. These run inside `manage.py rqworker`, never in a
web request. Each takes ids (strings), not model instances, because RQ pickles
arguments into Redis.

run_pipeline stages and what is durable after each:

    preprocessing  cache lookup or full preprocess          nothing written yet
    clustering     Leiden                                    nothing written yet
    markers        CellCluster rows COMMITTED (one txn)      scatter can render
    done           MarkerGene rows COMMITTED (one txn)       cluster panel can render

If the worker dies mid-markers the run stays in "markers": labels are intact,
the marker table is empty, and the status tells the client exactly that.
"""
import logging
import time
import traceback

from django.db import transaction
from django.utils import timezone

from engine.params import params_from_dict      # pure dataclasses, no scanpy

from .models import CellCluster, Dataset, MarkerGene, Run

# scanpy/numba are imported lazily inside the job bodies. The web process imports
# this module (to enqueue by function reference) and must stay lean: importing
# scanpy costs ~2 s and ~250 MB RSS per gunicorn worker, for code it never runs.

log = logging.getLogger(__name__)
BATCH = 5000


def validate_dataset(dataset_id: str) -> None:
    from engine.io import load_counts

    ds = Dataset.objects.get(id=dataset_id)
    try:
        adata = load_counts(ds.file.path)
        ds.n_cells, ds.n_genes = adata.n_obs, adata.n_vars
        ds.status = Dataset.Status.READY
        ds.error = ""
        del adata
    except Exception as e:                      # noqa: BLE001 - surface anything to the user
        ds.status = Dataset.Status.FAILED
        ds.error = f"{type(e).__name__}: {e}"
        log.warning("dataset %s failed validation: %s", dataset_id, ds.error)
    ds.save(update_fields=["n_cells", "n_genes", "status", "error"])


def _set_status(run: Run, status: str, **fields) -> None:
    for k, v in fields.items():
        setattr(run, k, v)
    run.status = status
    run.save(update_fields=["status", *fields.keys()])


def run_pipeline(run_id: str) -> None:
    from engine.io import load_counts
    from engine.markers import rank_genes
    from engine.pipeline import cluster, preprocess

    from . import cache

    run = Run.objects.select_related("dataset").get(id=run_id)
    if run.status == Run.Status.DONE:
        return                                   # idempotent on retry
    timings: dict[str, float] = {}
    _set_status(run, Run.Status.PREPROCESSING, started_at=timezone.now(), error="")
    try:
        pre_p, clu_p, mk_p = params_from_dict(run.params)
        key = pre_p.cache_key(str(run.dataset_id))
        run.preprocess_key = key

        # --- stage 1: the expensive, cacheable part -------------------------
        t0 = time.perf_counter()
        pre = cache.get_preprocessed(key, pre_p, need_expr=True)
        if pre is None:
            adata = load_counts(run.dataset.file.path)
            pre = preprocess(adata, pre_p)
            del adata
            sizes = cache.put_preprocessed(key, pre)
            timings["cache_write_mb"] = round((sizes["graph_bytes"] + sizes["expr_bytes"]) / 1e6, 2)
            timings.update({f"pre_{k}": round(v, 3) for k, v in pre.timings.items()})
            run.cache_hit = False
        else:
            run.cache_hit = True
        timings["preprocess"] = round(time.perf_counter() - t0, 3)

        # --- stage 2: Leiden ------------------------------------------------
        _set_status(run, Run.Status.CLUSTERING, preprocess_key=key, cache_hit=run.cache_hit)
        t0 = time.perf_counter()
        labels = cluster(pre, clu_p)
        timings["leiden"] = round(time.perf_counter() - t0, 3)

        # --- commit labels: all cells or none --------------------------------
        t0 = time.perf_counter()
        rows = [
            CellCluster(run=run, cell_barcode=str(b), cluster_id=int(c), umap_x=float(x), umap_y=float(y))
            for b, c, (x, y) in zip(pre.cell_barcodes, labels, pre.umap)
        ]
        with transaction.atomic():
            CellCluster.objects.filter(run=run).delete()        # retry-safe
            CellCluster.objects.bulk_create(rows, batch_size=BATCH)
            _set_status(run, Run.Status.MARKERS, n_clusters=int(labels.max()) + 1)
        timings["write_cells"] = round(time.perf_counter() - t0, 3)

        # --- stage 3: marker genes -------------------------------------------
        t0 = time.perf_counter()
        df = rank_genes(pre.lognorm, pre.gene_names, labels, mk_p)
        timings["markers"] = round(time.perf_counter() - t0, 3)
        mrows = [
            MarkerGene(run=run, cluster_id=int(r.cluster), rank=int(r.rank), gene=str(r.gene),
                       score=float(r.score), log2fc=float(r.log2fc), pct_in=float(r.pct_in),
                       pct_out=float(r.pct_out), pval=float(r.pval), padj=float(r.padj))
            for r in df.itertuples(index=False)
        ]
        with transaction.atomic():
            MarkerGene.objects.filter(run=run).delete()
            MarkerGene.objects.bulk_create(mrows, batch_size=BATCH)
            _set_status(run, Run.Status.DONE, timings=timings, finished_at=timezone.now())
        log.info("run %s done: %s", run_id, timings)

    except Exception as e:                      # noqa: BLE001
        log.error("run %s failed:\n%s", run_id, traceback.format_exc())
        _set_status(run, Run.Status.FAILED, error=f"{type(e).__name__}: {e}",
                    timings=timings, finished_at=timezone.now())
        raise                                    # let RQ record the failure too
