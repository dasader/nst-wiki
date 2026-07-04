import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")

from app import db
from tasks import run_ingest


def test_run_ingest_md_reaches_staged(tmp_path, monkeypatch):
    import tasks as tasks_mod
    source_id = str(uuid.uuid4())
    src_dir = tmp_path / source_id
    src_dir.mkdir()
    (src_dir / "original.md").write_text("# 제목\n본문", encoding="utf-8")
    (src_dir / "metadata.json").write_text('{"title": "t"}', encoding="utf-8")
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path))
    import pipeline.compile as compile_mod
    monkeypatch.setattr(compile_mod, "compile_source", lambda *a, **k: {
        "branch": None, "affected_pages": [], "affected_tables": {}, "contradictions": []})
    task_id = str(uuid.uuid4())
    db.create_task(task_id, source_id)
    try:
        tasks_mod.run_ingest(task_id)
        assert db.get_task(task_id)["status"] == "staged"
    finally:
        db.delete_task(task_id)


def test_run_ingest_failure_records_error(tmp_path, monkeypatch):
    source_id = str(uuid.uuid4())
    (tmp_path / source_id).mkdir()
    (tmp_path / source_id / "original.hwp").write_bytes(b"x")
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path))
    task_id = str(uuid.uuid4())
    db.create_task(task_id, source_id)
    try:
        try:
            run_ingest(task_id)
        except ValueError:
            pass
        t = db.get_task(task_id)
        assert t["status"] == "failed"
        assert "unsupported" in t["error"]
    finally:
        db.delete_task(task_id)
