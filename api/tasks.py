"""Celery 앱과 인제스트 태스크. 워커 실행: celery -A tasks worker"""
import os
from pathlib import Path

from celery import Celery

from app import db

celery = Celery("nst_wiki", broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))


@celery.task(name="ingest.run", time_limit=3600)  # 대형 스캔본 OCR 감안 (실측: 14MB 문서)
def run_ingest(task_id: str) -> None:
    task = db.get_task(task_id)
    db.set_status(task_id, "parsing")
    try:
        from pipeline.compile import compile_source
        from pipeline.parse import run_pipeline

        source_dir = Path(os.environ.get("SOURCES_PATH", "/data/sources")) / task["source_id"]
        run_pipeline(source_dir)
        db.set_status(task_id, "classifying")
        wiki_root = Path(os.environ.get("WIKI_REPO_PATH", "/data/wiki"))
        results = compile_source(source_dir, task["source_id"], wiki_root)
        db.save_results(task_id, results)
        db.set_status(task_id, "staged")
    except Exception as e:
        db.set_status(task_id, "failed", error=str(e))
        raise


@celery.task(name="embed.pages", time_limit=1800)
def embed_pages(paths: list[str]) -> int:
    import embeddings
    import wiki_ops

    root = Path(os.environ.get("WIKI_REPO_PATH", "/data/wiki"))
    client = embeddings.qdrant()
    embeddings.ensure_collection(client)
    n = 0
    for p in paths:
        text = wiki_ops.read_page(root, p)
        if text is not None:
            n += embeddings.index_page(client, p, text)
    return n


@celery.task(name="embed.unindex")
def unindex_pages(paths: list[str]) -> int:
    """삭제된 페이지의 Qdrant 포인트를 제거한다 (소스 삭제 시). 모델 로드 불필요 — 삭제만."""
    import embeddings

    client = embeddings.qdrant()
    for p in paths:
        embeddings.delete_page(client, p)
    return len(paths)


@celery.task(name="embed.reindex", time_limit=3600)
def reindex_all() -> int:
    import wiki_ops

    root = Path(os.environ.get("WIKI_REPO_PATH", "/data/wiki"))
    return embed_pages(wiki_ops.list_pages(root))
