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


def test_find_ingested_by_hash():
    fh = "sha256:" + uuid.uuid4().hex
    approved, failed = str(uuid.uuid4()), str(uuid.uuid4())
    db.create_task(approved, str(uuid.uuid4()), fh)
    db.create_task(failed, str(uuid.uuid4()), fh)
    try:
        db.set_status(approved, "approved")
        db.set_status(failed, "failed")
        # 거부·실패가 아닌 태스크만 중복으로 잡힌다 (실패본은 재업로드 허용)
        hit = db.find_ingested_by_hash(fh)
        assert hit is not None and hit["task_id"] == approved
        assert db.find_ingested_by_hash("sha256:" + uuid.uuid4().hex) is None
        db.set_status(approved, "rejected")
        assert db.find_ingested_by_hash(fh) is None  # 전부 거부/실패면 재업로드 가능
    finally:
        db.delete_task(approved)
        db.delete_task(failed)


def _stage_tech(source_id, name, field="반도체"):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO staging.technologies (name, field, source_id) VALUES (%s, %s, %s)",
            (name, field, source_id),
        )


def test_upsert_staged_inserts_and_updates():
    source_id = str(uuid.uuid4())
    _stage_tech(source_id, "업서트기술", field="반도체")
    try:
        counts = db.upsert_staged(source_id)
        assert counts["technologies"] == 1
        assert db.list_staged(source_id)["technologies"] == []  # staging 비워짐
        # 같은 name으로 재승인 → 갱신 (중복 행 없음)
        source_id2 = str(uuid.uuid4())
        _stage_tech(source_id2, "업서트기술", field="이차전지")
        db.upsert_staged(source_id2)
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT field FROM technologies WHERE name = %s", ("업서트기술",)
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["field"] == "이차전지"
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM technologies WHERE name = %s", ("업서트기술",))
            conn.execute("DELETE FROM staging.technologies WHERE source_id IN (%s, %s)",
                         (source_id, source_id2))


def test_upsert_staged_ministries_carries_source_id():
    source_id = str(uuid.uuid4())
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO staging.ministries (name, abbreviation, source_id) VALUES (%s, %s, %s)",
            ("업서트부처", "업부", source_id),
        )
    try:
        counts = db.upsert_staged(source_id)
        assert counts["ministries"] == 1
        with db.connect() as conn:
            row = conn.execute(
                "SELECT abbreviation, source_id FROM ministries WHERE name = %s", ("업서트부처",)
            ).fetchone()
        assert row["abbreviation"] == "업부"
        assert row["source_id"] == source_id
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM ministries WHERE name = %s", ("업서트부처",))
            conn.execute("DELETE FROM staging.ministries WHERE source_id = %s", (source_id,))


def test_upsert_staged_dedupes_batch_duplicates():
    source_id = str(uuid.uuid4())
    _stage_tech(source_id, "중복기술", field="반도체")
    _stage_tech(source_id, "중복기술", field="이차전지")  # 같은 배치에 동일 name 2행
    try:
        counts = db.upsert_staged(source_id)  # 자기충돌 없이 성공해야 함
        assert counts["technologies"] == 1
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT field FROM technologies WHERE name = %s", ("중복기술",)
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["field"] == "이차전지"  # id 큰(나중) 행 우선
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM technologies WHERE name = %s", ("중복기술",))
            conn.execute("DELETE FROM staging.technologies WHERE source_id = %s", (source_id,))


def test_discard_staged_clears():
    source_id = str(uuid.uuid4())
    _stage_tech(source_id, "폐기기술")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO staging_tables (source_id, table_title, raw_data) VALUES (%s, %s, %s)",
            (source_id, "표", '{"columns": [], "rows": []}'),
        )
    try:
        db.discard_staged(source_id)
        assert db.list_staged(source_id)["technologies"] == []
        with db.connect() as conn:
            row = conn.execute(
                "SELECT status FROM staging_tables WHERE source_id = %s", (source_id,)
            ).fetchone()
        assert row["status"] == "discarded"
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM staging_tables WHERE source_id = %s", (source_id,))


def test_list_tasks_recent_first():
    t1, t2 = str(uuid.uuid4()), str(uuid.uuid4())
    db.create_task(t1, str(uuid.uuid4()))
    db.create_task(t2, str(uuid.uuid4()))
    try:
        tasks = db.list_tasks(limit=10)
        ids = [t["task_id"] for t in tasks]
        assert ids.index(t2) < ids.index(t1)  # 최신 먼저
    finally:
        db.delete_task(t1)
        db.delete_task(t2)
