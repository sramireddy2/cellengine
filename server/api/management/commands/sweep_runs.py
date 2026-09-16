"""Mark orphaned runs failed. Run at worker boot, before rqworker starts:

    python server/manage.py sweep_runs; exec python server/manage.py rqworker default
"""
from django.core.management.base import BaseCommand

from api.reconcile import sweep_all


class Command(BaseCommand):
    help = "Fail any in-progress run whose RQ job is dead or missing"

    def handle(self, *args, **opts):
        n = sweep_all()
        self.stdout.write(f"sweep_runs: {n} orphaned run(s) marked failed")
