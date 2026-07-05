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
        "column_mapping": [{"src": "기술명", "dst": "name"}, {"src": "분야", "dst": "field"}],
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
        "table": "technologies", "confidence": 0.4, "column_mapping": []
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
        "column_mapping": [{"src": "기술명", "dst": "name"}, {"src": "분야", "dst": "field"},
                           {"src": "유령컬럼", "dst": "sub_field"}],
    })
    try:
        out = mt.map_and_stage_tables(tmp_path, source_id)
        assert out == {"staged": [{"table": "technologies", "rows": 1}], "needs_review": 0}
    finally:
        _cleanup(source_id)


def test_coerce_strips_list_markers():
    assert mt._coerce("name", "◯ 38 5G 고도화(5G-Adv)") == "5G 고도화(5G-Adv)"
    # field는 12대 분야 정규 표기로 정규화 (공백 무시 매칭 → 깨진 내부 공백 복구)
    assert mt._coerce("field", "<10> 차세대 통신") == "차세대통신"
    assert mt._coerce("field", "반도체· 디스 플레이") == "반도체·디스플레이"
    # · 주변 잘못된 공백은 제거하되 ·는 보존 (name 등 일반 문자열)
    assert mt._coerce("name", "우주항공 ·해양") == "우주항공·해양"
    assert mt._coerce("name", "1. 양자컴퓨팅") == "양자컴퓨팅"
    assert mt._coerce("name", "(3) 첨단로봇") == "첨단로봇"
    assert mt._coerce("name", "5G-6G 통합") == "5G-6G 통합"  # 숫자 시작 정상값 보존
    assert mt._coerce("name", "⑩ 차세대 이차전지 소재·셀") == "차세대 이차전지 소재·셀"  # 원문자 번호
    assert mt._coerce("trl_level", "7") == 7  # INT 경로 불변


def test_coerce_year_forms():
    assert mt._coerce("start_year", "'24") == 2024        # 2자리 약식
    assert mt._coerce("end_year", "'28") == 2028
    assert mt._coerce("start_year", "'24~'28") == 2024    # 기간 → 첫 연도
    assert mt._coerce("end_year", "'24~'28") == 2028      # 기간 → 끝 연도
    assert mt._coerce("start_year", "2024-2028") == 2024
    assert mt._coerce("start_year", "2024") == 2024
    assert mt._coerce("end_year", "미정") is None
    assert mt._coerce("budget_total", "30,000") == 30000  # 예산 정수 경로 불변
