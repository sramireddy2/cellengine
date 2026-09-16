"""Orphaned-run reconciliation against a real RQ job object in fake Redis."""
import pytest
from rq.job import Job, JobStatus

from api import jobs
from api.infra import get_redis
from api.models import Dataset, Run
from api.reconcile import reconcile_run, sweep_all
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db


@pytest.fixture
def run():
    user = get_user_model().objects.create_user("u", password="x")
    ds = Dataset.objects.create(owner=user, name="d", file="x.h5ad", original_filename="x.h5ad", size_bytes=1)
    return Run.objects.create(dataset=ds, owner=user, status=Run.Status.PREPROCESSING)


def make_job(status):
    job = Job.create(jobs.run_pipeline, args=("run-id",), connection=get_redis())
    job.save()
    job.set_status(status)
    return job


def test_live_job_is_left_alone(run):
    run.job_id = make_job(JobStatus.STARTED).id
    run.save()
    assert reconcile_run(run).status == "preprocessing"


def test_dead_job_fails_the_run(run):
    run.job_id = make_job(JobStatus.FAILED).id
    run.save()
    r = reconcile_run(run)
    assert r.status == "failed"
    assert "Orphaned during preprocessing" in r.error
    assert r.finished_at is not None


def test_missing_job_fails_the_run(run):
    run.job_id = "does-not-exist"
    run.save()
    assert reconcile_run(run).status == "failed"
    assert "missing" in Run.objects.get(id=run.id).error


def test_terminal_runs_untouched(run):
    run.status, run.job_id = Run.Status.DONE, "does-not-exist"
    run.save()
    assert reconcile_run(run).status == "done"


def test_sweep_all_counts(run):
    run.job_id = "gone"
    run.save()
    assert sweep_all() == 1
    assert sweep_all() == 0
