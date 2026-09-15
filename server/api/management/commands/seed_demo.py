"""Create a demo user and register the PBMC3k tarball as a dataset.

    python server/manage.py seed_demo

Idempotent. The dataset is validated by the worker like any upload, so run
`manage.py rqworker default` (or set CELLENGINE_ENV=test) for it to become ready.
"""
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from api import jobs
from api.infra import enqueue
from api.models import Dataset

DEMO_USER, DEMO_PASS = "demo", "demo-password-1"


class Command(BaseCommand):
    help = "Create demo user + PBMC3k dataset"

    def handle(self, *args, **opts):
        User = get_user_model()
        user, created = User.objects.get_or_create(username=DEMO_USER)
        if created:
            user.set_password(DEMO_PASS)
            user.save()
            self.stdout.write(f"created user {DEMO_USER} / {DEMO_PASS}")

        src = Path(settings.REPO_DIR) / "data" / "pbmc3k_filtered_gene_bc_matrices.tar.gz"
        if not src.exists():
            raise CommandError(f"{src} missing: run scripts/fetch_pbmc3k.py first")
        if Dataset.objects.filter(owner=user, name="PBMC 3k (10x)").exists():
            self.stdout.write("demo dataset already registered")
            return
        with open(src, "rb") as fh:
            ds = Dataset.objects.create(
                owner=user, name="PBMC 3k (10x)", file=File(fh, name=src.name),
                original_filename=src.name, size_bytes=src.stat().st_size,
            )
        enqueue(jobs.validate_dataset, str(ds.id))
        self.stdout.write(f"registered dataset {ds.id}; validation enqueued")
