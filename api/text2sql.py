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

# 정부조직 약칭과 영어 명칭에 관한 규칙 [별표] — 정식명↔약칭이 다른 기관만 (같은 건 변환 불필요)
MINISTRY_ABBR = {
    "재정경제부": "재경부", "과학기술정보통신부": "과기정통부", "행정안전부": "행안부",
    "국가보훈부": "보훈부", "문화체육관광부": "문체부", "농림축산식품부": "농식품부",
    "산업통상부": "산업부", "보건복지부": "복지부", "기후에너지환경부": "기후부",
    "고용노동부": "노동부", "성평등가족부": "성평등부", "국토교통부": "국토부",
    "해양수산부": "해수부", "중소벤처기업부": "중기부", "기획예산처": "기획처",
    "인사혁신처": "인사처", "식품의약품안전처": "식약처", "국가데이터처": "데이터처",
    "지식재산처": "지재처", "우주항공청": "우주청", "재외동포청": "동포청",
    "방위사업청": "방사청", "농촌진흥청": "농진청", "질병관리청": "질병청",
    "행정중심복합도시건설청": "행복청", "새만금개발청": "새만금청", "해양경찰청": "해경청",
    "방송미디어통신위원회": "방미통위", "공정거래위원회": "공정위",
    "국민권익위원회": "국민권익위", "개인정보보호위원회": "개인정보위",
    "원자력안전위원회": "원안위",
}
_ABBR_HINT = ", ".join(f"{k}→{v}" for k, v in MINISTRY_ABBR.items())
SCHEMA_DESC = SCHEMA_DESC.format(ministry_abbr=_ABBR_HINT)  # 스키마 설명에 약칭 표 주입

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
    if re.search(r"(?i)\b(insert|update|delete|drop|alter|create|grant|truncate|copy|into)\b", s):
        raise ValueError("쓰기 키워드 금지")
    if not re.search(r"(?i)\bLIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$", s.rstrip()):
        s = f"{s} LIMIT 100"
    return s


if __name__ == "__main__":  # ponytail 셀프체크: 약칭 표가 스키마 설명에 주입됐는지
    assert "과학기술정보통신부→과기정통부" in SCHEMA_DESC
    assert "{ministry_abbr}" not in SCHEMA_DESC  # 플레이스홀더가 실제로 치환됨
    print("ok")


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
