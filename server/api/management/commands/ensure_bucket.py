"""Create the upload bucket if object storage is configured and it is missing.

Runs alongside `migrate` at deploy time (compose migrate service, k8s Job).
A no-op on the local filesystem backend.
"""
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the S3 bucket for uploads if it does not exist"

    def handle(self, *args, **opts):
        if not settings.S3_BUCKET or settings.TESTING:
            self.stdout.write("ensure_bucket: filesystem storage, nothing to do")
            return
        import boto3
        from botocore.exceptions import ClientError

        opts_ = settings.STORAGES["default"]["OPTIONS"]
        s3 = boto3.client(
            "s3", endpoint_url=opts_["endpoint_url"], region_name=opts_["region_name"],
            aws_access_key_id=opts_["access_key"], aws_secret_access_key=opts_["secret_key"],
        )
        try:
            s3.head_bucket(Bucket=settings.S3_BUCKET)
            self.stdout.write(f"ensure_bucket: {settings.S3_BUCKET} exists")
        except ClientError:
            s3.create_bucket(Bucket=settings.S3_BUCKET)
            self.stdout.write(f"ensure_bucket: created {settings.S3_BUCKET}")
