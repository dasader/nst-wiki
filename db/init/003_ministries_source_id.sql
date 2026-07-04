-- 001의 ministries에는 source_id가 누락되어 있었다 (다른 모든 테이블은 보유).
-- staging 적재(map_tables)와 승인 조회(list_staged)가 출처 추적에 필요하므로 양쪽에 추가.
ALTER TABLE ministries ADD COLUMN IF NOT EXISTS source_id VARCHAR(100);
ALTER TABLE staging.ministries ADD COLUMN IF NOT EXISTS source_id VARCHAR(100);
