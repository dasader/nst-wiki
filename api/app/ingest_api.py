"""소스 업로드·상태 조회 엔드포인트 (스펙 6.1절)."""
import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import db
from app.auth import require_admin
import wiki_ops

router = APIRouter(prefix="/api/v1")
ALLOWED_EXTS = {".pdf", ".md", ".xlsx"}


class ApproveBody(BaseModel):
    contradiction_resolutions: dict[str, str] = {}
    exclude: dict[str, list[int]] = {}  # {staging 테이블명: 승인 제외할 행 id 목록}


def _wiki_root() -> Path:
    return wiki_ops.wiki_root()


@router.get("/admin/verify", dependencies=[Depends(require_admin)])
def verify_admin():
    """로그인 게이트용 키 검증. 유효하면 200, 아니면 require_admin이 401."""
    return {"ok": True}


@router.get("/ingest")
def list_ingest_tasks():
    tasks = db.list_tasks()
    for t in tasks:   # 큐 카드용 사람 읽는 제목(디스크 metadata) — 개인 규모라 파일 N개 읽기 무해
        t["title"] = _source_meta(t["source_id"]).get("title") or ""
    return {"tasks": tasks}


@router.get("/ingest/{task_id}/review")
def review(task_id: str):
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    pages = task["affected_pages"] or []
    meta = _source_meta(task["source_id"])
    return {
        "status": task["status"],
        "source_id": task["source_id"],
        "title": meta.get("title") or "",
        "publish_date": meta.get("publish_date") or "",  # 모순 카드에서 '신규' 측 시점 표시용
        "wiki_diff": wiki_ops.diff_branch(_wiki_root(), task["source_id"]),
        "staged": db.list_staged(task["source_id"]),
        "affected_pages": pages,
        "suggestions": [p for p in pages if p.get("action") in ("suggested", "rejected")],
        "contradictions": task["contradictions"] or [],
    }


def _apply_resolutions(task: dict, resolutions: dict[str, str]) -> None:
    """모순 결정(keep/replace/both)을 승인된 페이지 본문에 확정 반영한다.

    approve_branch가 log.md에 '해결' 도장을 찍는 것과 별개로, 실제 페이지 본문을 결정대로
    고치고 unresolved_contradictions 마커를 제거한다. main 병합 이후 페이지당 커밋.
    """
    if not resolutions or not task["contradictions"]:
        return
    from pipeline.narrative import apply_resolutions, group_resolutions

    root = _wiki_root()
    for page, decisions in group_resolutions(
            task["source_id"], task["contradictions"], resolutions).items():
        current = wiki_ops.read_page(root, page)
        if current is None:
            continue  # 삭제·이름변경된 페이지 — 반영 대상 없음
        wiki_ops.write_page(root, page, apply_resolutions(page, current, decisions),
                            f"resolve: {page} 모순 반영 ({task['source_id'][:8]})")


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
            from pipeline.narrative import resolve_page_conflict

            resolutions = body.contradiction_resolutions if body else {}
            wiki_ops.approve_branch(
                _wiki_root(), task["source_id"],
                f"approve: {task['source_id']}", resolutions=resolutions or None,
                resolve_conflict=resolve_page_conflict,
            )
            _apply_resolutions(task, resolutions)
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


def _sources_root() -> Path:
    return wiki_ops.sources_root()


def _source_meta(source_id: str) -> dict:
    p = _sources_root() / source_id / "metadata.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


@router.delete("/ingest/{task_id}/source", dependencies=[Depends(require_admin)])
def delete_source(task_id: str):
    """승인된 소스를 un-ingest: 정식 행·on-disk 원본·태스크·소스전용 위키 페이지를 삭제한다.

    중복 승인 소스 정리(pre-dedup)에도 이 엔드포인트를 쓴다.
    """
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    source_id = task["source_id"]

    counts = db.delete_source(source_id)

    # ponytail: 위키 서사는 여러 소스가 한 페이지로 병합돼 깔끔히 un-merge할 수 없다.
    # 정직한 최선 — 소스 전용 페이지(summaries/{source_id}.md)만 지운다.
    # 공유 tech/entity/synthesis 페이지에 남은 이 소스의 기여분은 제거하지 못한다(수동 편집 필요).
    summary_rel = f"summaries/{source_id}.md"
    summary_deleted = wiki_ops.delete_page(
        _wiki_root(), summary_rel, f"delete source: {source_id}"
    )
    if summary_deleted:  # 더 이상 존재하지 않는 페이지의 Qdrant 포인트만 제거
        try:
            from tasks import unindex_pages

            unindex_pages.delay([summary_rel])
        except Exception:
            pass  # 색인 정리 실패는 삭제를 되돌릴 사유가 아님 — POST /reindex로 복구

    db.delete_task(task_id)

    # on-disk 소스 디렉토리 삭제 (경로가 sources 루트 안인지 확인)
    src_dir = (_sources_root() / source_id).resolve()
    if src_dir.is_relative_to(_sources_root().resolve()) and src_dir.is_dir():
        shutil.rmtree(src_dir)

    return {
        "deleted": counts,
        "summary_page_deleted": summary_deleted,
        "wiki_narrative_remains": True,  # 공유 페이지 기여분은 남아 있음
        "note": ("공유 위키 페이지(tech/entity/synthesis)에 병합된 이 소스의 서술은 "
                 "자동 제거되지 않습니다. 필요하면 PUT /wiki/page로 수동 편집하세요."),
    }


@router.get("/ingest/{task_id}/original", dependencies=[Depends(require_admin)])
def download_original(task_id: str):
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    src_dir = _sources_root() / task["source_id"]
    originals = sorted(src_dir.glob("original.*")) if src_dir.is_dir() else []
    if not originals:
        raise HTTPException(status_code=404, detail="original file not found")
    orig = originals[0]
    title = _source_meta(task["source_id"]).get("title") or task["source_id"]
    return FileResponse(orig, filename=f"{title}{orig.suffix}")


@router.get("/admin/usage", dependencies=[Depends(require_admin)])
def llm_usage():
    """Gemini 토큰 사용량과 비용. 비용은 저장값이 아니라 현재 단가로 환산한 값이다."""
    import cost

    roll = db.usage_rollups()
    by_source = [{**cost.priced(r), "title": _source_meta(r["source_id"]).get("title") or ""}
                 for r in roll["by_source"]]
    by_purpose = [cost.priced(r) for r in roll["by_purpose"]]
    by_model = [cost.priced(r) for r in roll["by_model"]]
    total = sum(r["cost_usd"] or 0 for r in by_model)
    return {
        "since": roll["since"],
        "total_usd": total,
        "total_calls": sum(r["calls"] for r in by_model),
        "by_model": by_model,
        "by_purpose": by_purpose,
        "by_source": sorted(by_source, key=lambda r: r["cost_usd"] or 0, reverse=True),
        "query_side": [cost.priced(r) for r in roll["query_side"]],
        # 단가가 없는 모델은 0원으로 조용히 세지 않고 드러낸다 (no silent caps)
        "unpriced_models": cost.unpriced_models(by_model),
    }


class ResetBody(BaseModel):
    force: bool = False  # 처리 중 태스크가 있어도 강행 (멈춘 태스크로 영구 차단되는 것 방지)


@router.post("/admin/reset", dependencies=[Depends(require_admin)])
def reset_everything(body: ResetBody | None = None):
    """전체 초기화: DB·위키 저장소·업로드 원본·Qdrant 색인을 새 배포 상태로 되돌린다.

    되돌릴 수 없다. 스키마와 ministries 시드 행은 보존한다.
    """
    if not (body and body.force):
        in_flight = db.list_in_flight()
        if in_flight:
            raise HTTPException(status_code=409, detail=(
                f"처리 중인 태스크가 {len(in_flight)}건 있습니다 "
                f"({', '.join(sorted({t['status'] for t in in_flight}))}). "
                "끝나길 기다리거나 force=true로 강행하세요."
            ))
    counts = db.reset_all()
    wiki_ops.reset_repo(_wiki_root())

    sources = _sources_root()
    removed = 0
    if sources.is_dir():
        for p in sources.iterdir():
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            removed += 1

    # Qdrant는 마지막 — 실패해도 POST /reindex로 복구 가능. 모듈 임포트는 가볍다(BGE-M3는 지연 로드).
    import embeddings

    client = embeddings.qdrant()
    if client.collection_exists(embeddings.COLLECTION):
        client.delete_collection(embeddings.COLLECTION)
    embeddings.ensure_collection(client)

    return {"status": "reset", "db": counts, "sources_removed": removed}


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
    publish_date: str = Form(...),
    tags: str = Form(""),
    force: bool = Form(False),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"unsupported format: {ext}")
    if not publish_date.strip():
        # 시점 정합성(연도 기준 병합·모순 판정)의 전제 — 빈 값 차단
        raise HTTPException(status_code=400, detail="publish_date(발행 연도)는 필수입니다")
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
    src_dir = _sources_root() / source_id
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
