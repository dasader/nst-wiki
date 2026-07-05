-- 재인제스트 중복 방지: 업로드 파일의 sha256을 태스크에 저장하고, 이미 인제스트된
-- (거부·실패가 아닌) 동일 파일 재업로드를 업로드 시점에 막는다. project_code 없는
-- 사업처럼 dedup 키가 없는 행도 소스 단위로 한 번에 걸러진다.
ALTER TABLE ingest_tasks ADD COLUMN IF NOT EXISTS file_hash varchar(80);
CREATE INDEX IF NOT EXISTS ix_ingest_tasks_file_hash ON ingest_tasks (file_hash);
