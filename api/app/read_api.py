"""조회 전용 읽기 API: 위키 목록·페이지·전문검색, 데이터 테이블 (스펙 6.1)."""
import os
import subprocess
from pathlib import Path

import psycopg
from fastapi import APIRouter, HTTPException, Query
from psycopg.rows import dict_row

import wiki_ops

router = APIRouter(prefix="/api/v1")

DATA_TABLES = {
    "technologies": ["id", "name", "field", "sub_field", "lead_ministry", "trl_level",
                     "description", "source_id", "created_at", "updated_at"],
    "projects": ["id", "project_code", "name", "lead_ministry", "budget_total",
                 "budget_annual", "start_year", "end_year", "status", "source_id"],
    "policy_events": ["id", "event_date", "event_type", "title", "description",
                      "affected_fields", "source_id"],
    "ministries": ["id", "name", "abbreviation", "source_id"],
    "budget_history": ["id", "project_id", "fiscal_year", "amount", "source_id"],
    "tech_project_mapping": ["technology_id", "project_id", "relevance_score", "mapping_source"],
}


def _root() -> Path:
    return Path(os.environ.get("WIKI_REPO_PATH", "/data/wiki"))


@router.get("/wiki")
def wiki_list():
    return {"pages": wiki_ops.list_pages(_root())}


@router.get("/wiki/page")
def wiki_page(path: str = Query(...)):
    root = _root()
    if path not in wiki_ops.list_pages(root):
        raise HTTPException(status_code=404, detail="page not found")
    content = wiki_ops.read_page(root, path) or ""
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--format=%h\t%ad\t%s", "--date=short", "-5",
         "main", "--", path],
        capture_output=True, text=True, check=True,
    ).stdout
    history = [
        dict(zip(["hash", "date", "subject"], line.split("\t", 2)))
        for line in log.splitlines() if line
    ]
    return {"path": path, "content_md": content, "history": history}


@router.get("/wiki/search")
def wiki_search(q: str = Query(..., min_length=1)):
    out = subprocess.run(
        ["git", "-C", str(_root()), "grep", "-in", "--max-count=1", q, "main", "--", "*.md"],
        capture_output=True, text=True,
    )
    results = []
    for line in out.stdout.splitlines()[:50]:
        # 형식: main:tech/a.md:12:내용
        parts = line.split(":", 3)
        if len(parts) >= 4:
            results.append({"path": parts[1], "line": parts[3][:200]})
    return {"results": results}


@router.get("/data/{table}")
def data_table(table: str, sort_by: str | None = None, order: str = "asc",
               column: str | None = None, q: str | None = None,
               page: int = 1, limit: int = 50):
    cols = DATA_TABLES.get(table)
    if cols is None:
        raise HTTPException(status_code=404, detail="unknown table")
    if sort_by and sort_by not in cols:
        raise HTTPException(status_code=400, detail="invalid sort_by")
    if column and column not in cols:
        raise HTTPException(status_code=400, detail="invalid column")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="invalid order")
    limit = max(1, min(limit, 200))
    page = max(1, page)
    where, params = "", []
    if column and q:
        where = f" WHERE {column}::text ILIKE %s"
        params.append(f"%{q}%")
    order_sql = f" ORDER BY {sort_by} {order.upper()}" if sort_by else ""
    with psycopg.connect(os.environ["READONLY_DATABASE_URL"], row_factory=dict_row,
                         options="-c statement_timeout=5000") as conn:
        total = conn.execute(
            f"SELECT count(*) AS n FROM {table}{where}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM {table}{where}{order_sql} LIMIT %s OFFSET %s",
            params + [limit, (page - 1) * limit],
        ).fetchall()
    return {"rows": rows, "total": total, "page": page, "limit": limit}
