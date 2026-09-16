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
    open http://localhost:8080

Watch the isolation story:

    kubectl -n cellengine get pods -w          # upload something huge; only the worker restarts
    kubectl -n cellengine logs -f deploy/worker

## What is deliberately not here

- Object storage for uploads (the media PVC is ReadWriteOnce, so web and worker
  share a node). S3/GCS + presigned uploads is the real answer.
- Managed Postgres/Redis. The in-cluster ones exist so `kubectl apply` works
  on a laptop.
- Ingress/TLS. `port-forward` is enough to demo; an Ingress is ten lines when needed.
- Autoscaling the worker on queue depth (KEDA has an RQ scaler). One replica
  makes the memory boundary easy to watch.
