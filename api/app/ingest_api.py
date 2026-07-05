"""소스 업로드·상태 조회 엔드포인트 (스펙 6.1절)."""
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from app import db
import wiki_ops

router = APIRouter(prefix="/api/v1")
ALLOWED_EXTS = {".pdf", ".md", ".xlsx"}


class ApproveBody(BaseModel):
    contradiction_resolutions: dict[str, str] = {}
    exclude: dict[str, list[int]] = {}  # {staging 테이블명: 승인 제외할 행 id 목록}


def _wiki_root() -> Path:
    return Path(os.environ.get("WIKI_REPO_PATH", "/data/wiki"))


def require_admin(x_admin_key: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_admin_key, os.environ["ADMIN_API_KEY"]):
        raise HTTPException(status_code=401, detail="invalid admin key")


@router.get("/ingest")
def list_ingest_tasks():
    return {"tasks": db.list_tasks()}


@router.get("/ingest/{task_id}/review")
def review(task_id: str):
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    pages = task["affected_pages"] or []
    return {
        "status": task["status"],
        "source_id": task["source_id"],
        "wiki_diff": wiki_ops.diff_branch(_wiki_root(), task["source_id"]),
        "staged": db.list_staged(task["source_id"]),
        "affected_pages": pages,
        "suggestions": [p for p in pages if p.get("action") in ("suggested", "rejected")],
        "contradictions": task["contradictions"] or [],
    }


def _claim(task_id: str, new_status: str) -> dict:
    """staged → new_status로 원자적 전이. 동시 요청은 한쪽만 성공(나머지 409)."""
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    with db.connect() as conn:
        cur = conn.execute(
            "UPDATE ingest_tasks SET status=%s, reviewed_at=%s "
            "WHERE task_id=%s AND status='staged'",
            (new_status, datetime.now(timezone.utc), task_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail=f"not staged (status: {task['status']})")
    return task


@router.post("/ingest/{task_id}/approve", dependencies=[Depends(require_admin)])
def approve(task_id: str, body: ApproveBody | None = None):
    task = _claim(task_id, "approved")
    try:
        if body and body.exclude:
            db.drop_staged_rows(task["source_id"], body.exclude)
        counts = db.upsert_staged(task["source_id"])
        if task["branch_name"]:
            resolutions = body.contradiction_resolutions if body else {}
            wiki_ops.approve_branch(
                _wiki_root(), task["source_id"],
                f"approve: {task['source_id']}", resolutions=resolutions or None,
            )
        pages = [p["path"] for p in (task["affected_pages"] or [])
                 if p.get("action") in ("create", "update")]
        if pages:
            try:
                from tasks import embed_pages

                embed_pages.delay(pages)
            except Exception:
                # ponytail: 색인 enqueue 실패는 승인을 되돌릴 사유가 아님 — POST /reindex로 복구
                pass
    except Exception:
        db.set_status(task_id, "staged")  # 클레임 되돌림 — 재시도 가능
        raise
    return {"status": "approved", "upserted": counts}


@router.post("/ingest/{task_id}/reject", dependencies=[Depends(require_admin)])
def reject(task_id: str):
    task = _claim(task_id, "rejected")
    try:
        db.discard_staged(task["source_id"])
        wiki_ops.reject_branch(_wiki_root(), task["source_id"])
    except Exception:
        db.set_status(task_id, "staged")
        raise
    return {"status": "rejected"}


@router.post("/reindex", dependencies=[Depends(require_admin)])
def reindex():
    from tasks import reindex_all

    reindex_all.delay()
    return {"status": "queued"}


@router.post("/ingest", dependencies=[Depends(require_admin)])
async def ingest(
    file: UploadFile = File(...),
    title: str = Form(...),
    source_type: str = Form("policy_doc"),
    publisher: str = Form(""),
    publish_date: str = Form(""),
    tags: str = Form(""),
    force: bool = Form(False),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"unsupported format: {ext}")
    data = await file.read()
    file_hash = "sha256:" + hashlib.sha256(data).hexdigest()
    if not force:
        dup = db.find_ingested_by_hash(file_hash)
        if dup:
            raise HTTPException(status_code=409, detail=(
                f"이미 인제스트된 문서입니다 (task {dup['task_id'][:8]}, 상태 {dup['status']}). "
                "교체하려면 기존 태스크를 먼저 거부하거나 force=true로 다시 올리세요."
            ))
    source_id, task_id = str(uuid.uuid4()), str(uuid.uuid4())
    src_dir = Path(os.environ.get("SOURCES_PATH", "/data/sources")) / source_id
    src_dir.mkdir(parents=True)
    (src_dir / f"original{ext}").write_bytes(data)
    meta = {
        "source_id": source_id,
        "source_type": source_type,
        "title": title,
        "publisher": publisher,
        "publish_date": publish_date,
        "ingest_date": datetime.now(timezone.utc).isoformat(),
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
        "file_hash": file_hash,
    }
    (src_dir / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    db.create_task(task_id, source_id, file_hash)
    from tasks import run_ingest  # celery 브로커 연결은 enqueue 시점에만 필요

    run_ingest.delay(task_id)
    return {"task_id": task_id, "status": "queued"}


@router.get("/ingest/{task_id}/status")
def ingest_status(task_id: str):
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return {
        "status": task["status"],
        "affected_pages": task["affected_pages"],
        "affected_tables": task["affected_tables"],
        "contradictions": task["contradictions"],
        "error": task["error"],
    }
