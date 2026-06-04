"""
Shared pytest fixtures.

Env is configured BEFORE importing the app so config picks it up:
  * LLM_PROVIDER=stub  → deterministic, key-free replies (verbatim corpus facts).
  * low RATE_LIMIT_MAX → the 429 test is fast; the limiter is reset per test.
"""

import os

os.environ.setdefault("LLM_PROVIDER", "stub")
os.environ.setdefault("RATE_LIMIT_MAX", "5")
os.environ.setdefault("RATE_LIMIT_WINDOW", "60")

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.ratelimit import limiter


@pytest.fixture(scope="session")
def client():
    # `with` triggers FastAPI startup (loads retrieval engine + vector store once).
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Isolate rate-limit state between tests."""
    limiter.reset()
    yield
    limiter.reset()
