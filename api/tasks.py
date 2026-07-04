"""Celery 앱과 인제스트 태스크. 워커 실행: celery -A tasks worker"""
import os
from pathlib import Path

from celery import Celery

from app import db

celery = Celery("nst_wiki", broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))


@celery.task(name="ingest.run")
def run_ingest(task_id: str) -> None:
    task = db.get_task(task_id)
    db.set_status(task_id, "parsing")
    try:
        from pipeline.parse import run_pipeline

        source_dir = Path(os.environ.get("SOURCES_PATH", "/data/sources")) / task["source_id"]
        run_pipeline(source_dir)
        db.set_status(task_id, "parsed")
    except Exception as e:
        db.set_status(task_id, "failed", error=str(e))
        raise
