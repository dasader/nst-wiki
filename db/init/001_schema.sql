-- NEXT 기술 테이블
CREATE TABLE technologies (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    field VARCHAR(50) NOT NULL,        -- 10개 NEXT 분야
    sub_field VARCHAR(100),
    lead_ministry VARCHAR(50),
    trl_level INTEGER,
    description TEXT,
    wiki_page_path VARCHAR(200),       -- Wiki 페이지 참조
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    source_id VARCHAR(100)             -- 출처 소스
);

-- R&D 사업 테이블
CREATE TABLE projects (
    id SERIAL PRIMARY KEY,
    project_code VARCHAR(50) UNIQUE,
    name VARCHAR(200) NOT NULL,
    lead_ministry VARCHAR(50),
    budget_total BIGINT,               -- 총사업비 (백만원)
    budget_annual BIGINT,              -- 연간예산 (백만원)
    start_year INTEGER,
    end_year INTEGER,
    status VARCHAR(20),                -- 진행중/완료/예비타당성
    source_id VARCHAR(100)
);

-- 기술-사업 매핑 테이블
CREATE TABLE tech_project_mapping (
    technology_id INTEGER REFERENCES technologies(id),
    project_id INTEGER REFERENCES projects(id),
    relevance_score FLOAT,
    mapping_source VARCHAR(20),        -- manual/llm_inferred
    PRIMARY KEY (technology_id, project_id)
);

-- 예산 이력 테이블
CREATE TABLE budget_history (
    id SERIAL PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id),
    fiscal_year INTEGER NOT NULL,
    amount BIGINT NOT NULL,
    source_id VARCHAR(100)
);

-- 정책 이벤트 로그
CREATE TABLE policy_events (
    id SERIAL PRIMARY KEY,
    event_date DATE NOT NULL,
    event_type VARCHAR(50),            -- reform/announcement/law/summit
    title VARCHAR(200),
    description TEXT,
    affected_fields TEXT[],
    wiki_page_path VARCHAR(200),
    source_id VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

-- 부처 테이블
CREATE TABLE ministries (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    abbreviation VARCHAR(20),
    wiki_page_path VARCHAR(200)
);

-- 매핑 불가 표 보존 (스키마 검토 대기)
CREATE TABLE staging_tables (
    id SERIAL PRIMARY KEY,
    source_id VARCHAR(100) NOT NULL,
    table_title TEXT,                  -- 표 제목/캡션
    raw_data JSONB NOT NULL,           -- 정규화된 원본 표 (행 배열)
    suggested_mapping JSONB,           -- LLM의 매핑 제안 (참고용)
    mapping_confidence FLOAT,
    status VARCHAR(20) DEFAULT 'needs_review',  -- needs_review/mapped/discarded
    created_at TIMESTAMP DEFAULT NOW()
);

-- 인제스트 태스크 상태
CREATE TABLE ingest_tasks (
    task_id VARCHAR(100) PRIMARY KEY,
    source_id VARCHAR(100) NOT NULL,
    status VARCHAR(30) NOT NULL,       -- queued/parsing/parsed/classifying/
                                       -- staged/approved/rejected/failed
    branch_name VARCHAR(200),
    affected_pages JSONB,              -- 갱신된 페이지 + 갱신 제안 목록
    affected_tables JSONB,
    contradictions JSONB,
    error TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    reviewed_at TIMESTAMP
);

-- 승인 대기 데이터용 staging 스키마 (스펙 5.2)
-- ponytail: LIKE INCLUDING ALL — PK·기본값 복사, FK는 복사 안 됨(의도),
-- 시퀀스는 public과 공유되어 승인 upsert 시 id 충돌 없음
CREATE SCHEMA staging;
CREATE TABLE staging.technologies (LIKE public.technologies INCLUDING ALL);
CREATE TABLE staging.projects (LIKE public.projects INCLUDING ALL);
CREATE TABLE staging.tech_project_mapping (LIKE public.tech_project_mapping INCLUDING ALL);
CREATE TABLE staging.budget_history (LIKE public.budget_history INCLUDING ALL);
CREATE TABLE staging.policy_events (LIKE public.policy_events INCLUDING ALL);
CREATE TABLE staging.ministries (LIKE public.ministries INCLUDING ALL);
