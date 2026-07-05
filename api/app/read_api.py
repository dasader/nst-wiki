"""조회 전용 읽기 API: 위키 목록·페이지·전문검색, 데이터 테이블 (스펙 6.1)."""
import os
import re
import shlex
import subprocess
from pathlib import Path

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel

import wiki_ops
from app.ingest_api import require_admin

router = APIRouter(prefix="/api/v1")

# 새 페이지 허용 경로: PAGE_DIRS/ + 파일명(영문·한글·숫자·.-_). narrative.PATH_RE와 동일 규칙 + summaries
_NEW_PAGE_RE = re.compile(r"^(tech|entity|events|synthesis|summaries)/[\w가-힣.-]+\.md$")

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


def _main_pages(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", "main", "--name-only"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.splitlines()
            if p.endswith(".md") and p.split("/")[0] in wiki_ops.PAGE_DIRS]


def _page_titles(root: Path) -> dict[str, str]:
    """각 페이지 프론트매터의 title을 한 번의 git grep으로 수집한다 (path → 한글 제목)."""
    out = subprocess.run(
        ["git", "-C", str(root), "grep", "--max-count=1", "-e", "^title:", "main", "--", "*.md"],
        capture_output=True, text=True,
    ).stdout
    titles: dict[str, str] = {}
    for line in out.splitlines():
        m = re.match(r"^main:(.+?):title:\s*(.*)$", line)  # main:tech/a.md:title: 값
        if m:
            titles[m.group(1)] = m.group(2).strip().strip("\"'")
    return titles


def _parse_query(q: str) -> tuple[list[str], str]:
    """검색 연산자 파싱. 공백=AND(모든 낱말 포함), "따옴표"=구문, `|`/`OR`=OR."""
    try:
        toks = shlex.split(q)          # 따옴표로 감싼 구문을 하나의 토큰으로 유지
    except ValueError:
        toks = q.split()
    mode, terms = "and", []
    for t in toks:
        if t == "|" or t.upper() == "OR":
            mode = "or"
        else:
            terms.append(t)
    return (terms or [q.strip()]), mode


@router.get("/wiki")
def wiki_list():
    root = _root()
    return {"pages": _main_pages(root), "titles": _page_titles(root)}


@router.get("/wiki/page")
def wiki_page(path: str = Query(...), as_of: str | None = Query(None)):
    root = _root()
    if path not in _main_pages(root):  # 현재 main 기준 화이트리스트(경로 이탈 차단 겸용)
        raise HTTPException(status_code=404, detail="page not found")
    if as_of:  # 시점 조회: 해당 날짜 이하 마지막 커밋의 내용
        content = wiki_ops.read_page_asof(root, path, as_of)
        if content is None:
            raise HTTPException(status_code=404, detail="page not found at that date")
        return {"path": path, "content_md": content, "as_of": as_of}
    show = subprocess.run(
        ["git", "-C", str(root), "show", f"main:{path}"],
        capture_output=True, text=True,
    )
    if show.returncode != 0:
        raise HTTPException(status_code=404, detail="page not found")
    content = show.stdout
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--format=%h\t%ad\t%s", "--date=short", "-5",
         "main", "--", path],
        capture_output=True, text=True,
    ).stdout
    history = [
        dict(zip(["hash", "date", "subject"], line.split("\t", 2)))
        for line in log.splitlines() if line
    ]
    return {"path": path, "content_md": content, "history": history}


class WikiEditBody(BaseModel):
    path: str
    content_md: str
    message: str | None = None


@router.put("/wiki/page", dependencies=[Depends(require_admin)])
def wiki_edit(body: WikiEditBody):
    root = _root()
    existing = body.path in _main_pages(root)
    if not existing and not _NEW_PAGE_RE.match(body.path):
        raise HTTPException(status_code=400, detail="invalid page path")
    msg = body.message or f"edit: {body.path}"
    changed = wiki_ops.write_page(root, body.path, body.content_md, msg)
    if changed:  # 변경이 있을 때만 재색인 enqueue (모델 로드는 워커에서)
        try:
            from tasks import embed_pages

            embed_pages.delay([body.path])
        except Exception:
            pass  # 색인 enqueue 실패는 저장을 되돌릴 사유가 아님 — POST /reindex로 복구
    return {"path": body.path, "committed": changed, "created": not existing}


@router.get("/wiki/search")
def wiki_search(q: str = Query(..., min_length=1)):
    root = _root()
    terms, mode = _parse_query(q)
    # -e로 각 낱말을 패턴으로 강제 — "-O" 등으로 시작해도 옵션으로 해석되지 않게
    # -F로 고정 문자열 검색 — 사용자 입력은 정규식이 아니라 리터럴로 취급
    # --all-match: 여러 낱말이 모두(=AND) 포함된 파일만. OR이면 생략(git grep 기본이 OR)
    args = ["git", "-C", str(root), "grep", "-inF", "--max-count=1"]
    if mode == "and" and len(terms) > 1:
        args.append("--all-match")
    for t in terms:
        args += ["-e", t]
    args += ["main", "--", "*.md"]
    out = subprocess.run(args, capture_output=True, text=True)
    titles = _page_titles(root)
    results = []
    for line in out.stdout.splitlines()[:50]:
        # 형식: main:tech/a.md:12:내용
        parts = line.split(":", 3)
        if len(parts) >= 4:
            path = parts[1]
            results.append({"path": path, "title": titles.get(path), "line": parts[3][:200]})
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
