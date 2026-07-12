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


def _batch(*outs):
    """단일/복수 매핑 결과를 배치 응답 형태로 감싼다 (map_table은 표 묶음을 한 번에 매핑)."""
    return {"mappings": [{"index": i, **o} for i, o in enumerate(outs)]}


def _cleanup(source_id):
    with db.connect() as conn:
        conn.execute("DELETE FROM staging.technologies WHERE source_id = %s", (source_id,))
        conn.execute("DELETE FROM staging_tables WHERE source_id = %s", (source_id,))


def test_high_confidence_stages_rows(tmp_path, monkeypatch):
    source_id = str(uuid.uuid4())
    _write_table(tmp_path, {"table_title": "기술 목록", "columns": ["기술명", "분야"],
                            "rows": [["HBM", "반도체"], ["고체전지", "이차전지"]]})
    monkeypatch.setattr(mt.llm, "generate", lambda *a, **k: _batch({
        "table": "technologies", "confidence": 0.95,
        "column_mapping": [{"src": "기술명", "dst": "name"}, {"src": "분야", "dst": "field"}],
    }))
    try:
        out = mt.map_and_stage_tables(tmp_path, source_id)
        assert out == {"staged": [{"table": "technologies", "rows": 2}], "needs_review": 0, "inline_md": []}
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
    monkeypatch.setattr(mt.llm, "generate", lambda *a, **k: _batch({
        "table": "technologies", "confidence": 0.4, "column_mapping": []
    }))
    try:
        out = mt.map_and_stage_tables(tmp_path, source_id)
        assert out["staged"] == [] and out["needs_review"] == 1 and len(out["inline_md"]) == 1
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
    monkeypatch.setattr(mt.llm, "generate", lambda *a, **k: _batch({
        "table": "technologies", "confidence": 0.9,
        "column_mapping": [{"src": "기술명", "dst": "name"}, {"src": "분야", "dst": "field"},
                           {"src": "유령컬럼", "dst": "sub_field"}],
    }))
    try:
        out = mt.map_and_stage_tables(tmp_path, source_id)
        assert out == {"staged": [{"table": "technologies", "rows": 1}], "needs_review": 0, "inline_md": []}
    finally:
        _cleanup(source_id)


def test_coerce_strips_list_markers():
    assert mt._coerce("name", "◯ 38 5G 고도화(5G-Adv)") == "5G 고도화(5G-Adv)"
    # field는 12대 분야 정규 표기로 정규화 (공백 무시 매칭 → 깨진 내부 공백 복구)
    assert mt._coerce("field", "<10> 차세대 통신") == "차세대통신"
    assert mt._coerce("field", "반도체· 디스 플레이") == "반도체·디스플레이"
    assert mt.canon_field("우주항공 해양") == "우주항공·해양"  # ·대신 공백도 정규화
    # · 주변 잘못된 공백은 제거하되 ·는 보존 (name 등 일반 문자열)
    assert mt._coerce("name", "우주항공 ·해양") == "우주항공·해양"
    assert mt._coerce("name", "1. 양자컴퓨팅") == "양자컴퓨팅"
    assert mt._coerce("name", "(3) 첨단로봇") == "첨단로봇"
    assert mt._coerce("name", "5G-6G 통합") == "5G-6G 통합"  # 숫자 시작 정상값 보존
    assert mt._coerce("name", "⑩ 차세대 이차전지 소재·셀") == "차세대 이차전지 소재·셀"  # 원문자 번호
    assert mt._coerce("trl_level", "7") == 7  # INT 경로 불변


def test_canon_field_synonyms():
    # 세부주제 별칭 → 12분야 (대소문자 무관)
    assert mt.canon_field("6G") == "차세대통신"
    assert mt.canon_field("오픈랜") == "차세대통신"
    assert mt.canon_field("자율주행시스템") == "첨단모빌리티"
    assert mt.canon_field("UAM") == "첨단모빌리티"
    assert mt.canon_field("로봇") == "첨단로봇·제조"
    assert mt.canon_field("반도체") == "반도체·디스플레이"  # 단독 반도체 → 정규 분야
    assert mt.canon_field("HBM") == "반도체·디스플레이"
    assert mt.canon_field("양자컴퓨팅") == "양자"
    # 12분야 정규 표기는 그대로 통과
    assert mt.canon_field("첨단로봇·제조") == "첨단로봇·제조"
    # 애매·비별칭 태그는 원문 그대로 (오매핑 방지)
    assert mt.canon_field("연구데이터") == "연구데이터"
    assert mt.canon_field("국가전략기술") == "국가전략기술"


def test_budget_table_stages_rows(tmp_path, monkeypatch):
    source_id = str(uuid.uuid4())
    _write_table(tmp_path, {"table_title": "연도별 예산", "columns": ["연도", "예산(백만원)"],
                            "rows": [["2024", "30,000"], ["2025", "45,000"]]})
    monkeypatch.setattr(mt.llm, "generate", lambda *a, **k: _batch({
        "table": "budget_history", "confidence": 0.95,
        "column_mapping": [{"src": "연도", "dst": "fiscal_year"},
                           {"src": "예산(백만원)", "dst": "amount"}],
    }))
    try:
        out = mt.map_and_stage_tables(tmp_path, source_id)
        assert out == {"staged": [{"table": "budget_history", "rows": 2}], "needs_review": 0, "inline_md": []}
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT fiscal_year, amount FROM staging.budget_history "
                "WHERE source_id = %s ORDER BY fiscal_year", (source_id,)
            ).fetchall()
        assert [(r["fiscal_year"], r["amount"]) for r in rows] == [(2024, 30000), (2025, 45000)]
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM staging.budget_history WHERE source_id = %s", (source_id,))


def test_coerce_year_forms():
    assert mt._coerce("start_year", "'24") == 2024        # 2자리 약식
    assert mt._coerce("end_year", "'28") == 2028
    assert mt._coerce("start_year", "'24~'28") == 2024    # 기간 → 첫 연도
    assert mt._coerce("end_year", "'24~'28") == 2028      # 기간 → 끝 연도
    assert mt._coerce("start_year", "2024-2028") == 2024
    assert mt._coerce("start_year", "2024") == 2024
    assert mt._coerce("end_year", "미정") is None
    assert mt._coerce("budget_total", "30,000") == 30000  # 예산 정수 경로 불변


def _write_tables(parsed_dir, payloads):
    (parsed_dir / "tables").mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(payloads):
        (parsed_dir / "tables" / f"table_{i:03d}.json").write_text(
            json.dumps(p, ensure_ascii=False), encoding="utf-8"
        )


def test_tables_are_mapped_in_one_batched_call(tmp_path, monkeypatch):
    """표 여러 개를 표당 1회가 아니라 배치 1회로 매핑한다 (LLM 호출 수 절감)."""
    source_id = str(uuid.uuid4())
    _write_tables(tmp_path, [
        {"table_title": f"기술 목록 {i}", "columns": ["기술명", "분야"],
         "rows": [[f"기술{i}", "반도체"]]}
        for i in range(3)
    ])
    calls = []

    def fake(purpose, contents, schema=None):
        calls.append(purpose)
        return {"mappings": [
            {"index": i, "table": "technologies", "confidence": 0.95,
             "column_mapping": [{"src": "기술명", "dst": "name"}, {"src": "분야", "dst": "field"}]}
            for i in range(3)
        ]}

    monkeypatch.setattr(mt.llm, "generate", fake)
    try:
        out = mt.map_and_stage_tables(tmp_path, source_id)
        assert len(calls) == 1, f"표 3개에 LLM 호출 {len(calls)}회 — 배치 안 됨"
        assert out["needs_review"] == 0
        assert sum(s["rows"] for s in out["staged"]) == 3
    finally:
        _cleanup(source_id)


def test_table_missing_from_batch_response_goes_to_review(tmp_path, monkeypatch):
    """배치 응답에서 빠진 표만 needs_review로 가고, 나머지는 정상 적재된다."""
    source_id = str(uuid.uuid4())
    _write_tables(tmp_path, [
        {"table_title": "기술 목록", "columns": ["기술명", "분야"], "rows": [["HBM", "반도체"]]},
        {"table_title": "누락될 표", "columns": ["가"], "rows": [["1"]]},
    ])
    monkeypatch.setattr(mt.llm, "generate", lambda *a, **k: {"mappings": [
        {"index": 0, "table": "technologies", "confidence": 0.95,
         "column_mapping": [{"src": "기술명", "dst": "name"}, {"src": "분야", "dst": "field"}]},
    ]})   # index 1 없음
    try:
        out = mt.map_and_stage_tables(tmp_path, source_id)
        assert out["staged"] == [{"table": "technologies", "rows": 1}]
        assert out["needs_review"] == 1
    finally:
        _cleanup(source_id)


def test_batch_llm_failure_sends_all_tables_to_review(tmp_path, monkeypatch):
    """배치 호출 자체가 실패해도 인제스트가 죽지 않고 표 전부 needs_review로 남는다."""
    source_id = str(uuid.uuid4())
    _write_tables(tmp_path, [{"table_title": f"표{i}", "columns": ["가"], "rows": [["1"]]}
                             for i in range(2)])
    monkeypatch.setattr(mt.llm, "generate",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gemini down")))
    try:
        out = mt.map_and_stage_tables(tmp_path, source_id)
        assert out["staged"] == [] and out["needs_review"] == 2 and len(out["inline_md"]) == 2
    finally:
        _cleanup(source_id)


# --- 티어 2 (metrics) melt 경로 ---

def test_melt_metrics_wide_table():
    """와이드 표(대상 + 연도 컬럼)를 (entity, metric, year, value, unit) 롱포맷으로 melt."""
    payload = {"table_title": "연도별 예산", "columns": ["사업명", "2024", "2025", "'26"],
               "rows": [["AI반도체", "100", "150", "200"], ["양자", "50", "", "80"]]}
    out = {"table": "metrics", "confidence": 0.9, "entity_col": "사업명",
           "metric_name": "예산", "unit": "백만원"}
    rows = mt._melt_metrics(payload, out)
    # AI반도체 3개(2024/25/26) + 양자 2개(2025 빈칸 제외) = 5
    assert rows == [
        ("AI반도체", "예산", 2024, 100.0, "백만원"),
        ("AI반도체", "예산", 2025, 150.0, "백만원"),
        ("AI반도체", "예산", 2026, 200.0, "백만원"),
        ("양자", "예산", 2024, 50.0, "백만원"),
        ("양자", "예산", 2026, 80.0, "백만원"),
    ]


def test_melt_metrics_rejects_when_no_year_columns():
    """연도 컬럼이 없으면(이미 롱포맷이거나 비시계열) [] → 호출부가 검토 대기로."""
    payload = {"columns": ["사업명", "금액"], "rows": [["AI", "100"]]}
    out = {"table": "metrics", "confidence": 0.9, "entity_col": "사업명",
           "metric_name": "예산", "unit": "백만원"}
    assert mt._melt_metrics(payload, out) == []


def test_canon_metric_and_num():
    assert mt.canon_metric("예 산") == "예산"          # 공백 무시 정규화
    assert mt.canon_metric("R&D 투자") == "R&D 투자"    # 어휘 밖은 원문 유지(clean)
    assert mt._num("1,234") == 1234.0
    assert mt._num("12.5%") == 12.5
    assert mt._num("미정") is None


def test_metrics_stages_rows(tmp_path, monkeypatch):
    """table=metrics 매핑이 staging.metrics에 롱포맷으로 적재되는지 (DB 통합)."""
    source_id = str(uuid.uuid4())
    _write_table(tmp_path, {"table_title": "연도별 인력", "columns": ["분야", "2024", "2025"],
                            "rows": [["반도체", "10", "20"]]})
    monkeypatch.setattr(mt.llm, "generate", lambda *a, **k: _batch({
        "table": "metrics", "confidence": 0.95, "entity_col": "분야",
        "metric_name": "인력", "unit": "명",
    }))
    try:
        out = mt.map_and_stage_tables(tmp_path, source_id)
        assert out == {"staged": [{"table": "metrics", "rows": 2}], "needs_review": 0, "inline_md": []}
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT entity, metric_name, year, value, unit FROM staging.metrics "
                "WHERE source_id = %s ORDER BY year", (source_id,),
            ).fetchall()
        assert [(r["year"], float(r["value"])) for r in rows] == [(2024, 10.0), (2025, 20.0)]
        assert rows[0]["metric_name"] == "인력" and rows[0]["unit"] == "명"
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM staging.metrics WHERE source_id = %s", (source_id,))


# --- 티어 3 (매핑 실패 표 → md 인라인) ---

def test_table_to_md():
    md = mt._table_to_md({"table_title": "추진 일정", "columns": ["단계", "시기"],
                          "rows": [["1단계", "2024"], ["2단계|병행", "2025"]]})
    assert md.splitlines() == [
        "**추진 일정**", "",
        "| 단계 | 시기 |", "| --- | --- |",
        "| 1단계 | 2024 |", "| 2단계\\|병행 | 2025 |",  # 파이프 이스케이프
    ]
    assert mt._table_to_md({"columns": [], "rows": []}) == ""  # 헤더 없으면 빈 문자열


def test_unmapped_table_collected_for_inline(tmp_path, monkeypatch):
    """매핑 실패 표는 staging_tables 보존 + inline_md에 마크다운으로 수집된다 (티어 3)."""
    source_id = str(uuid.uuid4())
    _write_table(tmp_path, {"table_title": "비교 매트릭스", "columns": ["항목", "값"],
                            "rows": [["A", "1"]]})
    monkeypatch.setattr(mt.llm, "generate", lambda *a, **k: _batch({
        "table": "none", "confidence": 0.2, "column_mapping": []}))
    try:
        out = mt.map_and_stage_tables(tmp_path, source_id)
        assert out["needs_review"] == 1
        assert len(out["inline_md"]) == 1
        assert "비교 매트릭스" in out["inline_md"][0] and "| 항목 | 값 |" in out["inline_md"][0]
    finally:
        _cleanup(source_id)
