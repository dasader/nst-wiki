import json
import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")

from app import db
import pipeline.map_tables as mt


def _write_table(parsed_dir, payload):
    (parsed_dir / "tables").mkdir(parents=True, exist_ok=True)
    (parsed_dir / "tables" / "table_001.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _cleanup(source_id):
    with db.connect() as conn:
        conn.execute("DELETE FROM staging.technologies WHERE source_id = %s", (source_id,))
        conn.execute("DELETE FROM staging_tables WHERE source_id = %s", (source_id,))


def test_high_confidence_stages_rows(tmp_path, monkeypatch):
    source_id = str(uuid.uuid4())
    _write_table(tmp_path, {"table_title": "기술 목록", "columns": ["기술명", "분야"],
                            "rows": [["HBM", "반도체"], ["고체전지", "이차전지"]]})
    monkeypatch.setattr(mt.llm, "generate", lambda *a, **k: {
        "table": "technologies", "confidence": 0.95,
        "column_mapping": {"기술명": "name", "분야": "field"},
    })
    try:
        out = mt.map_and_stage_tables(tmp_path, source_id)
        assert out == {"staged": [{"table": "technologies", "rows": 2}], "needs_review": 0}
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT name, field FROM staging.technologies WHERE source_id = %s ORDER BY name",
                (source_id,),
            ).fetchall()
        assert sorted([r["name"] for r in rows]) == ["HBM", "고체전지"]
    finally:
        _cleanup(source_id)


def test_low_confidence_falls_back(tmp_path, monkeypatch):
    source_id = str(uuid.uuid4())
    _write_table(tmp_path, {"table_title": "알 수 없는 표", "columns": ["가", "나"], "rows": [["1", "2"]]})
    monkeypatch.setattr(mt.llm, "generate", lambda *a, **k: {
        "table": "technologies", "confidence": 0.4, "column_mapping": {}
    })
    try:
        out = mt.map_and_stage_tables(tmp_path, source_id)
        assert out == {"staged": [], "needs_review": 1}
        with db.connect() as conn:
            row = conn.execute(
                "SELECT status, mapping_confidence FROM staging_tables WHERE source_id = %s",
                (source_id,),
            ).fetchone()
        assert row["status"] == "needs_review"
        assert abs(row["mapping_confidence"] - 0.4) < 1e-6
    finally:
        _cleanup(source_id)


def test_mismatched_mapping_and_short_rows_do_not_crash(tmp_path, monkeypatch):
    source_id = str(uuid.uuid4())
    # 주의: staging.technologies.field는 NOT NULL — name+field는 매핑하되,
    # 표에 없는 원본 컬럼(유령컬럼)과 빈 행이 크래시 없이 처리되는지 검증한다.
    _write_table(tmp_path, {"table_title": "기술 목록", "columns": ["기술명", "분야"],
                            "rows": [["HBM", "반도체"], []]})
    monkeypatch.setattr(mt.llm, "generate", lambda *a, **k: {
        "table": "technologies", "confidence": 0.9,
        "column_mapping": {"기술명": "name", "분야": "field", "유령컬럼": "sub_field"},
    })
    try:
        out = mt.map_and_stage_tables(tmp_path, source_id)
        assert out == {"staged": [{"table": "technologies", "rows": 1}], "needs_review": 0}
    finally:
        _cleanup(source_id)
