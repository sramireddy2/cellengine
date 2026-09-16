"""local_copy: the worker gets a real path whether the file is local or remote."""
import io

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import Storage

from api.models import Dataset
from api.storage import local_copy

pytestmark = pytest.mark.django_db


class FakeObjectStorage(Storage):
    """Behaves like S3Storage: has open(), has no path()."""
    def __init__(self):
        self.blobs = {}

    def _save(self, name, content):
        self.blobs[name] = content.read()
        return name

    def _open(self, name, mode="rb"):
        return io.BytesIO(self.blobs[name])

    def exists(self, name):
        return name in self.blobs

    def path(self, name):
        raise NotImplementedError("remote storage has no local path")


@pytest.fixture
def user():
    return get_user_model().objects.create_user("u", password="x")


def test_local_storage_yields_real_path(user, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    ds = Dataset.objects.create(owner=user, name="d", original_filename="a.h5ad", size_bytes=3,
                                file=ContentFile(b"abc", name="a.h5ad"))
    with local_copy(ds) as p:
        assert p.read_bytes() == b"abc"
        assert str(p).startswith(str(tmp_path))
    assert p.exists()                    # not a temp file: must not be deleted


def test_remote_storage_downloads_to_temp_with_suffix(user):
    ds = Dataset(owner=user, name="d", original_filename="pbmc.tar.gz", size_bytes=5)
    fake = FakeObjectStorage()
    ds.file.storage = fake
    ds.file.name = fake.save("datasets/pbmc.tar.gz", ContentFile(b"hello"))
    with local_copy(ds) as p:
        assert p.read_bytes() == b"hello"
        assert p.name.endswith(".tar.gz")      # load_counts dispatches on this
        tmp = p
    assert not tmp.exists()                    # cleaned up
