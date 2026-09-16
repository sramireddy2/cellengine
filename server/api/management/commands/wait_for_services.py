"""Block until Postgres, Redis and (if configured) object storage answer.

Kubernetes starts everything at once and offers no ordering, so the migrate
Job runs this first instead of burning its backoff budget while a database
image is still downloading. Compose orders with healthchecks, but running
this there too keeps the two paths identical.
"""
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Wait for Postgres, Redis and object storage to accept connections"

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=int, default=300)

    def handle(self, *args, **opts):
        deadline = time.monotonic() + opts["timeout"]
        for name, check in (("postgres", self._db), ("redis", self._redis), ("object storage", self._s3)):
            attempt = 0
            while True:
                attempt += 1
                try:
                    check()
                    self.stdout.write(f"wait_for_services: {name} ok (attempt {attempt})")
                    break
                except Exception as e:                    # noqa: BLE001
                    if time.monotonic() > deadline:
                        raise CommandError(f"{name} not ready after {opts['timeout']}s: {e}")
                    time.sleep(min(2 * attempt, 10))

    @staticmethod
    def _db():
        from django.db import connection
        connection.close()
        with connection.cursor() as c:
            c.execute("SELECT 1")

    @staticmethod
    def _redis():
        if settings.TESTING:
            return
        from api.infra import get_redis
        get_redis().ping()

    @staticmethod
    def _s3():
        if not settings.S3_BUCKET or settings.TESTING:
            return
        import boto3
        o = settings.STORAGES["default"]["OPTIONS"]
        boto3.client("s3", endpoint_url=o["endpoint_url"], region_name=o["region_name"],
                     aws_access_key_id=o["access_key"], aws_secret_access_key=o["secret_key"]).list_buckets()
