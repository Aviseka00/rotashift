"""Pytest fixtures — set env before importing the FastAPI app (MongoDB required for most tests)."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

# Default local dev; override in CI with MONGO_URI=...
os.environ.setdefault("ROTASHIFT_ENV", "development")
os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("ROTASHIFT_SECRET_KEY", "test-secret-key-for-pytest-only-not-production")


@pytest.fixture(scope="module")
def client() -> TestClient:
    from app.main import app

    c = TestClient(app)
    try:
        c.__enter__()
    except Exception as exc:
        pytest.skip(f"App startup failed (is MongoDB running on MONGO_URI?): {exc!r}")
    yield c
    try:
        c.__exit__(None, None, None)
    except Exception:
        pass


@pytest.fixture(scope="module")
def mongo_ready(client: TestClient) -> bool:
    r = client.get("/health/ready")
    return r.status_code == 200


@pytest.fixture(scope="module")
def require_mongo(client: TestClient, mongo_ready: bool):
    if not mongo_ready:
        pytest.skip("MongoDB not reachable (start Mongo or set MONGO_URI); /health/ready must return 200")


@pytest.fixture
def unique_employee_id() -> str:
    return f"T{uuid.uuid4().hex[:10].upper()}"


@pytest.fixture
def employee_token(client: TestClient, require_mongo, unique_employee_id: str) -> str:
    body = {
        "employee_id": unique_employee_id,
        "password": "pytest-pass-9x",
        "full_name": "QA Test User",
        "department_name": "rota",
        "role": "employee",
    }
    r = client.post("/api/auth/register", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "access_token" in data
    return data["access_token"]


@pytest.fixture
def auth_headers(employee_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {employee_token}"}
