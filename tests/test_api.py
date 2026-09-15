"""End-to-end API tests: sqlite + fake Redis + synchronous jobs (see api.infra).

The toy dataset is written to an .h5ad in a temp MEDIA_ROOT so the upload,
validation, and pipeline paths all exercise real file IO.
"""
import io

import pytest
from django.test import override_settings

from api.models import CellCluster, Dataset, MarkerGene, Run

pytestmark = pytest.mark.django_db


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture(scope="session")
def h5ad_bytes(toy_adata, tmp_path_factory):
    p = tmp_path_factory.mktemp("toy") / "toy.h5ad"
    toy_adata.write_h5ad(p)          # anndata wants a path, not a buffer
    return p.read_bytes()


@pytest.fixture
def user_client(client):
    r = client.post("/api/auth/register/", {"username": "alice", "password": "correct-horse"},
                    content_type="application/json")
    assert r.status_code == 201, r.content
    return client


PARAMS = {"min_genes_per_cell": 1, "min_cells_per_gene": 1, "max_pct_mito": 100,
          "n_top_genes": 100, "n_pcs": 10, "n_neighbors": 10}


def upload(client, h5ad_bytes, name="toy"):
    f = io.BytesIO(h5ad_bytes)
    f.name = "toy.h5ad"
    r = client.post("/api/datasets/", {"name": name, "file": f})
    assert r.status_code == 201, r.content
    return r.json()


def test_auth_required(client):
    assert client.get("/api/datasets/").status_code == 403
    assert client.get("/api/auth/me/").json() == {"username": None}


def test_upload_rejects_bad_extension(user_client, media_root):
    f = io.BytesIO(b"nope")
    f.name = "counts.csv"
    r = user_client.post("/api/datasets/", {"file": f})
    assert r.status_code == 400
    assert "h5ad" in r.json()["file"][0]


def test_upload_validates_and_records_shape(user_client, media_root, h5ad_bytes):
    ds = upload(user_client, h5ad_bytes)
    ds = user_client.get(f"/api/datasets/{ds['id']}/").json()
    assert ds["status"] == "ready"
    assert (ds["n_cells"], ds["n_genes"]) == (600, 400)


def test_corrupt_upload_fails_cleanly(user_client, media_root):
    f = io.BytesIO(b"this is not hdf5")
    f.name = "bad.h5ad"
    r = user_client.post("/api/datasets/", {"file": f})
    ds = user_client.get(f"/api/datasets/{r.json()['id']}/").json()
    assert ds["status"] == "failed" and ds["error"]


def test_run_rejects_unknown_params(user_client, media_root, h5ad_bytes):
    ds = upload(user_client, h5ad_bytes)
    r = user_client.post("/api/runs/", {"dataset": ds["id"], "params": {"resolutoin": 1.0}},
                         content_type="application/json")
    assert r.status_code == 400
    assert "resolutoin" in str(r.json())


def test_full_run_and_cache_hit(user_client, media_root, h5ad_bytes):
    ds = upload(user_client, h5ad_bytes)

    # --- first run: cache miss, full preprocess ------------------------------
    r = user_client.post("/api/runs/", {"dataset": ds["id"], "params": {**PARAMS, "resolution": 0.3}},
                         content_type="application/json")
    assert r.status_code == 202, r.content
    run = user_client.get(f"/api/runs/{r.json()['id']}/").json()
    assert run["status"] == "done", run
    assert run["cache_hit"] is False
    assert run["n_clusters"] >= 3
    assert {"preprocess", "leiden", "write_cells", "markers"} <= set(run["timings"])

    cells = user_client.get(f"/api/runs/{run['id']}/cells/").json()
    assert cells["n"] == 600
    assert len(cells["x"]) == len(cells["y"]) == len(cells["cluster"]) == 600
    assert max(cells["cluster"]) + 1 == run["n_clusters"]

    clusters = user_client.get(f"/api/runs/{run['id']}/clusters/").json()
    assert sum(c["n_cells"] for c in clusters) == 600
    assert clusters[0]["n_cells"] >= clusters[-1]["n_cells"]     # cluster 0 is the largest

    mk = user_client.get(f"/api/runs/{run['id']}/markers/?cluster=0").json()
    assert mk["status"] == "done"
    assert 1 <= len(mk["markers"]) <= 25
    assert mk["markers"][0]["rank"] == 1 and mk["markers"][0]["padj"] < 0.05

    # --- second run, new resolution, same preprocessing: cache hit -----------
    r = user_client.post("/api/runs/", {"dataset": ds["id"], "params": {**PARAMS, "resolution": 3.0}},
                         content_type="application/json")
    run2 = user_client.get(f"/api/runs/{r.json()['id']}/").json()
    assert run2["status"] == "done"
    assert run2["cache_hit"] is True
    assert run2["preprocess_key"] == run["preprocess_key"]
    assert run2["n_clusters"] > run["n_clusters"]
    assert "pre_pca" not in run2["timings"]                      # no preprocessing happened

    # --- changed preprocessing param => different key => miss ----------------
    r = user_client.post("/api/runs/", {"dataset": ds["id"], "params": {**PARAMS, "n_pcs": 5}},
                         content_type="application/json")
    run3 = user_client.get(f"/api/runs/{r.json()['id']}/").json()
    assert run3["cache_hit"] is False and run3["preprocess_key"] != run["preprocess_key"]

    # rows are per run, unique per (run, barcode)
    assert CellCluster.objects.filter(run_id=run["id"]).count() == 600
    assert CellCluster.objects.filter(run_id=run2["id"]).count() == 600


def test_users_cannot_see_each_other(user_client, media_root, h5ad_bytes, client):
    ds = upload(user_client, h5ad_bytes)
    from django.test import Client
    other = Client()
    other.post("/api/auth/register/", {"username": "bob", "password": "correct-horse"},
               content_type="application/json")
    assert other.get(f"/api/datasets/{ds['id']}/").status_code == 404
    r = other.post("/api/runs/", {"dataset": ds["id"]}, content_type="application/json")
    assert r.status_code == 400                                   # not in bob's queryset


def test_failed_run_is_recorded(user_client, media_root, h5ad_bytes):
    """Corrupt the file after validation; the run must land in failed with an error, not hang."""
    ds = upload(user_client, h5ad_bytes)
    d = Dataset.objects.get(id=ds["id"])
    with open(d.file.path, "wb") as fh:
        fh.write(b"garbage")
    r = user_client.post("/api/runs/", {"dataset": ds["id"], "params": {**PARAMS, "n_pcs": 7}},
                         content_type="application/json")
    assert r.status_code == 202
    run = Run.objects.get(id=r.json()["id"])
    assert run.status == "failed" and run.error
    assert CellCluster.objects.filter(run=run).count() == 0
