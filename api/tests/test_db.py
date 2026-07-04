import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")

from app import db


def test_task_roundtrip():
    task_id, source_id = str(uuid.uuid4()), str(uuid.uuid4())
    db.create_task(task_id, source_id)
    try:
        t = db.get_task(task_id)
        assert t["status"] == "queued"
        assert t["source_id"] == source_id
        db.set_status(task_id, "failed", error="boom")
        t = db.get_task(task_id)
        assert t["status"] == "failed"
        assert t["error"] == "boom"
    finally:
        db.delete_task(task_id)
    assert db.get_task(task_id) is None
