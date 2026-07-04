"""Celery 앱과 인제스트 태스크. 워커 실행: celery -A tasks worker"""
import os
from pathlib import Path

from celery import Celery

from app import db

celery = Celery("nst_wiki", broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))


@celery.task(name="ingest.run", time_limit=1800)
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
