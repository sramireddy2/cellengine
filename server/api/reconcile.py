"""Reconcile Run rows against the RQ job that owns them.

A run's status is written by the job itself. If the worker (or its forked
work horse) is OOM-killed mid-job, nobody writes "failed": the row is stuck
at "preprocessing" and the UI shows a spinner forever.

Postgres is the source of truth for results; Redis is the source of truth
for whether a job is still alive. So: for any in-progress run, ask RQ what
happened to its job, and if the job is dead, say so on the row.

Called from two places:
  - the API, whenever a client polls an in-progress run (cheap: one Redis GET),
    so the person watching the spinner is the one who gets the answer;
  - the worker at boot (manage.py sweep_runs), so nothing stays stuck after a
    restart even if nobody is polling.
"""
import logging

from django.utils import timezone

from .infra import get_redis
from .models import Run

log = logging.getLogger(__name__)

DEAD = {"failed", "stopped", "canceled"}


def reconcile_run(run: Run) -> Run:
    if run.status not in Run.IN_PROGRESS or not run.job_id:
        return run                   # terminal already, or ran inline (test mode) with no RQ job
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    try:
        job = Job.fetch(run.job_id, connection=get_redis())
    except NoSuchJobError:
        return _fail(run, "job record missing from Redis (expired or never enqueued); worker likely restarted")

    status = job.get_status(refresh=False)
    if status in DEAD:
        tail = (job.latest_result().exc_string if job.latest_result() else "") or ""
        tail = tail.strip().splitlines()[-1] if tail.strip() else "no traceback recorded"
        return _fail(run, f"worker reported job {status}: {tail}")
    if status == "finished":
        # The job returned but never wrote a terminal status: should not happen.
        return _fail(run, "job finished without completing the run")
    return run                       # queued / started / deferred / scheduled: still alive


def _fail(run: Run, reason: str) -> Run:
    stage = run.status
    log.warning("reconcile: run %s stuck in %s -> failed (%s)", run.id, stage, reason)
    run.status = Run.Status.FAILED
    run.error = f"Orphaned during {stage}: {reason}"
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "finished_at"])
    return run


def sweep_all() -> int:
    """Reconcile every in-progress run. Returns how many were marked failed."""
    n = 0
    for run in Run.objects.filter(status__in=Run.IN_PROGRESS):
        before = run.status
        if reconcile_run(run).status != before:
            n += 1
    return n
