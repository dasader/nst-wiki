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


def validate_sql(sql: str) -> str:
    s = sql.strip()
    if ";" in s or "--" in s or "/*" in s:
        raise ValueError("금지된 토큰 (세미콜론/주석)")
    if not re.match(r"(?is)^\s*SELECT\b", s):
        raise ValueError("SELECT 문만 허용")
    if re.search(r"(?i)\b(insert|update|delete|drop|alter|create|grant|truncate|copy)\b", s):
        raise ValueError("쓰기 키워드 금지")
    if not re.search(r"(?i)\bLIMIT\s+\d+", s):
        s = f"{s} LIMIT 100"
    return s


def run_data_query(question: str) -> dict:
    out = llm.generate("text2sql", PROMPT.format(schema=SCHEMA_DESC, question=question),
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
