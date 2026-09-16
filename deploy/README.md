# Deploy

## Docker Compose (everything in containers)

    docker compose up --build
    docker compose exec web python server/manage.py seed_demo
    open http://localhost:8000      # demo / demo-password-1

## Kubernetes (local cluster: Docker Desktop, kind, minikube)

    docker build -t cellengine:local .
    # kind only: kind load docker-image cellengine:local
    kubectl apply -f deploy/k8s/
    kubectl -n cellengine wait --for=condition=complete job/migrate --timeout=120s
    kubectl -n cellengine rollout status deploy/web deploy/worker
    kubectl -n cellengine exec deploy/web -- python server/manage.py seed_demo
    kubectl -n cellengine port-forward svc/web 8080:80
    open http://localhost:8080                  # register a user, upload data/pbmc3k_*.tar.gz

Verified on Docker Desktop Kubernetes (kind-based, v1.36): first run 6.6 s,
resolution change 1.1 s submit-to-done, same as compose. The migrate Job may
fail once while Postgres is still pulling; backoffLimit retries it.

Watch the isolation story:

    kubectl -n cellengine get pods -w
    kubectl -n cellengine logs -f deploy/worker

Starve the worker (`kubectl -n cellengine set resources deploy/worker
--limits=memory=256Mi`, then `kubectl apply -f deploy/k8s/20-app.yaml` to
restore) and a run's work horse is OOM-killed; the parent worker and both web
pods stay up, and the next poll reports the run failed with the cause. Same
mechanism as the compose demo below.

The worker runs every numeric library at ONE thread. This is not laziness:
GNU OpenMP is not fork-safe, and the worker forks a child per job. With
OMP_NUM_THREADS=2 every job segfaulted in scikit-learn kNN (signal 11,
found here, reproduced in a bare container). Scale with replicas.

## The OOM demo (measured)

    docker compose -f docker-compose.yml -f deploy/compose.oom-demo.yml up -d worker
    # submit a run with a new preprocessing param (cache miss), then poll it

What happens, in order:

1. RQ forks a work horse for the job; preprocessing starts.
2. ~18 s in, the cgroup OOM killer sends the horse SIGKILL (signal 9).
3. The parent worker survives, logs `Work-horse terminated unexpectedly`, and
   marks the RQ job failed.
4. The next poll of GET /api/runs/{id}/ reconciles the row against Redis and
   returns `status=failed`, error `Orphaned during preprocessing: worker
   reported job failed: Work-horse terminated unexpectedly; signal 9`.
5. The web container never noticed. `docker compose up -d worker` restores the 2G limit.

If the parent itself dies (a hard node OOM), the container restarts and
`sweep_runs` fails any run whose job record is gone before rqworker starts.

## Object storage

Uploads go to a bucket (MinIO in-cluster, `15-minio.yaml`; S3/GCS in
production by changing four env values). The web pod streams the upload in,
the worker streams it to a temp file on its own emptyDir. Nothing on disk is
shared, so web and worker can be on different nodes and the worker can have
many replicas. `manage.py ensure_bucket` creates the bucket at deploy time;
`manage.py wait_for_services` runs first because Kubernetes offers no start
ordering and a Job that retries while MinIO is still pulling burns its
backoff budget (measured: it did).

## Autoscaling the worker (KEDA)

    kubectl apply --server-side -f https://github.com/kedacore/keda/releases/download/v2.20.2/keda-2.20.2.yaml
    kubectl apply -f deploy/k8s-keda/

RQ queues are Redis lists, so KEDA's generic redis scaler targets one queued
job per worker, 1..4 replicas. Measured: six cache-miss runs submitted at
once, worker Deployment 1 -> 4 replicas in 17 s, all six done in 57 s. New
replicas pay the ~25 s JIT warmup first, so this helps sustained load more
than a burst. minReplicaCount stays at 1 for the same reason.

## Local-cluster notes

- `imagePullPolicy: Always` on the app containers: the tag `cellengine:local`
  never changes, and the node caches by tag. Real deployments use immutable
  tags and IfNotPresent.
- If a KEDA pod sits in ImagePullBackOff after install, the Docker Desktop
  registry mirror hiccuped; `kubectl -n keda delete pod <name>` re-pulls.

## What is deliberately not here

- Managed Postgres/Redis/S3. The in-cluster ones exist so `kubectl apply`
  works on a laptop; each is a config change away.
- Ingress/TLS. `port-forward` is enough to demo; an Ingress is ten lines when needed.
- Presigned direct-to-bucket uploads. Today the upload passes through the web
  pod (512Mi limit, streamed to disk then to the bucket). Presigned PUTs would
  take the web tier out of the data path entirely.
