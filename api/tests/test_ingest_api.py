import os
import uuid as _uuid

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


def test_review_and_approve_reject_flow(tmp_path, monkeypatch):
    from app import ingest_api
    # wiki_ops를 스텁으로: API 로직만 검증 (git 실동작은 test_wiki_ops가 검증)
    calls = {}
    monkeypatch.setattr(ingest_api.wiki_ops, "diff_branch", lambda r, s: "diff-텍스트")
    monkeypatch.setattr(ingest_api.wiki_ops, "approve_branch",
                        lambda r, s, m, resolutions=None: calls.setdefault("approve", resolutions))
    monkeypatch.setattr(ingest_api.wiki_ops, "reject_branch", lambda r, s: calls.setdefault("reject", s))
    monkeypatch.setattr(ingest_api.db, "upsert_staged", lambda s: {"technologies": 1})
    monkeypatch.setattr(ingest_api.db, "discard_staged", lambda s: None)
    monkeypatch.setattr(ingest_api.db, "list_staged", lambda s: {"technologies": [], "needs_review": []})

    from app import db as real_db
    task_id, source_id = str(_uuid.uuid4()), str(_uuid.uuid4())
    real_db.create_task(task_id, source_id)
    try:
        with real_db.connect() as conn:
            conn.execute(
                "UPDATE ingest_tasks SET status='staged', branch_name=%s, "
                "affected_pages=%s, contradictions=%s WHERE task_id=%s",
                (f"ingest/{source_id}",
                 '[{"path": "tech/a.md", "action": "create"}, {"path": "tech/b.md", "action": "suggested"}]',
                 '[{"summary": "모순1"}]', task_id),
            )
        r = client.get(f"/api/v1/ingest/{task_id}/review")
        assert r.status_code == 200
        body = r.json()
        assert body["wiki_diff"] == "diff-텍스트"
        assert body["source_id"] == source_id
        assert body["suggestions"] == [{"path": "tech/b.md", "action": "suggested"}]
        assert body["contradictions"] == [{"summary": "모순1"}]

        r = client.post(f"/api/v1/ingest/{task_id}/approve",
                        headers={"X-Admin-Key": "testkey"},
                        json={"contradiction_resolutions": {"x-1": "keep"}})
        assert r.status_code == 200
        assert calls["approve"] == {"x-1": "keep"}
        assert real_db.get_task(task_id)["status"] == "approved"

        # 이미 approved → 재승인 409
        r = client.post(f"/api/v1/ingest/{task_id}/approve", headers={"X-Admin-Key": "testkey"})
        assert r.status_code == 409

        # reject도 409 (approved 상태)
        r = client.post(f"/api/v1/ingest/{task_id}/reject", headers={"X-Admin-Key": "testkey"})
        assert r.status_code == 409
    finally:
        real_db.delete_task(task_id)


def test_list_tasks_endpoint():
    r = client.get("/api/v1/ingest")
    assert r.status_code == 200
    assert "tasks" in r.json()
