"""Shared fixtures.

Every test gets its own SQLite database and vector store, so tests never touch
`data/astrix.db` and never see each other's mission memory. The trained detector
under `data/models` is shared read-only: retraining it per test would add minutes
and test nothing new.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# LLM calls would make results nondeterministic and slow; the deterministic
# reasoners exercise the same loop.
os.environ.setdefault("ASTRIX_LLM_ENABLED", "false")

from backend.app.bootstrap import Astrix  # noqa: E402
from backend.app.config import Settings  # noqa: E402


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'astrix.db').as_posix()}",
        vector_path=str(tmp_path / "vectors.json"),
        llm_enabled=False,
    )


@pytest.fixture
def astrix(tmp_path: Path):
    instance = Astrix(make_settings(tmp_path))
    yield instance
    instance.engine.dispose()
