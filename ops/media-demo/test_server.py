import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "POOL", Mock())
    server.app.dependency_overrides[server.identity] = lambda: "alice"
    with TestClient(server.app) as value:
        yield value
    server.app.dependency_overrides.clear()


def test_job_owner_isolation(client):
    response = client.post("/jobs", data={"kind": "image"}, files={"file": ("sample.png", b"fixture", "image/png")})
    assert response.status_code == 202
    job_id = response.json()["id"]
    assert len(client.get("/jobs").json()["jobs"]) == 1
    server.app.dependency_overrides[server.identity] = lambda: "bob"
    assert client.get("/jobs").json()["jobs"] == []
    assert client.get(f"/jobs/{job_id}").status_code == 404


def test_queue_bound(client):
    for _ in range(8):
        assert client.post("/jobs", data={"kind": "audio"}, files={"file": ("a.wav", b"fixture")}).status_code == 202
    assert client.post("/jobs", data={"kind": "audio"}, files={"file": ("a.wav", b"fixture")}).status_code == 429


def test_rejects_unsupported_and_empty(client):
    assert client.post("/jobs", data={"kind": "video"}, files={"file": ("a.m3u8", b"fixture")}).status_code == 422
    assert client.post("/jobs", data={"kind": "video"}, files={"file": ("a.mp4", b"")}).status_code == 413


def test_auth_required(client):
    server.app.dependency_overrides.clear()
    assert client.get("/jobs").status_code == 401
    assert client.post("/jobs", data={"kind": "image", "sample": "true"}).status_code == 401


def test_failed_job_persists(client, monkeypatch):
    response = client.post("/jobs", data={"kind": "image"}, files={"file": ("a.png", b"bad image")})
    job_id = response.json()["id"]
    monkeypatch.setattr(server.subprocess, "run", lambda *args, **kw: Mock(returncode=1))
    server.execute(job_id, "image", Path("a.png"), "en", "original")
    result = client.get(f"/jobs/{job_id}").json()
    assert result["state"] == "failed"
    assert result["error"]
