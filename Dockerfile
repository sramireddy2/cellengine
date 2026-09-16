# One image, two roles. The web and worker pods run the same code with a
# different command, so there is exactly one thing to build, tag and roll back.
#
#   web:    gunicorn config.wsgi
#   worker: python server/manage.py rqworker default
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # BLAS/numba see the HOST core count, not the container CPU limit. Left alone,
    # OpenBLAS spins 8+ threads inside a 1-CPU pod and everything gets slower
    # (measured: 160 ms vs 9 ms per matmul). Keep these at 1 in the worker: the RQ
    # worker forks a child per job, and GNU OpenMP (scikit-learn kNN) is NOT
    # fork-safe once a pool exists in the parent (measured: signal 11 in every
    # work horse with OMP_NUM_THREADS=2). Scale with replicas, not threads.
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMBA_NUM_THREADS=1 \
    # Persist numba JIT output across worker restarts (mount a volume here).
    NUMBA_CACHE_DIR=/var/cache/numba

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /var/cache/numba /app/media && chown -R app:app /var/cache/numba /app/media

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY engine ./engine
COPY server ./server
COPY frontend ./frontend
COPY scripts ./scripts

# Build-time collectstatic; DJANGO_DEBUG=0 needs a secret key but the value is irrelevant here.
RUN DJANGO_DEBUG=0 DJANGO_SECRET_KEY=build CELLENGINE_ENV=test \
    python server/manage.py collectstatic --noinput -v 0 \
    && chown -R app:app /app

USER app
ENV DJANGO_DEBUG=0 \
    CELLENGINE_MEDIA_ROOT=/app/media \
    PYTHONPATH=/app:/app/server
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fsS http://localhost:8000/api/healthz/ || exit 1

CMD ["gunicorn", "config.wsgi:application", "--chdir", "server", "--bind", "0.0.0.0:8000", \
     "--workers", "2", "--timeout", "120", "--access-logfile", "-"]
