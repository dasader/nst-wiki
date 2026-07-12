import os
import uuid as _uuid

import pytest

os.environ.setdefault("ADMIN_API_KEY", "testkey")
os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_ingest_requires_admin_key():
    r = client.post("/api/v1/ingest", headers={"X-Admin-Key": "wrong"})
    assert r.status_code == 401


def test_admin_verify_accepts_valid_key():
    r = client.get("/api/v1/admin/verify", headers={"X-Admin-Key": "testkey"})
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_admin_verify_rejects_bad_key():
    assert client.get("/api/v1/admin/verify", headers={"X-Admin-Key": "wrong"}).status_code == 401


def test_ingest_rejects_unsupported_ext(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path))
    r = client.post(
        "/api/v1/ingest",
        headers={"X-Admin-Key": "testkey"},
        files={"file": ("doc.hwp", b"x")},
        data={"title": "테스트", "publish_date": "2026"},
    )
    assert r.status_code == 400


def test_ingest_requires_publish_date(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path))
    r = client.post(
        "/api/v1/ingest",
        headers={"X-Admin-Key": "testkey"},
        files={"file": ("doc.md", b"x")},
        data={"title": "테스트"},  # publish_date 누락
    )
    assert r.status_code == 422  # Form(...) 필수 — 필드 자체 누락


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
        data={"title": "행복 경로", "tags": "NEXT, 반도체", "publish_date": "2026"},
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
    assert meta["publish_date"] == "2026"
    assert meta["tags"] == ["NEXT", "반도체"]
    assert meta["file_hash"].startswith("sha256:")
    from app import db
    db.delete_task(body["task_id"])


def test_review_and_approve_reject_flow(tmp_path, monkeypatch):
    from app import ingest_api
    import tasks as tasks_mod
    # wiki_ops를 스텁으로: API 로직만 검증 (git 실동작은 test_wiki_ops가 검증)
    calls = {}
    embed_calls = []
    monkeypatch.setattr(ingest_api.wiki_ops, "diff_branch", lambda r, s: "diff-텍스트")
    monkeypatch.setattr(ingest_api.wiki_ops, "approve_branch",
                        lambda r, s, m, resolutions=None, resolve_conflict=None: calls.setdefault("approve", resolutions))
    monkeypatch.setattr(ingest_api.wiki_ops, "reject_branch", lambda r, s: calls.setdefault("reject", s))
    monkeypatch.setattr(ingest_api.db, "upsert_staged", lambda s: {"technologies": 1})
    monkeypatch.setattr(ingest_api.db, "discard_staged", lambda s: None)
    monkeypatch.setattr(ingest_api.db, "list_staged", lambda s: {"technologies": [], "needs_review": []})
    monkeypatch.setattr(tasks_mod.embed_pages, "delay", lambda paths: embed_calls.append(paths))

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
        assert embed_calls == [["tech/a.md"]]  # create만 enqueue, suggested는 제외

        # 이미 approved → 재승인 409
        r = client.post(f"/api/v1/ingest/{task_id}/approve", headers={"X-Admin-Key": "testkey"})
        assert r.status_code == 409

        # reject도 409 (approved 상태)
        r = client.post(f"/api/v1/ingest/{task_id}/reject", headers={"X-Admin-Key": "testkey"})
        assert r.status_code == 409
    finally:
        real_db.delete_task(task_id)


def test_approve_applies_contradiction_resolution_to_page(tmp_path, monkeypatch):
    """승인 시 모순 결정이 실제 페이지 본문에 반영된다 (log 도장뿐 아니라 write_page 호출)."""
    from app import ingest_api
    import pipeline.narrative as narrative
    import tasks as tasks_mod
    from app import db as real_db

    monkeypatch.setattr(ingest_api.wiki_ops, "approve_branch",
                        lambda r, s, m, resolutions=None, resolve_conflict=None: None)
    monkeypatch.setattr(ingest_api.db, "upsert_staged", lambda s: {})
    monkeypatch.setattr(ingest_api.wiki_ops, "read_page", lambda root, p: "기존 본문")
    monkeypatch.setattr(narrative, "apply_resolutions",
                        lambda path, content, decisions: f"반영됨:{decisions[0]['action']}")
    writes = []
    monkeypatch.setattr(ingest_api.wiki_ops, "write_page",
                        lambda root, p, content, msg: writes.append((p, content)))
    monkeypatch.setattr(tasks_mod.embed_pages, "delay", lambda paths: None)

    task_id, source_id = str(_uuid.uuid4()), str(_uuid.uuid4())
    real_db.create_task(task_id, source_id)
    try:
        with real_db.connect() as conn:
            conn.execute(
                "UPDATE ingest_tasks SET status='staged', branch_name=%s, contradictions=%s "
                "WHERE task_id=%s",
                (f"ingest/{source_id}",
                 '[{"page": "tech/a.md", "summary": "s", "existing": "e", "new": "n"}]', task_id),
            )
        cid = f"{source_id[:8]}-1"  # frontend/backend 공통 규약
        r = client.post(f"/api/v1/ingest/{task_id}/approve",
                        headers={"X-Admin-Key": "testkey"},
                        json={"contradiction_resolutions": {cid: "replace"}})
        assert r.status_code == 200
        assert writes == [("tech/a.md", "반영됨:replace")]  # 결정이 본문에 확정됨
    finally:
        real_db.delete_task(task_id)


def test_list_tasks_endpoint():
    r = client.get("/api/v1/ingest")
    assert r.status_code == 200
    assert "tasks" in r.json()


def test_delete_source_endpoint(tmp_path, monkeypatch):
    from app import ingest_api
    from app import db as real_db
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path))
    monkeypatch.setattr(ingest_api.db, "delete_source",
                        lambda s: {"technologies": 2, "projects": 1})
    deleted_pages = []
    monkeypatch.setattr(ingest_api.wiki_ops, "delete_page",
                        lambda root, rel, msg: deleted_pages.append(rel) or True)

    task_id, source_id = str(_uuid.uuid4()), str(_uuid.uuid4())
    (tmp_path / source_id).mkdir()
    (tmp_path / source_id / "original.pdf").write_bytes(b"x")
    real_db.create_task(task_id, source_id)
    try:
        r = client.delete(f"/api/v1/ingest/{task_id}/source",
                          headers={"X-Admin-Key": "testkey"})
        assert r.status_code == 200
        body = r.json()
        assert body["deleted"] == {"technologies": 2, "projects": 1}
        assert body["summary_page_deleted"] is True
        assert body["wiki_narrative_remains"] is True
        assert deleted_pages == [f"summaries/{source_id}.md"]
        assert real_db.get_task(task_id) is None      # 태스크 삭제됨
        assert not (tmp_path / source_id).exists()     # 소스 디렉토리 삭제됨
    finally:
        real_db.delete_task(task_id)


def test_delete_source_requires_admin_and_404():
    r = client.delete("/api/v1/ingest/x/source", headers={"X-Admin-Key": "wrong"})
    assert r.status_code == 401
    r = client.delete("/api/v1/ingest/00000000-0000-0000-0000-000000000000/source",
                      headers={"X-Admin-Key": "testkey"})
    assert r.status_code == 404


def test_download_original(tmp_path, monkeypatch):
    from app import db as real_db
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path))
    task_id, source_id = str(_uuid.uuid4()), str(_uuid.uuid4())
    (tmp_path / source_id).mkdir()
    (tmp_path / source_id / "original.pdf").write_bytes("%PDF-1.4 데이터".encode())
    import json as _json
    (tmp_path / source_id / "metadata.json").write_text(
        _json.dumps({"title": "테스트문서"}), encoding="utf-8")
    real_db.create_task(task_id, source_id)
    try:
        r = client.get(f"/api/v1/ingest/{task_id}/original",
                       headers={"X-Admin-Key": "testkey"})
        assert r.status_code == 200
        assert r.content == "%PDF-1.4 데이터".encode()
        # 404 when missing
        (tmp_path / source_id / "original.pdf").unlink()
        r = client.get(f"/api/v1/ingest/{task_id}/original",
                       headers={"X-Admin-Key": "testkey"})
        assert r.status_code == 404
    finally:
        real_db.delete_task(task_id)


def test_approve_reverts_to_staged_on_wiki_failure(monkeypatch):
    from app import ingest_api
    from app import db as real_db
    monkeypatch.setattr(ingest_api.db, "upsert_staged", lambda s: {})
    def boom(*a, **k):
        raise RuntimeError("wiki 실패")
    monkeypatch.setattr(ingest_api.wiki_ops, "approve_branch", boom)
    task_id, source_id = str(_uuid.uuid4()), str(_uuid.uuid4())
    real_db.create_task(task_id, source_id)
    try:
        with real_db.connect() as conn:
            conn.execute(
                "UPDATE ingest_tasks SET status='staged', branch_name=%s WHERE task_id=%s",
                (f"ingest/{source_id}", task_id),
            )
        local_client = TestClient(app, raise_server_exceptions=False)
        r = local_client.post(f"/api/v1/ingest/{task_id}/approve",
                              headers={"X-Admin-Key": "testkey"})
        assert r.status_code == 500
        assert real_db.get_task(task_id)["status"] == "staged"  # 되돌려짐 — 재시도 가능
    finally:
        real_db.delete_task(task_id)


def test_reset_requires_admin_key():
    assert client.post("/api/v1/admin/reset", headers={"X-Admin-Key": "wrong"}).status_code == 401


def test_reset_409_when_tasks_in_flight(monkeypatch):
    """처리 중 태스크가 있으면 거부하고, 파괴적 초기화는 시작조차 하지 않는다."""
    from app import ingest_api

    monkeypatch.setattr(ingest_api.db, "list_in_flight",
                        lambda: [{"task_id": "t", "source_id": "s", "status": "parsing"}])
    monkeypatch.setattr(ingest_api.db, "reset_all",
                        lambda: pytest.fail("in-flight인데 reset_all이 호출됨"))
    r = client.post("/api/v1/admin/reset", headers={"X-Admin-Key": "testkey"}, json={})
    assert r.status_code == 409
    assert "처리 중인 태스크가 1건" in r.json()["detail"]


class _FakeQdrant:
    def collection_exists(self, name):
        return True

    def delete_collection(self, name):
        self.deleted = name


def test_reset_force_bypasses_in_flight_guard(tmp_path, monkeypatch):
    """force=true면 멈춘 태스크가 있어도 진행한다 (영구 차단 방지)."""
    import embeddings
    from app import ingest_api

    called = {}
    monkeypatch.setattr(ingest_api.db, "list_in_flight",
                        lambda: pytest.fail("force면 in-flight 검사를 건너뛰어야 함"))
    monkeypatch.setattr(ingest_api.db, "reset_all", lambda: called.setdefault("db", True) or {})
    monkeypatch.setattr(ingest_api.wiki_ops, "reset_repo",
                        lambda root: called.setdefault("wiki", True))
    monkeypatch.setattr(ingest_api, "_wiki_root", lambda: tmp_path / "wiki")
    (tmp_path / "sources" / "s1").mkdir(parents=True)
    monkeypatch.setattr(ingest_api, "_sources_root", lambda: tmp_path / "sources")
    monkeypatch.setattr(embeddings, "qdrant", lambda: _FakeQdrant())
    monkeypatch.setattr(embeddings, "ensure_collection", lambda c: None)

    r = client.post("/api/v1/admin/reset", headers={"X-Admin-Key": "testkey"},
                    json={"force": True})
    assert r.status_code == 200 and r.json()["sources_removed"] == 1
    assert called == {"db": True, "wiki": True}
    assert not (tmp_path / "sources" / "s1").exists()   # 업로드 원본 삭제


def test_promote_metrics_from_needs_review(monkeypatch):
    """검토 대기 표를 metrics로 승격: melt→staging.metrics 적재 + 원행 mapped 처리."""
    from app import db
    sid, tid = str(_uuid.uuid4()), str(_uuid.uuid4())
    db.create_task(tid, sid)
    db.set_status(tid, "staged")
    payload = {"table_title": "연도별 예산", "columns": ["사업", "2024", "2025"],
               "rows": [["AI", "100", "150"]]}
    with db.connect() as conn:
        r = conn.execute(
            "INSERT INTO staging_tables (source_id, table_title, raw_data, mapping_confidence) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (sid, "연도별 예산", __import__("json").dumps(payload, ensure_ascii=False), 0.2),
        ).fetchone()
    staging_id = r["id"]
    try:
        res = client.post(f"/api/v1/ingest/{tid}/promote-metrics",
                          headers={"X-Admin-Key": "testkey"},
                          json={"staging_id": staging_id, "entity_col": "사업",
                                "metric_name": "예산", "unit": "백만원"})
        assert res.status_code == 200 and res.json() == {"promoted": 2}
        with db.connect() as conn:
            mrows = conn.execute("SELECT year, value FROM staging.metrics WHERE source_id=%s ORDER BY year",
                                 (sid,)).fetchall()
            st = conn.execute("SELECT status FROM staging_tables WHERE id=%s", (staging_id,)).fetchone()
        assert [(m["year"], float(m["value"])) for m in mrows] == [(2024, 100.0), (2025, 150.0)]
        assert st["status"] == "mapped"
        # 승격 후 검토 목록에서 사라진다
        assert db.list_staged(sid)["needs_review"] == []
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM staging.metrics WHERE source_id=%s", (sid,))
            conn.execute("DELETE FROM staging_tables WHERE source_id=%s", (sid,))
        db.delete_task(tid)


def test_promote_metrics_rejects_non_year_table(monkeypatch):
    """연도 컬럼 없는 표는 400 — 데이터 오적재 방지."""
    from app import db
    sid, tid = str(_uuid.uuid4()), str(_uuid.uuid4())
    db.create_task(tid, sid)
    db.set_status(tid, "staged")
    payload = {"table_title": "비교표", "columns": ["항목", "값"], "rows": [["A", "1"]]}
    with db.connect() as conn:
        r = conn.execute(
            "INSERT INTO staging_tables (source_id, table_title, raw_data, mapping_confidence) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (sid, "비교표", __import__("json").dumps(payload, ensure_ascii=False), 0.2),
        ).fetchone()
    try:
        res = client.post(f"/api/v1/ingest/{tid}/promote-metrics",
                          headers={"X-Admin-Key": "testkey"},
                          json={"staging_id": r["id"], "entity_col": "항목",
                                "metric_name": "값", "unit": ""})
        assert res.status_code == 400
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM staging_tables WHERE source_id=%s", (sid,))
        db.delete_task(tid)


def test_promote_metrics_flat_no_year(monkeypatch):
    """무연도 표: 값 컬럼+연도 지정 승격. 재승격 허용(status는 needs_review 유지)."""
    from app import db
    sid, tid = str(_uuid.uuid4()), str(_uuid.uuid4())
    db.create_task(tid, sid); db.set_status(tid, "staged")
    payload = {"columns": ["기술", "목표TRL"], "rows": [["AI", "8"], ["양자", "6"]]}
    with db.connect() as conn:
        r = conn.execute("INSERT INTO staging_tables (source_id, table_title, raw_data, "
                         "mapping_confidence) VALUES (%s,%s,%s,%s) RETURNING id",
                         (sid, "기술 목표", __import__("json").dumps(payload, ensure_ascii=False), 0.2)).fetchone()
    stid = r["id"]
    try:
        res = client.post(f"/api/v1/ingest/{tid}/promote-metrics", headers={"X-Admin-Key": "testkey"},
                          json={"staging_id": stid, "entity_col": "기술", "value_col": "목표TRL",
                                "metric_name": "목표", "unit": "", "year": 2026})
        assert res.status_code == 200 and res.json() == {"promoted": 2}
        with db.connect() as conn:
            m = conn.execute("SELECT entity, year, value FROM staging.metrics WHERE source_id=%s",
                             (sid,)).fetchall()
            st = conn.execute("SELECT status FROM staging_tables WHERE id=%s", (stid,)).fetchone()
        assert sorted((x["entity"], x["year"], float(x["value"])) for x in m) == \
            sorted([("AI", 2026, 8.0), ("양자", 2026, 6.0)])
        assert st["status"] == "needs_review"   # 재승격 허용 — mapped 처리 안 함
        # 같은 표 다른 시도: year 비움(NULL) — 재승격 가능
        res2 = client.post(f"/api/v1/ingest/{tid}/promote-metrics", headers={"X-Admin-Key": "testkey"},
                           json={"staging_id": stid, "entity_col": "기술", "value_col": "목표TRL",
                                 "metric_name": "목표2", "unit": "", "year": None})
        assert res2.status_code == 200
        with db.connect() as conn:
            n = conn.execute("SELECT count(*) c FROM staging.metrics WHERE source_id=%s AND year IS NULL",
                             (sid,)).fetchone()
        assert n["c"] == 2   # year=NULL 적재됨
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM staging.metrics WHERE source_id=%s", (sid,))
            conn.execute("DELETE FROM staging_tables WHERE source_id=%s", (sid,))
        db.delete_task(tid)


def test_promote_metrics_flat_rejects_nonnumeric_value(monkeypatch):
    """값 컬럼이 비숫자면 400 (오적재 방지)."""
    from app import db
    sid, tid = str(_uuid.uuid4()), str(_uuid.uuid4())
    db.create_task(tid, sid); db.set_status(tid, "staged")
    payload = {"columns": ["기술", "상태"], "rows": [["AI", "진행중"]]}
    with db.connect() as conn:
        r = conn.execute("INSERT INTO staging_tables (source_id, table_title, raw_data, "
                         "mapping_confidence) VALUES (%s,%s,%s,%s) RETURNING id",
                         (sid, "상태표", __import__("json").dumps(payload, ensure_ascii=False), 0.2)).fetchone()
    try:
        res = client.post(f"/api/v1/ingest/{tid}/promote-metrics", headers={"X-Admin-Key": "testkey"},
                          json={"staging_id": r["id"], "entity_col": "기술", "value_col": "상태",
                                "metric_name": "상태", "unit": "", "year": 2026})
        assert res.status_code == 400
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM staging_tables WHERE source_id=%s", (sid,))
        db.delete_task(tid)
