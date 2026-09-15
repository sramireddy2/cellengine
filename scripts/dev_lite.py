"""Run the whole app with no Docker: sqlite + in-memory fake Redis + inline jobs.

    python scripts/dev_lite.py [port]

Jobs execute inside the request, so the first run blocks for the numba warmup
(~50 s) and the UI will not show stage-by-stage progress. That is the trade;
use docker compose + rqworker for the real behavior.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
state = Path(os.environ.get("CELLENGINE_LITE_DIR", ROOT / ".lite"))
state.mkdir(exist_ok=True)

env = {
    **os.environ,
    "CELLENGINE_ENV": "test",
    "CELLENGINE_SQLITE": str(state / "db.sqlite3"),
    "CELLENGINE_MEDIA_ROOT": str(state / "media"),
}
py = sys.executable
manage = str(ROOT / "server" / "manage.py")
port = sys.argv[1] if len(sys.argv) > 1 else "8000"

subprocess.run([py, manage, "migrate", "-v", "0"], env=env, check=True)
subprocess.run([py, manage, "seed_demo"], env=env, check=False)
sys.exit(subprocess.run([py, manage, "runserver", port, "--noreload"], env=env).returncode)
