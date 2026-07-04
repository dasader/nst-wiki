"""ingest_tasks 테이블 접근 헬퍼."""
import json
import os

import psycopg
from psycopg.rows import dict_row


def connect() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def create_task(task_id: str, source_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO ingest_tasks (task_id, source_id, status) VALUES (%s, %s, 'queued')",
            (task_id, source_id),
        )


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
