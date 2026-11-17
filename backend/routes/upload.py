"""
backend/routes/upload.py
------------------------
POST /api/upload - Accepts video file, stores it, creates Job,
and dispatches background processing.
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.database import get_db
from backend.models import Job
from backend.tasks.pipeline_runner import execute_pipeline

logger = logging.getLogger(__name__)

router = APIRouter()

# Thread pool for in-process background task execution
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="clip_worker")

ALLOWED_EXTENSIONS: set[str] = {
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".flv", ".wmv",
}

# 4 GB max upload
MAX_UPLOAD_BYTES: int = 4 * 1024 * 1024 * 1024


def _validate_video_file(file: UploadFile) -> None:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported file extension '{suffix}'. "
                f"Supported formats: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            ),
        )


async def _save_upload(file: UploadFile, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    chunk_size = 1024 * 1024  # 1 MiB chunks

    async with aiofiles.open(dest, "wb") as out_file:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            bytes_written += len(chunk)
            if bytes_written > MAX_UPLOAD_BYTES:
                await out_file.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Upload exceeds maximum limit of {MAX_UPLOAD_BYTES // (1024**3)} GB.",
                )
            await out_file.write(chunk)

    return bytes_written


@router.post(
    "/upload",
    summary="Upload a video file and start generating clips",
    status_code=status.HTTP_200_OK,
)
async def upload_video(
    file: UploadFile = File(..., description="Video file to process"),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    settings = get_settings()

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No filename provided with upload.",
        )

    _validate_video_file(file)

    job_id = str(uuid.uuid4())
    original_name = Path(file.filename).name
    suffix = Path(original_name).suffix.lower()

    upload_dir = settings.uploads_dir / job_id
    dest_path = upload_dir / f"original{suffix}"

    try:
        bytes_written = await _save_upload(file, dest_path)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to save upload for job %s: %s", job_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save uploaded video.",
        ) from exc

    # Insert Job record
    now = datetime.now(timezone.utc)
    job = Job(
        id=job_id,
        filename=original_name,
        video_path=str(dest_path),
        status="queued",
        error=None,
        created_at=now,
        updated_at=now,
    )

    try:
        db.add(job)
        await db.commit()
    except Exception as exc:
        logger.exception("Failed to insert Job %s into DB: %s", job_id, exc)
        dest_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error creating job.",
        ) from exc

    # Dispatch background task
    dispatched = False
    if settings.use_celery:
        try:
            from backend.tasks.celery_tasks import process_video
            process_video.apply_async(args=[job_id, str(dest_path)], task_id=f"job_{job_id}")
            dispatched = True
            logger.info("Enqueued job %s with Celery.", job_id)
        except Exception as cel_err:
            logger.warning("Celery enqueue failed (%s). Falling back to background thread.", cel_err)

    if not dispatched:
        # Run in thread pool executor
        _executor.submit(execute_pipeline, job_id, str(dest_path))
        logger.info("Dispatched job %s to in-process background worker thread.", job_id)

    return JSONResponse(
        content={
            "job_id": job_id,
            "status": "queued",
            "filename": original_name,
            "size_bytes": bytes_written,
        },
        status_code=status.HTTP_200_OK,
    )
