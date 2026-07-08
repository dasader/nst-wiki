-- 승인 upsert(ON CONFLICT)용 유니크 제약.
-- staging 스키마에는 적용하지 않는다: 승인 전 데이터는 중복도 검토 대상이며,
-- 제약 위반으로 인제스트가 실패하는 것보다 사람이 승인 화면에서 거르는 것이 맞다.
--
-- ALTER TABLE ... ADD CONSTRAINT에는 IF NOT EXISTS 구문이 없다. apply_schema가 기동마다
-- 재적용하므로 카탈로그를 확인해 감싼다 (모든 SQL이 멱등이라는 전제를 지킨다).
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'technologies_name_key') THEN
        ALTER TABLE technologies ADD CONSTRAINT technologies_name_key UNIQUE (name);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ministries_name_key') THEN
        ALTER TABLE ministries ADD CONSTRAINT ministries_name_key UNIQUE (name);
    END IF;
END $$;
