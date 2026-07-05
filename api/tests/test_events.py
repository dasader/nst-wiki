import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")

from app import db
import pipeline.events as events


def _cleanup(source_id):
    with db.connect() as conn:
        conn.execute("DELETE FROM staging.policy_events WHERE source_id = %s", (source_id,))


def test_extract_stages_dated_events(monkeypatch):
    source_id = str(uuid.uuid4())
    monkeypatch.setattr(events.llm, "generate", lambda *a, **k: {"events": [
        {"event_date": "2024-02", "event_type": "고시", "title": "국가전략기술 확정 고시",
         "description": "12대 분야 확정", "affected_fields": ["반도체·디스플레이"]},
        {"event_date": "2022", "event_type": "선정", "title": "국가전략기술 선정",
         "description": "", "affected_fields": []},
        {"event_date": "미상", "event_type": "기타", "title": "날짜없음 → 제외"},  # 드롭
        {"event_date": "2024-03-01", "event_type": "발표", "title": ""},  # 제목없음 → 드롭
    ]})
    try:
        out = events.extract_and_stage_events(["서사 본문"], source_id)
        assert out == {"staged": 2}  # 날짜·제목 있는 2건만
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT event_date::text, title, affected_fields FROM staging.policy_events "
                "WHERE source_id = %s ORDER BY event_date", (source_id,)
            ).fetchall()
        assert rows[0]["event_date"] == "2022-01-01"  # 연도만 → 01-01
        assert rows[1]["event_date"] == "2024-02-01"  # 연월 → 01일
        assert rows[1]["affected_fields"] == ["반도체·디스플레이"]
    finally:
        _cleanup(source_id)


def test_coerce_date():
    assert events._coerce_date("2024-02-15") == "2024-02-15"
    assert events._coerce_date("2024-02") == "2024-02-01"   # 연월 → 01일
    assert events._coerce_date("2022") == "2022-01-01"       # 연도 → 01-01
    assert events._coerce_date("2024-02-30") == "2024-02-01"  # 잘못된 일 → 월-01
    assert events._coerce_date("미상") is None


def test_empty_narrative_no_call(monkeypatch):
    called = []
    monkeypatch.setattr(events.llm, "generate", lambda *a, **k: called.append(1) or {"events": []})
    assert events.extract_and_stage_events([], "s") == {"staged": 0}
    assert called == []  # 서사 없으면 LLM 호출 안 함
