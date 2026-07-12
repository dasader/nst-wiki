-- metrics 중복·이중집계 방지: 같은 (출처, 대상, 지표, 연도)는 한 행으로.
-- 무연도 승격(year=NULL)도 같은 값으로 취급해 dedup — NULLS NOT DISTINCT (PG15+).
-- 승인 upsert(db._UPSERT_SQL["metrics"])의 ON CONFLICT 대상.
CREATE UNIQUE INDEX IF NOT EXISTS metrics_dedup
    ON metrics (source_id, entity, metric_name, year) NULLS NOT DISTINCT;
