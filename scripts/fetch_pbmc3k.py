"""Download the 10x PBMC 3k demo dataset (10x MTX tarball, ~7 MB) into data/."""
import sys
import urllib.request
from pathlib import Path

URL = "https://cf.10xgenomics.com/samples/cell/pbmc3k/pbmc3k_filtered_gene_bc_matrices.tar.gz"
dest = Path(__file__).resolve().parent.parent / "data" / "pbmc3k_filtered_gene_bc_matrices.tar.gz"

if dest.exists():
    print(f"already present: {dest}")
    sys.exit(0)
dest.parent.mkdir(exist_ok=True)
print(f"downloading {URL}")
req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (cellengine fetch)"})
with urllib.request.urlopen(req) as r, open(dest, "wb") as f:
    f.write(r.read())
print(f"saved {dest} ({dest.stat().st_size/1e6:.1f} MB)")
