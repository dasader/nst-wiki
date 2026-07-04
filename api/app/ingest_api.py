"""소스 업로드·상태 조회 엔드포인트 (스펙 6.1절)."""
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile

from app import db

router = APIRouter(prefix="/api/v1")
ALLOWED_EXTS = {".pdf", ".md", ".xlsx"}


def require_admin(x_admin_key: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_admin_key, os.environ["ADMIN_API_KEY"]):
        raise HTTPException(status_code=401, detail="invalid admin key")


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
