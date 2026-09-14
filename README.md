# cellengine

Interactive single-cell clustering: upload a count matrix, cluster it, tweak the
resolution, see which genes define each cluster.

## The one design decision that matters

    QC -> normalize -> HVG -> PCA -> kNN graph -> UMAP      cached   (~30-40 s)
                                             -> Leiden      rerun    (~1 s)

The expensive steps (PCA, neighbor graph, UMAP) depend only on the dataset and
the preprocessing parameters. Leiden resolution, the knob researchers turn
most, sits downstream of all of them. So we cache the graph keyed by
sha256(dataset_id + preprocessing_params) and a resolution change reruns only
Leiden, then recolors the same UMAP.

## Layout

    engine/     framework-free science core (scanpy + hand-written marker stats)
    server/     Django + DRF API, Postgres models          (phase 2)
    worker/     RQ worker that runs engine jobs             (phase 2)
    frontend/   UMAP scatter + resolution slider            (phase 3)
    deploy/     Docker + Kubernetes manifests               (phase 4)

## Dev

    python -m venv .venv && .venv/Scripts/activate
    pip install -r requirements.txt
    python scripts/fetch_pbmc3k.py
    pytest
    python scripts/bench_pbmc3k.py
