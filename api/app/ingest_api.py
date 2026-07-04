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


def _reviewable(task_id: str) -> dict:
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task["status"] != "staged":
        raise HTTPException(status_code=409, detail=f"not staged (status: {task['status']})")
    return task


@router.post("/ingest/{task_id}/approve", dependencies=[Depends(require_admin)])
def approve(task_id: str, body: ApproveBody | None = None):
    task = _reviewable(task_id)
    counts = db.upsert_staged(task["source_id"])
    if task["branch_name"]:
        resolutions = body.contradiction_resolutions if body else {}
        wiki_ops.approve_branch(
            _wiki_root(), task["source_id"],
            f"approve: {task['source_id']}", resolutions=resolutions or None,
        )
    with db.connect() as conn:
        conn.execute(
            "UPDATE ingest_tasks SET status='approved', reviewed_at=%s WHERE task_id=%s",
            (datetime.now(timezone.utc), task_id),
        )
    return {"status": "approved", "upserted": counts}


@router.post("/ingest/{task_id}/reject", dependencies=[Depends(require_admin)])
def reject(task_id: str):
    task = _reviewable(task_id)
    db.discard_staged(task["source_id"])
    wiki_ops.reject_branch(_wiki_root(), task["source_id"])
    with db.connect() as conn:
        conn.execute(
            "UPDATE ingest_tasks SET status='rejected', reviewed_at=%s WHERE task_id=%s",
            (datetime.now(timezone.utc), task_id),
        )
    return {"status": "rejected"}


@router.post("/ingest", dependencies=[Depends(require_admin)])
async def ingest(
    file: UploadFile = File(...),
    title: str = Form(...),
    source_type: str = Form("policy_doc"),
    publisher: str = Form(""),
    publish_date: str = Form(""),
    tags: str = Form(""),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"unsupported format: {ext}")
    source_id, task_id = str(uuid.uuid4()), str(uuid.uuid4())
    src_dir = Path(os.environ.get("SOURCES_PATH", "/data/sources")) / source_id
    src_dir.mkdir(parents=True)
    data = await file.read()
    (src_dir / f"original{ext}").write_bytes(data)
    meta = {
        "source_id": source_id,
        "source_type": source_type,
        "title": title,
        "publisher": publisher,
        "publish_date": publish_date,
        "ingest_date": datetime.now(timezone.utc).isoformat(),
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
        "file_hash": "sha256:" + hashlib.sha256(data).hexdigest(),
    }
    (src_dir / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    db.create_task(task_id, source_id)
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
