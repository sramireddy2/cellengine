"""Give the worker a local path for a dataset file, wherever it is stored.

engine.io.load_counts wants a filesystem path (h5py and tarfile both do). With
FileSystemStorage that is just .path. With object storage there is no path:
stream the object into a temp file with the original suffix (load_counts
dispatches on it) and delete it afterwards.

Memory note: the download is chunked (8 MB), so a 300 MB upload never sits in
RAM twice. Disk in the worker pod is the buffer, which is what emptyDir is for.
"""
import contextlib
import os
import shutil
import tempfile
from pathlib import Path

from django.core.files.storage import default_storage

from .models import Dataset

CHUNK = 8 * 1024 * 1024


def _suffix(name: str) -> str:
    n = name.lower()
    for s in (".tar.gz", ".tgz", ".h5ad"):
        if n.endswith(s):
            return s
    return Path(name).suffix


@contextlib.contextmanager
def local_copy(dataset: Dataset):
    """Yield a Path to the dataset file; temporary if the storage is remote."""
    storage = dataset.file.storage
    try:
        yield Path(storage.path(dataset.file.name))    # FileSystemStorage: no copy
        return
    except NotImplementedError:
        pass                                          # remote storage: fall through

    fd, tmp = tempfile.mkstemp(suffix=_suffix(dataset.original_filename or dataset.file.name))
    try:
        with os.fdopen(fd, "wb") as out, storage.open(dataset.file.name, "rb") as src:
            shutil.copyfileobj(src, out, CHUNK)
        yield Path(tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def storage_kind() -> str:
    return type(default_storage).__name__
