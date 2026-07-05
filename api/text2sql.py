"""자연어 → SQL. 이중 방어: 코드 검증(단일 SELECT) + wiki_ro 읽기 전용 롤."""
import os
import re

import psycopg
from psycopg.rows import dict_row

import llm

SCHEMA_DESC = """
- technologies(id, name, field, sub_field, lead_ministry, trl_level, description): NEXT 기술
- projects(id, project_code, name, lead_ministry, budget_total, budget_annual, start_year, end_year, status): R&D 사업 (예산 단위: 백만원)
- tech_project_mapping(technology_id, project_id, relevance_score): 기술-사업 매핑
- budget_history(id, project_id, fiscal_year, amount): 연도별 예산
- policy_events(id, event_date, event_type, title, description, affected_fields): 정책 이벤트
- ministries(id, name, abbreviation): 부처
주의: technologies.lead_ministry, projects.lead_ministry는 부처명이 그대로 담긴 텍스트다
(ministries.id로 조인하는 FK 아님). 부처로 거를 땐 lead_ministry LIKE '%부처명%'로 비교하라.
lead_ministry에는 정부 공식 '약칭'이 담긴다. 질문이 정식 명칭이면 아래 약칭으로 바꿔 비교하라:
{ministry_abbr}
"""

SQL_SCHEMA = {
    "type": "object",
    "properties": {"sql": {"type": "string"}},
    "required": ["sql"],
}

PROMPT = """다음 PostgreSQL 스키마에서 질문에 답하는 SELECT 문 하나를 작성하라.
{schema}
규칙: SELECT 단일 문장만. 세미콜론·주석 금지. 한국어 값은 그대로 비교. 집계 질문이면 집계 함수 사용.

질문: {question}"""


_abbr_cache = None


def _abbr_hint() -> str:
    """ministries(006 seed)에서 정식명↔약칭이 다른 기관만 읽어 힌트 문자열로. 캐시.
    미seed면 빈 문자열 — 힌트 없이 진행(graceful)."""
    global _abbr_cache
    if _abbr_cache is None:
        try:
            with psycopg.connect(os.environ["READONLY_DATABASE_URL"],
                                 options="-c statement_timeout=5000") as conn:
                rows = conn.execute(
                    "SELECT name, abbreviation FROM ministries "
                    "WHERE abbreviation IS NOT NULL AND name <> abbreviation ORDER BY id"
                ).fetchall()
            _abbr_cache = ", ".join(f"{n}→{a}" for n, a in rows)
        except Exception:
            _abbr_cache = ""
    return _abbr_cache


def validate_sql(sql: str) -> str:
    s = sql.strip()
    if ";" in s or "--" in s or "/*" in s:
        raise ValueError("금지된 토큰 (세미콜론/주석)")
    if not re.match(r"(?is)^\s*SELECT\b", s):
        raise ValueError("SELECT 문만 허용")
    if re.search(r"(?i)\b(insert|update|delete|drop|alter|create|grant|truncate|copy|into)\b", s):
        raise ValueError("쓰기 키워드 금지")
    if not re.search(r"(?i)\bLIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$", s.rstrip()):
        s = f"{s} LIMIT 100"
    return s


def run_data_query(question: str) -> dict:
    schema = SCHEMA_DESC.format(ministry_abbr=_abbr_hint())
    out = llm.generate("text2sql", PROMPT.format(schema=schema, question=question),
                       schema=SQL_SCHEMA)
    raw = out["sql"]
    try:
        sql = validate_sql(raw)
        with psycopg.connect(os.environ["READONLY_DATABASE_URL"], row_factory=dict_row,
                             options="-c statement_timeout=5000") as conn:
            rows = conn.execute(sql).fetchall()
        return {"sql": sql, "rows": rows, "error": None}
    except Exception as e:  # ponytail: LLM 생성 SQL은 실패가 일상 — 오류를 답변 합성에 넘긴다
        return {"sql": raw, "rows": [], "error": str(e)}


if __name__ == "__main__":  # ponytail 셀프체크: seed 약칭이 스키마에 채워지는지 (DB 있으면)
    filled = SCHEMA_DESC.format(ministry_abbr=_abbr_hint())
    assert "{ministry_abbr}" not in filled
    print("ok, 약칭 항목 수:", _abbr_hint().count("→"))
