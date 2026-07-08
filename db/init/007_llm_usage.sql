-- Gemini 호출별 토큰 사용량. 비용은 저장하지 않고 조회 시 llm_pricing.json 단가로 계산한다
-- (단가가 바뀌거나 잘못 넣어도 과거 기록이 자동 정정된다).
-- output_tokens(candidates)와 thought_tokens는 분리 저장 — 과금은 둘의 합이지만
-- thinking_level=high에서 사고 토큰이 얼마나 먹는지 보여야 한다.
-- 002~006과 같은 관례: 버전 추적 테이블이 없으므로 몇 번 실행해도 안전해야 한다
-- (빈 DB는 docker-entrypoint-initdb.d가 자동 실행, 기존 DB는 psql로 직접 적용).
CREATE TABLE IF NOT EXISTS llm_usage (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    purpose VARCHAR(40) NOT NULL,
    model VARCHAR(60) NOT NULL,
    source_id VARCHAR(100),            -- 인제스트 중 호출이면 문서 귀속. 질의응답은 NULL
    prompt_tokens INTEGER NOT NULL,    -- 전체 입력 (cached_tokens 포함)
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL,    -- candidates
    thought_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER
);

CREATE INDEX IF NOT EXISTS llm_usage_created_idx ON llm_usage (created_at DESC);
CREATE INDEX IF NOT EXISTS llm_usage_source_idx ON llm_usage (source_id);

-- wiki_ro(Text-to-SQL 롤)에는 일부러 GRANT하지 않는다 — 004의 화이트리스트는 사용자 데이터 테이블 전용.
