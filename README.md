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

## Architecture in numbers

All measured on PBMC3k (2,700 cells x 32,738 genes) on a laptop; see
scripts/bench_pbmc3k.py.

| Step | Cold process | Steady state | Where it runs |
|---|---|---|---|
| Load 10x tarball | 1.3 s | 1.3 s | worker |
| QC -> normalize -> HVG -> PCA -> kNN -> UMAP | ~50 s | ~7 s | worker, cached in Redis |
| Leiden (resolution change) | 0.1 s | 0.1 s | worker, every run |
| Marker genes (Wilcoxon + BH, ~14k genes x k clusters) | 0.85 s | 0.85 s | worker, every run |
| Cache blobs written | graph 2.6 MB + expression 18 MB | | Redis, 24 h TTL |

The cold column is numba JIT compilation inside scanpy and umap-learn. It is
paid once per worker process, which is why the worker is a long-lived
Deployment (not a Job per upload) and why the JIT cache directory is a volume.
The worker also warms the JIT on a toy matrix at boot (api/apps.py), so the
first real run does not pay it either.

Measured through the containerized stack (docker compose, Linux), submit to
done including queue pickup and both Postgres commits:

| Run | Cache | Submit -> done |
|---|---|---|
| First run on a dataset | miss, JIT already warm | 6.2 s |
| Resolution change | hit | 1.0 s |

RQ forks one child per job so memory from one dataset dies with it. The cost
is that lazily imported modules are re-imported per child (~2 s of scanpy);
the worker parent imports the science stack at boot so forks inherit it.

Memory, same dataset:

| Representation | Size |
|---|---|
| Raw counts, dense float32 | 354 MB |
| Raw counts, CSR (2.6% non-zero) | 18 MB |
| HVG slice handed to PCA (2,643 x 2,000, dense) | 21 MB |
| Marker test working set | O(non-zeros), never cells x genes |

Three things found by profiling that are easy to get wrong:

- scipy rankdata returns float32 for float32 input, and a float64 @ float32
  matmul in numpy skips BLAS (150 ms vs 2 ms per chunk).
- After scanpy loads, OpenBLAS and numba both spin thread pools; a 9 ms
  matmul became 160 ms. Worker containers pin OPENBLAS_NUM_THREADS and
  NUMBA_NUM_THREADS to their CPU limit because BLAS cannot see cgroup limits.
- With 500+ cells per cluster, housekeeping genes reach p < 1e-40 on a 1.3x
  shift. Markers require padj < 0.05 AND log2 fold change >= 0.5.

## Layout

    engine/     framework-free science core (scanpy + hand-written marker stats)
    server/     Django + DRF API, Postgres models, RQ job functions
    frontend/   UMAP scatter (canvas) + resolution slider + marker table, no build step
    deploy/     Kubernetes manifests + deploy notes (see deploy/README.md)

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

No-Docker demo (sqlite + fake Redis + jobs run inline in the request):

    python scripts/dev_lite.py          # http://localhost:8000, user demo / demo-password-1

Full stack locally:

    docker compose up -d                # postgres + redis
    python server/manage.py migrate
    python server/manage.py seed_demo   # user demo / demo-password-1 + PBMC3k dataset
    python server/manage.py rqworker default      # terminal 1: long-lived worker
    python server/manage.py runserver             # terminal 2: web

## Frontend

Plain HTML + one script, served by Django. The scatter is a canvas, not SVG:
3k points is fine either way, 50k is not. Hover uses a screen-space grid so the
nearest-point lookup is O(1). The resolution slider submits a run on release;
each run is a row in the history table with its cache hit/miss and stage timings,
so the caching story is visible rather than claimed. Cluster identity never
relies on color alone: every cluster gets a centroid label on the plot and a
legend row with its size and top-3 marker genes.

## Deploy

One image for web and worker (Dockerfile). `docker compose up --build` runs the
whole stack; `deploy/k8s/` has plain manifests for a local cluster with a
512Mi web tier and a 2Gi worker tier. The web process never imports scanpy;
the worker pins BLAS/numba threads to its CPU limit and persists the numba JIT
cache. CI runs the tests, a production settings check, and builds + boots the
image. Details and known gaps in deploy/README.md.
