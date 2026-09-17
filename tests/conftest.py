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
        corpus_path=str(tmp_path / "corpus" / "corpus.db"),
        llm_enabled=False,
    )


@pytest.fixture
def astrix(tmp_path: Path):
    instance = Astrix(make_settings(tmp_path))
    yield instance
    instance.engine.dispose()


# The console is account-gated, so an API test has to be somebody. This registers
# a throwaway operator against the client's own (temporary) database and pins the
# session token onto the client, so every later request in that test is signed in.
TEST_ACCOUNT = {
    "email": "test-operator@astrix.test",
    "password": "orbital-drift-7741",
    "name": "Test Operator",
}


def sign_in(test_client):
    """Register-or-sign-in the test operator and authorise `test_client`."""
    response = test_client.post("/auth/register", json=TEST_ACCOUNT)
    if response.status_code == 409:  # module-scoped client, already registered
        response = test_client.post(
            "/auth/login",
            json={"email": TEST_ACCOUNT["email"], "password": TEST_ACCOUNT["password"]},
        )
    response.raise_for_status()
    token = response.json()["token"]
    test_client.headers["Authorization"] = f"Bearer {token}"
    return token
