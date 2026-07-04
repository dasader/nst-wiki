-- 승인 upsert(ON CONFLICT)용 유니크 제약.
-- staging 스키마에는 적용하지 않는다: 승인 전 데이터는 중복도 검토 대상이며,
-- 제약 위반으로 인제스트가 실패하는 것보다 사람이 승인 화면에서 거르는 것이 맞다.
ALTER TABLE technologies ADD CONSTRAINT technologies_name_key UNIQUE (name);
ALTER TABLE ministries ADD CONSTRAINT ministries_name_key UNIQUE (name);
