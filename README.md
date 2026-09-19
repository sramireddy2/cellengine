<h1 align="center">🧬 cellengine</h1>

<p align="center">
  <b>Interactive single-cell clustering.</b><br>
  Upload a count matrix, cluster it, drag the resolution slider, and see which genes define each cluster — in about a second.
</p>

<p align="center">
  <a href="https://github.com/sramireddy2/cellengine/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/sramireddy2/cellengine/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.13" src="https://img.shields.io/badge/python-3.13-3776AB?logo=python&logoColor=white">
  <img alt="Django 5" src="https://img.shields.io/badge/Django-5-092E20?logo=django&logoColor=white">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white">
  <img alt="Redis" src="https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white">
  <img alt="Kubernetes" src="https://img.shields.io/badge/Kubernetes-KEDA-326CE5?logo=kubernetes&logoColor=white">
  <img alt="Tests" src="https://img.shields.io/badge/tests-25%20passing-brightgreen">
</p>

<p align="center">
  <img src="docs/umap.png" alt="PBMC 3k UMAP at two resolutions, same embedding, only Leiden reran" width="900">
</p>

---

## What it does

You upload a single-cell RNA-seq count matrix (rows are cells, columns are genes, each value is how many mRNA molecules of that gene were detected in that cell). cellengine runs the standard pipeline, draws the cells as a UMAP scatter colored by cluster, and ranks the marker genes that make each cluster distinct. Move the resolution slider and it reclusters in about a second, because the expensive part is cached.

On the demo dataset (10x PBMC 3k, the "hello world" of the field) the clusters come out as the textbook cell types: T cells light up with `CD3D` and `IL7R`, B cells with `MS4A1` and `CD79A`, monocytes with `CD14` and `S100A8`, NK cells with `NKG7` and `GZMA`.

## 🛠️ Tech stack

| Layer | What | Why this one |
|---|---|---|
| **Science** | [scanpy](https://scanpy.readthedocs.io/), scipy, numpy, scikit-learn, umap-learn, leidenalg | The standard toolkit. Nobody reimplements Leiden; the win is knowing which step depends on which. |
| **Marker stats** | Hand-written Wilcoxon rank-sum + Benjamini-Hochberg, computed directly on the sparse matrix | The one piece I wanted to own end to end. See [the sparse trick](#the-sparse-wilcoxon-trick). |
| **API** | Django 5 + Django REST Framework, session auth | Boring, well-understood, and the ORM makes the transactional writes trivial. |
| **Database** | PostgreSQL 16 | Datasets, runs, one row per cell per run, marker tables. Unique on `(run, barcode)`. |
| **Cache + queue** | Redis 7 with [RQ](https://python-rq.org/) | One process does both jobs at this scale. RQ over Celery because I needed a queue, not a framework. |
| **Object storage** | S3 API (MinIO locally) via django-storages | Web and worker share no disk, so the worker can scale out. |
| **Frontend** | Plain HTML + one JS file, `<canvas>` scatter | No build step. Canvas because 50k SVG nodes is where browsers give up. |
| **Packaging** | One Docker image, two roles (web / worker) | One thing to build, tag, and roll back. |
| **Orchestration** | Kubernetes manifests + [KEDA](https://keda.sh/) scaling the worker on queue depth | Memory-capped worker pods so a bad upload kills a job, not the site. |
| **CI** | GitHub Actions: pytest, production settings check, image build + boot smoke test | Green badge or it didn't happen. |

## 🧠 The one design decision that matters

```
QC → normalize → HVG → PCA → kNN graph → UMAP      cached    (~6 s warm, ~50 s cold)
                                       → Leiden     rerun     (~0.1 s)
```

Everything left of the arrow depends only on the dataset and the preprocessing parameters. Leiden's resolution, the knob researchers actually turn, sits downstream of all of it. So the graph and the UMAP are cached in Redis under `sha256(dataset_id + preprocessing_params)`, and a resolution change reruns Leiden alone and recolors the same dots. The points never move when you drag the slider, which is both a nicer experience and visible proof that only Leiden ran.

## 📈 Numbers, not adjectives

Everything below was measured, most of it more than once. Dataset is PBMC 3k: 2,700 cells × 32,738 genes.

**Submit to done**, through the containerized stack, including queue pickup and both Postgres commits:

| Run | Cache | Time |
|---|---|---|
| First run on a dataset | miss | **6.2 s** |
| Resolution change | hit | **1.0 s** |
| Six cache-miss runs at once on Kubernetes | KEDA scaled the worker 1 → 4 replicas in 17 s | all done in 57 s |

**Per stage**, inside the worker:

| Step | Cold process | Warm |
|---|---|---|
| Load 10x tarball | 1.3 s | 1.3 s |
| QC → normalize → HVG → PCA → kNN → UMAP | ~50 s | ~7 s |
| Leiden | 0.1 s | 0.1 s |
| Marker genes (Wilcoxon + BH, ~14k genes × k clusters) | 0.85 s | 0.85 s |

The cold column is numba compiling scanpy's kernels. It's paid once per worker process, which is why the worker is a long-lived Deployment rather than a Job per upload, and why it warms the JIT on a toy matrix at boot.

**Memory**, same dataset:

| Representation | Size |
|---|---|
| Raw counts, dense float32 | 354 MB |
| Raw counts, CSR (2.6% non-zero) | **18 MB** |
| The only dense allocation: 2,643 × 2,000 HVG slice handed to PCA | 21 MB |
| Marker test working set | proportional to non-zeros, never cells × genes |

## 🔬 The sparse Wilcoxon trick

For every (cluster, gene) pair I need a one-vs-rest Wilcoxon rank-sum test. The naive version densifies each gene column and ranks it. But 93% of the matrix is zeros, and every zero in a column is a single tie group whose average rank is just `(n_zeros + 1) / 2`. So the implementation sorts only the non-zeros once (one `lexsort` keyed by gene then value), assigns tie-averaged ranks, and adds the zero group analytically. Rank sums, counts, and means per (cluster, gene) fall out of `np.bincount` on a flattened key.

Result: 6.8 s → 0.85 s, identical output, verified against `scipy.stats.mannwhitneyu` to six decimal places. And with 14,000 genes per cluster, Benjamini-Hochberg isn't optional: uncorrected `p < 0.05` would hand you ~700 false positives per cluster. There's also a fold-change floor, because with 500 cells in a cluster a housekeeping gene hits `p < 1e-40` on a 1.3× shift.

## 🧱 How a run flows

```
POST /api/datasets/          multipart upload → object storage → worker validates the shape
POST /api/runs/              {dataset, params} → 202 + run id
GET  /api/runs/{id}/         poll: queued → preprocessing → clustering → markers → done
GET  /api/runs/{id}/cells/   four parallel arrays: barcodes, x, y, cluster
GET  /api/runs/{id}/markers/?cluster=3
```

A few things I'd defend in a design review:

- **Two commits per run.** Cell labels land in one transaction and the status flips to `markers`, so the scatter can render while the Wilcoxon test finishes. Markers land in a second transaction. A run showing 60% of its cells is worse than no run.
- **The web process never imports scanpy.** Measured: it boots in 1.8 s with neither scanpy nor numba loaded. Opening a matrix inside a request handler is exactly the memory spike the worker tier exists to absorb.
- **Runs are reconciled against the queue on every poll.** If the worker's job process is OOM-killed, the next `GET` asks Redis what happened and reports `failed` with the stage and the signal. I starved the worker to 256 MB and watched this happen; the web tier never noticed. Details in [deploy/README.md](deploy/README.md).
- **Unknown parameters are rejected.** A typo like `resolutoin` is a 400 with the key named, not a silent run on defaults.

## 🐛 Five things profiling taught me

These are the parts I'd actually want to talk about.

1. **numpy skips BLAS on mixed dtypes.** `scipy.stats.rankdata` returns float32 for float32 input, and a float64 @ float32 matmul falls off the fast path. 150 ms → 2 ms per chunk once everything was float64.
2. **Thread pools fight.** After scanpy loads, OpenBLAS and numba both spin up pools. A 9 ms matmul became 160 ms. Containers make this worse because BLAS sees the host's cores, not the cgroup limit.
3. **GNU OpenMP is not fork-safe.** The RQ worker forks a child per job. With `OMP_NUM_THREADS=2`, the boot warmup started a libgomp pool in the parent and every child segfaulted inside scikit-learn's kNN. Found on Kubernetes, reproduced in a bare container, fixed with one thread per library and `PYTHONFAULTHANDLER=1`, which is what turned "signal 11" into a traceback.
4. **Fork re-imports lazy modules.** Making the web tier lean meant importing scanpy lazily inside the job, which then cost ~2 s in every forked child. The worker parent now imports the science stack at boot so forks inherit it. Cache hits went from 3 s to 1 s.
5. **On Windows, `localhost` costs 2 seconds.** Python's HTTP client tries IPv6 first; Django's dev server binds IPv4. Use `127.0.0.1` before you benchmark anything.

## 🚀 Running it

**Tests only** (sqlite + fake Redis + inline jobs, no services needed):

```bash
python -m venv .venv && .venv/Scripts/activate      # or source .venv/bin/activate
pip install -r requirements.txt
pytest
```

**No-Docker demo**, same trick, whole app on one process:

```bash
python scripts/fetch_pbmc3k.py
python scripts/dev_lite.py           # http://localhost:8000 · demo / demo-password-1
```

**The real stack** (Postgres, Redis, MinIO, web, worker):

```bash
docker compose up --build
```

**Kubernetes** with a memory-capped worker and queue-depth autoscaling: see [deploy/README.md](deploy/README.md). It includes the OOM demo and the exact commands.

## 🗂️ Layout

```
engine/      framework-free science core: pipeline, cache serialization, sparse Wilcoxon
server/      Django + DRF API, models, RQ jobs, reconciliation, management commands
frontend/    index.html + app.js + style.css, served by Django, no build step
deploy/      Dockerfile lives at the root; k8s manifests, KEDA scaler, deploy notes here
tests/       25 tests: scipy oracle for the stats, planted-marker recovery, cache roundtrip,
             end-to-end API with cache hit/miss, orphaned-run reconciliation, remote storage
```

## 🙋 Why I built it

Before single-cell sequencing you could only measure average gene expression across a whole tissue sample, which blurs distinct cell populations together. Clustering individual cells by expression is how you find those populations, and marker genes are how you name them. I wanted a project where the systems work (caching, sparse memory, transactional writes, worker isolation) was in service of something real, and where every architectural claim had a number behind it.

Scoped deliberately: 3k–50k cells, one dataset format family, one clustering algorithm. The architecture scales further; I'd rather show one measured path than claim ten.
