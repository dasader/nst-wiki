-- 티어 2: 롱포맷 지표 테이블 (설계서 4.4/5.2/12.4).
-- 엔티티 테이블(티어 1)에 안 맞는 (대상,지표,연도,값) 수치 표를 표마다 새 스키마 없이
-- 흡수하면서 집계·비교 질의를 유지하는 "좁은 허리". metric_name은 통제 어휘로 정규화한다.
CREATE TABLE IF NOT EXISTS metrics (
    id SERIAL PRIMARY KEY,
    entity VARCHAR(200) NOT NULL,      -- 지표 대상 (기술명·사업명·분야 등)
    metric_name VARCHAR(100) NOT NULL, -- map_tables.METRIC_VOCAB로 정규화 (예: 예산, 인력, 목표)
    year INTEGER,                      -- 시점(연도); 시계열이 아니면 NULL
    value NUMERIC NOT NULL,
    unit VARCHAR(30),                  -- 백만원, 명, 건 등
    source_id VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

-- staging 미러 (001과 동일 관례: LIKE INCLUDING ALL, 시퀀스는 public과 공유)
CREATE TABLE IF NOT EXISTS staging.metrics (LIKE public.metrics INCLUDING ALL);

-- Text-to-SQL 읽기 롤에 노출 (004와 동일)
GRANT SELECT ON metrics TO wiki_ro;
