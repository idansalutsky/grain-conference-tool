"""Pytest config — points the DB at a temp file per session.

The tests are intentionally minimal: they cover the **assignment-relevant**
modules (scoring, entity resolution, arc classifier, nudge). They do NOT
mock the LLM — anywhere an LLM call would be needed, we exercise the
deterministic fallback path.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Point the DB at a temp file BEFORE any backend import.
_tmp_dir = Path(tempfile.mkdtemp(prefix="grain_test_"))
os.environ["DATA_DIR"] = str(_tmp_dir)

# Add `backend` to sys.path so `import grain` works.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# Initialise schema once per session.
from grain import db  # noqa: E402

db.init_db()
