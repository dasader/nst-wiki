-- Text-to-SQL 실행 전용 읽기 롤 (스펙 6.2: 읽기 전용 계정 + public 화이트리스트).
-- 개인 자가호스팅 전제로 비밀번호는 dev 기본값 — 외부 노출 시 .env로 교체할 것.
DO $$ BEGIN
    CREATE ROLE wiki_ro LOGIN PASSWORD 'ro_devpass';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
GRANT CONNECT ON DATABASE llm_wiki TO wiki_ro;
GRANT USAGE ON SCHEMA public TO wiki_ro;
GRANT SELECT ON technologies, projects, tech_project_mapping, budget_history,
                policy_events, ministries TO wiki_ro;
