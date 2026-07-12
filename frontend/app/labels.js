// 영문 스키마·경로 코드를 한글 라벨로 옮기는 도메인 사전.
// DB 스키마(db/init/001_schema.sql)와 위키 디렉토리(scripts/init_wiki.py) 기준.

export const TABLE_LABELS = {
  technologies: "기술",
  projects: "사업",
  policy_events: "정책 변화",
  ministries: "부처·기관",
  budget_history: "예산 이력",
  metrics: "지표",
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
  metrics: { entity: "대상", metric_name: "지표", year: "연도", value: "값", unit: "단위" },
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
};

// 테이블별 실존 컬럼 (api/data_schema.py DATA_TABLES 미러). 데이터 링크 유효성 검사에 쓴다.
export const COLUMNS = {
  technologies: ["id", "name", "field", "sub_field", "lead_ministry", "trl_level",
                 "description", "source_id", "created_at", "updated_at"],
  projects: ["id", "project_code", "name", "lead_ministry", "budget_total",
             "budget_annual", "start_year", "end_year", "status", "source_id"],
  policy_events: ["id", "event_date", "event_type", "title", "description",
                  "affected_fields", "source_id"],
  ministries: ["id", "name", "abbreviation", "source_id"],
  budget_history: ["id", "project_id", "fiscal_year", "amount", "source_id"],
  metrics: ["id", "entity", "metric_name", "year", "value", "unit", "source_id"],
  tech_project_mapping: ["technology_id", "project_id", "relevance_score", "mapping_source"],
};

// 백만원 단위 금액 컬럼 (천 단위 구분자 + 단위 표기)
export const MONEY_COLS = new Set(["budget_total", "budget_annual", "amount"]);

export function prettify(key) {
  return String(key).replace(/_/g, " ");
}

// 페이지 경로 → 표시 슬러그: tech/hbm.md → hbm · 과기정통부.md → 과기정통부
export function pageSlug(path) {
  const s = String(path);
  return s.replace(/\.md$/, "").split("/").slice(1).join("/") || s;
}

// /wiki/view 딥링크 (선택적 검색어 q 유지)
export function wikiViewHref(path, q) {
  const base = `/wiki/view?path=${encodeURIComponent(path)}`;
  return q ? `${base}&q=${encodeURIComponent(q)}` : base;
}

export function colLabel(table, key) {
  return COLS[table]?.[key] ?? SHARED_COLS[key] ?? prettify(key);
}

export function tableLabel(table) {
  return TABLE_LABELS[table] ?? prettify(table);
}
