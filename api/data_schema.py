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
