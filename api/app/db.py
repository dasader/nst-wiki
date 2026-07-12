"""ingest_tasks 테이블 접근 헬퍼."""
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

SCHEMA_DIR = Path(os.environ.get("SCHEMA_PATH", "/schema"))
_SCHEMA_LOCK_KEY = 0x6E737477  # 'nstw' — api·worker 동시 기동 시 스키마 적용 직렬화


def connect() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def apply_schema(schema_dir: Path | None = None) -> list[str]:
    """db/init/NNN_*.sql을 번호 순서대로 멱등 적용하고 적용한 파일명을 반환한다.

    postgres의 docker-entrypoint-initdb.d는 데이터 디렉토리가 빌 때만 실행된다 — 기존 DB에는
    새 SQL이 반영되지 않고, "README대로 psql로 직접 적용"은 아무도 하지 않는다(실제로 두 번
    사고가 났다: 위키 미초기화, llm_usage 미생성). 모든 SQL이 멱등이므로 기동 때마다 다시
    적용해 코드와 DB를 수렴시킨다.
    """
    d = SCHEMA_DIR if schema_dir is None else schema_dir
    files = sorted(d.glob("[0-9][0-9][0-9]_*.sql")) if d.is_dir() else []
    if not files:
        return []
    with connect() as conn:  # 한 트랜잭션 — 하나라도 실패하면 전부 롤백
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_KEY,))
        for f in files:
            conn.execute(f.read_text(encoding="utf-8"))
    return [f.name for f in files]


def create_task(task_id: str, source_id: str, file_hash: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO ingest_tasks (task_id, source_id, status, file_hash) "
            "VALUES (%s, %s, 'queued', %s)",
            (task_id, source_id, file_hash),
        )


# 거부·실패가 아닌 상태 = 이미 인제스트됐거나 진행 중 → 재업로드 시 중복
_ACTIVE_STATUSES = ("queued", "parsing", "classifying", "staged", "approved")


def find_ingested_by_hash(file_hash: str) -> dict | None:
    with connect() as conn:
        return conn.execute(
            "SELECT task_id, source_id, status FROM ingest_tasks "
            "WHERE file_hash = %s AND status = ANY(%s) ORDER BY created_at DESC LIMIT 1",
            (file_hash, list(_ACTIVE_STATUSES)),
        ).fetchone()


def get_task(task_id: str) -> dict | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM ingest_tasks WHERE task_id = %s", (task_id,)
        ).fetchone()


def set_status(task_id: str, status: str, error: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE ingest_tasks SET status = %s, error = %s WHERE task_id = %s",
            (status, error, task_id),
        )


def delete_task(task_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM ingest_tasks WHERE task_id = %s", (task_id,))


def save_results(task_id: str, results: dict) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE ingest_tasks SET affected_pages = %s, affected_tables = %s, "
            "contradictions = %s, branch_name = %s WHERE task_id = %s",
            (json.dumps(results["affected_pages"], ensure_ascii=False),
             json.dumps(results["affected_tables"], ensure_ascii=False),
             json.dumps(results["contradictions"], ensure_ascii=False),
             results["branch"], task_id),
        )


STAGED_TABLES = ["technologies", "projects", "policy_events", "ministries", "budget_history", "metrics"]

_UPSERT_SQL = {
    "technologies": """
        INSERT INTO technologies (name, field, sub_field, lead_ministry, trl_level,
                                  description, source_id)
        SELECT DISTINCT ON (name) name, field, sub_field, lead_ministry, trl_level,
               description, source_id
        FROM staging.technologies WHERE source_id = %s
        ORDER BY name, id DESC
        ON CONFLICT (name) DO UPDATE SET
            field = EXCLUDED.field, sub_field = EXCLUDED.sub_field,
            lead_ministry = EXCLUDED.lead_ministry, trl_level = EXCLUDED.trl_level,
            description = EXCLUDED.description, source_id = EXCLUDED.source_id,
            updated_at = NOW()
    """,
    "ministries": """
        INSERT INTO ministries (name, abbreviation, wiki_page_path, source_id)
        SELECT DISTINCT ON (name) name, abbreviation, wiki_page_path, source_id
        FROM staging.ministries WHERE source_id = %s
        ORDER BY name, id DESC
        ON CONFLICT (name) DO UPDATE SET
            abbreviation = EXCLUDED.abbreviation, source_id = EXCLUDED.source_id
    """,
    "projects": """
        INSERT INTO projects (project_code, name, lead_ministry, budget_total,
                              budget_annual, start_year, end_year, status, source_id)
        SELECT DISTINCT ON (project_code) project_code, name, lead_ministry, budget_total,
               budget_annual, start_year, end_year, status, source_id
        FROM staging.projects WHERE source_id = %s AND project_code IS NOT NULL
        ORDER BY project_code, id DESC
        ON CONFLICT (project_code) DO UPDATE SET
            name = EXCLUDED.name, lead_ministry = EXCLUDED.lead_ministry,
            budget_total = EXCLUDED.budget_total, budget_annual = EXCLUDED.budget_annual,
            start_year = EXCLUDED.start_year, end_year = EXCLUDED.end_year,
            status = EXCLUDED.status, source_id = EXCLUDED.source_id
    """,
    # ponytail: project_code 없는 사업·policy_events는 자연키가 없어 단순 INSERT —
    # 동일 소스 재승인은 상태 가드(409)가 막아주므로 중복은 서로 다른 소스 간에만 발생 가능
    "_projects_no_code": """
        INSERT INTO projects (name, lead_ministry, budget_total, budget_annual,
                              start_year, end_year, status, source_id)
        SELECT name, lead_ministry, budget_total, budget_annual, start_year,
               end_year, status, source_id
        FROM staging.projects WHERE source_id = %s AND project_code IS NULL
    """,
    # 교차 소스 중복 방지: 같은 event_date + 정규화(공백·가운뎃점 제거, 소문자) 제목이 이미
    # canonical에 있으면 skip(먼저 적재한 소스가 우선). ponytail: casefold는 lower()로 근사
    # (한글은 대소문자 없음). 한 배치 내 자체 중복은 서로 다른 소스에서만 생긴다는 전제라 미처리.
    "policy_events": """
        INSERT INTO policy_events (event_date, event_type, title, description,
                                   affected_fields, wiki_page_path, source_id)
        SELECT s.event_date, s.event_type, s.title, s.description, s.affected_fields,
               s.wiki_page_path, s.source_id
        FROM staging.policy_events s WHERE s.source_id = %s
          AND NOT EXISTS (
            SELECT 1 FROM policy_events p
            WHERE p.event_date = s.event_date
              AND lower(regexp_replace(p.title, '[[:space:]·ㆍ‧]', '', 'g'))
                = lower(regexp_replace(s.title, '[[:space:]·ㆍ‧]', '', 'g'))
          )
    """,
    # project_id는 문서 표에서 직접 얻을 수 없어 NULL — map_tables 주석 참고
    "budget_history": """
        INSERT INTO budget_history (fiscal_year, amount, source_id)
        SELECT fiscal_year, amount, source_id
        FROM staging.budget_history
        WHERE source_id = %s AND fiscal_year IS NOT NULL AND amount IS NOT NULL
    """,
    # 티어 2 지표: 자연키 없이 단순 INSERT (budget_history와 동일 관례).
    # 동일 소스 재승인은 상태 가드(409)가 막고, 교차 소스 중복은 (entity,metric,year) 다르면 발생 안 함
    "metrics": """
        INSERT INTO metrics (entity, metric_name, year, value, unit, source_id)
        SELECT entity, metric_name, year, value, unit, source_id
        FROM staging.metrics WHERE source_id = %s
    """,
}


def list_staged(source_id: str) -> dict:
    out = {}
    with connect() as conn:
        for t in STAGED_TABLES:
            out[t] = conn.execute(
                f"SELECT * FROM staging.{t} WHERE source_id = %s", (source_id,)
            ).fetchall()
        out["needs_review"] = conn.execute(
            "SELECT id, table_title, raw_data, suggested_mapping, mapping_confidence, status "
            "FROM staging_tables WHERE source_id = %s AND status = 'needs_review'", (source_id,)
        ).fetchall()
    return out


def get_staging_table(staging_id: int, source_id: str) -> dict | None:
    """검토 대기 표 원본 로드 (source_id 일치 확인 — 타 소스 행 승격 차단)."""
    with connect() as conn:
        return conn.execute(
            "SELECT raw_data FROM staging_tables WHERE id = %s AND source_id = %s "
            "AND status = 'needs_review'", (staging_id, source_id),
        ).fetchone()


# melt된 지표 행 → staging.metrics 적재 SQL. 자동 매핑·수동 승격이 공유(컬럼 단일 출처).
_STAGE_METRICS_SQL = ("INSERT INTO staging.metrics (entity, metric_name, year, value, unit, source_id) "
                      "VALUES (%s, %s, %s, %s, %s, %s)")


def stage_metrics_rows(source_id: str, rows: list[tuple]) -> int:
    """melt된 지표 행들을 staging.metrics에 적재 (적재 건수 반환)."""
    with connect() as conn:
        conn.cursor().executemany(_STAGE_METRICS_SQL, [list(r) + [source_id] for r in rows])
    return len(rows)


def promote_staging_metrics(staging_id: int, source_id: str, rows: list[tuple]) -> int:
    """melt된 지표 행들을 staging.metrics에 적재하고 원 검토대기 행을 mapped로 처리 (한 트랜잭션).
    staging.metrics에 넣으므로 소스 승인 시 다른 staging과 함께 canonical로 upsert된다."""
    with connect() as conn:
        conn.cursor().executemany(_STAGE_METRICS_SQL, [list(r) + [source_id] for r in rows])
        conn.execute("UPDATE staging_tables SET status = 'mapped' WHERE id = %s", (staging_id,))
    return len(rows)


def upsert_staged(source_id: str) -> dict:
    counts = {}
    with connect() as conn:  # 한 트랜잭션: 전부 성공 시에만 커밋
        for t in STAGED_TABLES:
            cur = conn.execute(_UPSERT_SQL[t], (source_id,))
            counts[t] = cur.rowcount
            if t == "projects":
                cur = conn.execute(_UPSERT_SQL["_projects_no_code"], (source_id,))
                counts[t] += cur.rowcount
            conn.execute(f"DELETE FROM staging.{t} WHERE source_id = %s", (source_id,))
    return counts


def drop_staged_rows(source_id: str, exclude: dict) -> int:
    """승인 전 사람이 제외한 staging 행을 삭제한다. 표 오매핑을 걸러내는 용도."""
    n = 0
    with connect() as conn:
        for t, ids in (exclude or {}).items():
            if t not in STAGED_TABLES or not ids:
                continue  # 화이트리스트 밖 테이블명은 무시 (SQL 주입 방지)
            cur = conn.execute(
                f"DELETE FROM staging.{t} WHERE source_id = %s AND id = ANY(%s)",
                (source_id, [int(i) for i in ids]),
            )
            n += cur.rowcount
    return n


def discard_staged(source_id: str) -> None:
    with connect() as conn:
        for t in STAGED_TABLES:
            conn.execute(f"DELETE FROM staging.{t} WHERE source_id = %s", (source_id,))
        conn.execute(
            "UPDATE staging_tables SET status = 'discarded' WHERE source_id = %s",
            (source_id,),
        )


# source_id로 소유가 추적되는 정식 테이블 (ministries는 seed/공유라 제외 — 지우면 다른 소스가 깨진다)
SOURCE_TABLES = ["technologies", "projects", "policy_events", "budget_history"]


def delete_source(source_id: str) -> dict:
    """승인된 소스를 un-ingest: 이 source_id의 정식 행·staging 잔재를 전부 삭제하고 삭제 건수를 반환.

    tech_project_mapping·budget_history는 projects/technologies를 FK 참조하므로 자식부터 지운다.
    tech_project_mapping엔 source_id가 없어 이 소스의 tech/project를 참조하는 행을 고아로 보고 함께 삭제.
    위키 서사는 소스 간 병합되어 여기서 되돌릴 수 없다 (엔드포인트가 summaries 페이지만 처리).
    """
    counts = {}
    with connect() as conn:  # 한 트랜잭션 — 부분 삭제로 FK가 어긋나지 않게
        # 이 소스가 만든 projects/technologies를 참조하는 자식 행 먼저 (source_id 무관)
        counts["tech_project_mapping"] = conn.execute(
            "DELETE FROM tech_project_mapping WHERE "
            "technology_id IN (SELECT id FROM technologies WHERE source_id=%s) OR "
            "project_id IN (SELECT id FROM projects WHERE source_id=%s)",
            (source_id, source_id),
        ).rowcount
        counts["budget_history"] = conn.execute(
            "DELETE FROM budget_history WHERE source_id=%s OR "
            "project_id IN (SELECT id FROM projects WHERE source_id=%s)",
            (source_id, source_id),
        ).rowcount
        for t in ["projects", "technologies", "policy_events"]:
            counts[t] = conn.execute(
                f"DELETE FROM {t} WHERE source_id=%s", (source_id,)  # 이름은 리터럴 화이트리스트
            ).rowcount
        # staging 잔재 (거부 전 남았을 수 있음)
        staged = 0
        for t in STAGED_TABLES:
            staged += conn.execute(
                f"DELETE FROM staging.{t} WHERE source_id=%s", (source_id,)
            ).rowcount
        staged += conn.execute(
            "DELETE FROM staging_tables WHERE source_id=%s", (source_id,)
        ).rowcount
        counts["staging"] = staged
    return counts


def record_llm_usage(purpose: str, model: str, source_id: str | None,
                     prompt_tokens: int, cached_tokens: int, output_tokens: int,
                     thought_tokens: int, latency_ms: int | None = None) -> None:
    """Gemini 호출 한 건의 토큰 사용량 적재. 비용은 저장하지 않는다 (조회 시 단가로 계산)."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO llm_usage (purpose, model, source_id, prompt_tokens, cached_tokens, "
            "output_tokens, thought_tokens, latency_ms) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (purpose, model, source_id, prompt_tokens, cached_tokens,
             output_tokens, thought_tokens, latency_ms),
        )


_USAGE_COLS = ("count(*) AS calls, "
               "COALESCE(sum(prompt_tokens),0) AS prompt_tokens, "
               "COALESCE(sum(cached_tokens),0) AS cached_tokens, "
               "COALESCE(sum(output_tokens),0) AS output_tokens, "
               "COALESCE(sum(thought_tokens),0) AS thought_tokens")


def usage_rollups() -> dict:
    """비용 페이지용 집계 원자료. 토큰만 반환하고 금액 환산은 호출 측(단가 설정)이 한다."""
    with connect() as conn:
        return {
            "since": (conn.execute("SELECT min(created_at) AS t FROM llm_usage")
                      .fetchone()["t"]),
            "by_model": conn.execute(
                f"SELECT model, {_USAGE_COLS} FROM llm_usage GROUP BY model ORDER BY 2 DESC"
            ).fetchall(),
            "by_purpose": conn.execute(
                f"SELECT purpose, model, {_USAGE_COLS} FROM llm_usage "
                "GROUP BY purpose, model ORDER BY 3 DESC"
            ).fetchall(),
            "by_source": conn.execute(
                f"SELECT source_id, model, {_USAGE_COLS} FROM llm_usage "
                "WHERE source_id IS NOT NULL GROUP BY source_id, model"
            ).fetchall(),
            "query_side": conn.execute(
                f"SELECT model, {_USAGE_COLS} FROM llm_usage "
                "WHERE source_id IS NULL GROUP BY model"
            ).fetchall(),
        }


TERMINAL_STATUSES = ["staged", "approved", "rejected", "failed"]

# 전체 초기화 대상 (이름은 리터럴 화이트리스트). ministries는 시드 행 보존 때문에 별도 처리.
# FK 참조(tech_project_mapping·budget_history)가 있어 한 TRUNCATE 문에 함께 넣는다.
_RESET_TABLES = [
    "tech_project_mapping", "budget_history", "metrics", "technologies", "projects",
    "policy_events", "staging_tables", "ingest_tasks", "llm_usage",
    "staging.technologies", "staging.projects", "staging.tech_project_mapping",
    "staging.budget_history", "staging.metrics", "staging.policy_events", "staging.ministries",
]


def list_in_flight() -> list[dict]:
    """아직 종료 상태가 아닌 태스크 (queued/parsing/classifying). 초기화를 막는 대상."""
    with connect() as conn:
        return conn.execute(
            "SELECT task_id, source_id, status FROM ingest_tasks WHERE NOT (status = ANY(%s))",
            (TERMINAL_STATUSES,),
        ).fetchall()


def reset_all() -> dict:
    """모든 인제스트 데이터를 지워 새 배포 상태로 되돌린다. 되돌릴 수 없다.

    스키마는 건드리지 않는다 (설계 원칙 5 — DDL은 db/init/NNN_*.sql로만).
    ministries의 시드 행(source_id IS NULL, 006_seed_ministries.sql)은 새 배포에도
    존재하므로 보존하고, 인제스트로 들어온 행만 지운다.
    """
    counts: dict[str, int] = {}
    with connect() as conn:  # 한 트랜잭션 — 부분 초기화로 FK가 어긋나지 않게
        for t in _RESET_TABLES:
            counts[t] = conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
        conn.execute(f"TRUNCATE {', '.join(_RESET_TABLES)} RESTART IDENTITY")
        counts["ministries"] = conn.execute(
            "DELETE FROM ministries WHERE source_id IS NOT NULL"  # 시드(NULL)는 보존
        ).rowcount
    return counts


def list_tasks(limit: int = 50) -> list[dict]:
    with connect() as conn:
        return conn.execute(
            "SELECT task_id, source_id, status, branch_name, created_at, reviewed_at "
            "FROM ingest_tasks ORDER BY created_at DESC LIMIT %s", (limit,)
        ).fetchall()
