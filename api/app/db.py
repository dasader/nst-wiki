"""ingest_tasks 테이블 접근 헬퍼."""
import json
import os

import psycopg
from psycopg.rows import dict_row


def connect() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


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


STAGED_TABLES = ["technologies", "projects", "policy_events", "ministries", "budget_history"]

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
            "FROM staging_tables WHERE source_id = %s", (source_id,)
        ).fetchall()
    return out


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


def list_tasks(limit: int = 50) -> list[dict]:
    with connect() as conn:
        return conn.execute(
            "SELECT task_id, source_id, status, branch_name, created_at, reviewed_at "
            "FROM ingest_tasks ORDER BY created_at DESC LIMIT %s", (limit,)
        ).fetchall()
