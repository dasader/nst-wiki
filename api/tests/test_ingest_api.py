import os

os.environ.setdefault("ADMIN_API_KEY", "testkey")
os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_ingest_requires_admin_key():
    r = client.post("/api/v1/ingest", headers={"X-Admin-Key": "wrong"})
    assert r.status_code == 401


def test_ingest_rejects_unsupported_ext(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path))
    r = client.post(
        "/api/v1/ingest",
        headers={"X-Admin-Key": "testkey"},
        files={"file": ("doc.hwp", b"x")},
        data={"title": "테스트"},
    )
    assert r.status_code == 400


def test_status_unknown_task_404():
    r = client.get("/api/v1/ingest/00000000-0000-0000-0000-000000000000/status")
    assert r.status_code == 404


def test_ingest_happy_path_md(tmp_path, monkeypatch):
    import tasks as tasks_mod
    calls = []
    monkeypatch.setattr(tasks_mod.run_ingest, "delay", lambda tid: calls.append(tid))
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path))
    r = client.post(
        "/api/v1/ingest",
        headers={"X-Admin-Key": "testkey"},
        files={"file": ("doc.md", "# 제목\n본문".encode())},
        data={"title": "행복 경로", "tags": "NEXT, 반도체"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert calls == [body["task_id"]]
    import json as _json
    src_dirs = list(tmp_path.iterdir())
    assert len(src_dirs) == 1
    meta = _json.loads((src_dirs[0] / "metadata.json").read_text(encoding="utf-8"))
    assert meta["title"] == "행복 경로"
    assert meta["tags"] == ["NEXT", "반도체"]
    assert meta["file_hash"].startswith("sha256:")
    from app import db
    db.delete_task(body["task_id"])
