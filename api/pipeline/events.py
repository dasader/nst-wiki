"""서사 → 정책 이벤트 추출. 날짜가 있는 정책 사건을 staging.policy_events로 적재.
표 매핑이 못 잡는 '본문 속 시점 정보'를 정형 타임라인으로 만든다. 승인은 기존 대시보드."""
import re
from datetime import date

import llm
from app import db
from pipeline.map_tables import canon_field

EVENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event_date": {"type": "string"},
                    "event_type": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "affected_fields": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["event_date", "event_type", "title"],
            },
        }
    },
    "required": ["events"],
}

PROMPT = """한국 정책문서 서사에서 '날짜가 특정되는 정책 이벤트'만 추출하라.
이벤트 = 특정 시점에 일어난 정책 사건(기술 선정, 고시 제정·개정, 계획 수립·발표, 법령 시행, 정상회의 등).

- event_date: YYYY-MM-DD. 월·일을 모르면 01로. (예: '24.2월→2024-02-01, '22년→2022-01-01)
- event_type: 선정/고시/계획수립/개정/발표/시행/기타 중 간결히
- title: 이벤트 제목 한 줄
- description: 1~2문장 맥락
- affected_fields: 관련 국가전략기술 분야 목록(없으면 빈 배열)

날짜를 특정할 수 없는 일반 서술은 제외하라. 서사에 명시된 시점만.

서사:
{narrative}"""

_DATE_RE = re.compile(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?")


def _coerce_date(s: str) -> str | None:
    """YYYY / YYYY-MM / YYYY-MM-DD를 유효한 ISO 날짜로. 없는 월·일은 01, 범위 오류도 01 보정."""
    m = _DATE_RE.search(str(s))
    if not m:
        return None
    y = int(m[1])
    mo = int(m[2]) if m[2] else 1
    d = int(m[3]) if m[3] else 1
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        try:
            return date(y, mo if 1 <= mo <= 12 else 1, 1).isoformat()
        except ValueError:
            return None


def extract_and_stage_events(narrative_texts: list[str], source_id: str) -> dict:
    if not narrative_texts:
        return {"staged": 0}
    out = llm.generate(
        "extract_events",
        PROMPT.format(narrative="\n\n".join(narrative_texts)[:20000]),
        schema=EVENTS_SCHEMA,
    )
    rows = []
    for ev in out.get("events", []):
        d = _coerce_date(ev.get("event_date", ""))
        title = (ev.get("title") or "").strip()
        if not d or not title:
            continue
        fields = [canon_field(f) for f in ev.get("affected_fields") or []]  # 12분야 정규 표기
        rows.append((d, (ev.get("event_type") or "").strip(), title,
                     (ev.get("description") or "").strip(), fields or None, source_id))
    if rows:
        with db.connect() as conn:
            conn.cursor().executemany(
                "INSERT INTO staging.policy_events (event_date, event_type, title, "
                "description, affected_fields, source_id) VALUES (%s, %s, %s, %s, %s, %s)",
                rows,
            )
    return {"staged": len(rows)}
