// 영문 스키마·경로 코드를 한글 라벨로 옮기는 도메인 사전.
// DB 스키마(db/init/001_schema.sql)와 위키 디렉토리(scripts/init_wiki.py) 기준.

export const TABLE_LABELS = {
  technologies: "기술",
  projects: "사업",
  policy_events: "정책 변화",
  ministries: "부처·기관",
  budget_history: "예산 이력",
  tech_project_mapping: "기술–사업 연계",
};

// 테이블별 컬럼 라벨. 없으면 SHARED_COLS, 그것도 없으면 prettify() 폴백.
const SHARED_COLS = {
  id: "ID",
  name: "이름",
  lead_ministry: "주관부처",
  source_id: "출처",
  created_at: "생성일",
  updated_at: "수정일",
  wiki_page_path: "위키 페이지",
  project_id: "사업 ID",
  technology_id: "기술 ID",
};

const COLS = {
  technologies: { name: "기술명", field: "분야", sub_field: "세부분야", trl_level: "TRL", description: "설명" },
  projects: {
    project_code: "사업코드", name: "사업명", budget_total: "총사업비", budget_annual: "연간예산",
    start_year: "시작연도", end_year: "종료연도", status: "상태",
  },
  tech_project_mapping: { relevance_score: "연관도", mapping_source: "매핑 출처" },
  budget_history: { fiscal_year: "회계연도", amount: "금액" },
  policy_events: {
    event_date: "일자", event_type: "유형", title: "제목", description: "설명", affected_fields: "영향 분야",
  },
  ministries: { name: "기관명", abbreviation: "약칭" },
};

// 위키 디렉토리 → 한글 그룹명 + 부제
export const WIKI_DIRS = {
  tech: { label: "기술 개념", hint: "기술별 개념 페이지" },
  entity: { label: "정책 엔티티", hint: "부처·기관 페이지" },
  events: { label: "정책변화 이력", hint: "시점별 정책 변화" },
  synthesis: { label: "종합·비교 분석", hint: "교차 분석 페이지" },
  summaries: { label: "소스 요약", hint: "원본 문서 요약" },
  contradictions: { label: "모순 기록", hint: "충돌·검토 로그" },
};

// 백만원 단위 금액 컬럼 (천 단위 구분자 + 단위 표기)
export const MONEY_COLS = new Set(["budget_total", "budget_annual", "amount"]);

export function prettify(key) {
  return String(key).replace(/_/g, " ");
}

export function colLabel(table, key) {
  return COLS[table]?.[key] ?? SHARED_COLS[key] ?? prettify(key);
}

export function tableLabel(table) {
  return TABLE_LABELS[table] ?? prettify(table);
}
