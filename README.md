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
    server/     Django + DRF API, Postgres models, RQ job functions
    frontend/   UMAP scatter + resolution slider            (phase 3)
    deploy/     Docker + Kubernetes manifests               (phase 4)

## Request flow

    POST /api/datasets/   multipart upload -> file on disk -> worker validates shape
    POST /api/runs/       {dataset, params} -> 202 + run id -> worker runs pipeline
    GET  /api/runs/{id}/  poll: queued -> preprocessing -> clustering -> markers -> done
    GET  /api/runs/{id}/cells/     four parallel arrays: barcodes, x, y, cluster
    GET  /api/runs/{id}/markers/?cluster=3

Cluster labels commit in one transaction (status becomes `markers`, the scatter
can render); marker genes commit in a second one (status `done`). The web
process never opens a matrix; only the worker does.

## Dev

    python -m venv .venv && .venv/Scripts/activate
    pip install -r requirements.txt
    python scripts/fetch_pbmc3k.py
    pytest                              # sqlite + fake redis + sync jobs, no services needed
    python scripts/bench_pbmc3k.py

Full stack locally:

    docker compose up -d                # postgres + redis
    python server/manage.py migrate
    python server/manage.py seed_demo   # user demo / demo-password-1 + PBMC3k dataset
    python server/manage.py rqworker default      # terminal 1: long-lived worker
    python server/manage.py runserver             # terminal 2: web
