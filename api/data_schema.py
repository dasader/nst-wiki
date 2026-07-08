"""데이터 탐색기가 노출하는 테이블·컬럼 화이트리스트 (단일 출처).

read_api(조회 API)와 narrative(위키 [[data:...]] 참조 검증)가 공유한다 —
pipeline이 웹 계층을 임포트하지 않도록 여기로 뺐다. frontend/app/labels.js가 이를 미러링한다.
"""

DATA_TABLES = {
    "technologies": ["id", "name", "field", "sub_field", "lead_ministry", "trl_level",
                     "description", "source_id", "created_at", "updated_at"],
    "projects": ["id", "project_code", "name", "lead_ministry", "budget_total",
                 "budget_annual", "start_year", "end_year", "status", "source_id"],
    "policy_events": ["id", "event_date", "event_type", "title", "description",
                      "affected_fields", "source_id"],
    "ministries": ["id", "name", "abbreviation", "source_id"],
    "budget_history": ["id", "project_id", "fiscal_year", "amount", "source_id"],
    "tech_project_mapping": ["technology_id", "project_id", "relevance_score", "mapping_source"],
}

# 인제스트가 실제로 채우는 테이블만 위키 [[data:...]] 참조 대상으로 광고한다.
# tech_project_mapping은 적재 경로가 없어(db.STAGED_TABLES에도 없다) 항상 비어 있다 —
# 링크를 걸면 검증은 통과하고 빈 테이블로 안내한다. 사용자에겐 깨진 링크와 구분되지 않는다.
# 적재 경로가 생기면 이 집합에서 빼면 된다.
UNPOPULATED = {"tech_project_mapping"}
LINKABLE_TABLES = {t: c for t, c in DATA_TABLES.items() if t not in UNPOPULATED}
