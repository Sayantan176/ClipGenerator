"""
backend/tasks/celery_tasks.py
-----------------------------
Celery task definition for ClipGenerator.
Delegates to execute_pipeline.
"""

from __future__ import annotations

import logging
from celery import Celery
from celery.utils.log import get_task_logger

from backend.config import get_settings
from backend.tasks.pipeline_runner import execute_pipeline

settings = get_settings()

celery_app = Celery(
    "clipgenerator",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

logger = get_task_logger(__name__)


@celery_app.task(
    bind=True,
    name="backend.tasks.celery_tasks.process_video",
    max_retries=0,
    time_limit=7200,
)
def process_video(self, job_id: str, video_path: str) -> dict:
    """Execute video clipping pipeline via Celery."""
    return execute_pipeline(job_id, video_path)
