#!/usr/bin/env python
import os
import sys
from pathlib import Path

# Make the repo root importable so `import engine` works from the web and worker processes.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
