"""
backend/tasks/pipeline_runner.py
--------------------------------
Core pipeline executor for ClipGenerator.
Can be run via:
  1. Celery task
  2. Asyncio / ThreadPool background task (no Redis/Celery required!)
"""

from __future__ import annotations

import logging
import re
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from backend.config import get_settings
from backend.tasks.analyze import analyze_transcript
from backend.tasks.clip import cut_clip, generate_portrait_crop
from backend.tasks.subtitle import burn_subtitles
from backend.tasks.transcribe import transcribe_video

logger = logging.getLogger(__name__)


def _sync_engine():
    """Return synchronous SQLAlchemy engine for background tasks."""
    from sqlalchemy import create_engine
    settings = get_settings()
    db_url = settings.database_url
    if db_url.startswith("sqlite+aiosqlite"):
        db_url = db_url.replace("sqlite+aiosqlite", "sqlite", 1)
    return create_engine(db_url, connect_args={"check_same_thread": False})


def update_job_status(job_id: str, status: str, error: str | None = None) -> None:
    """Synchronously update Job status and error message in SQLite."""
    from sqlalchemy.orm import Session
    from backend.models import Job

    engine = _sync_engine()
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None:
            logger.error("Job %s not found in DB - cannot update status.", job_id)
            return
        job.status = status
        if error is not None:
            job.error = error
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        logger.info("Job %s -> status=%s", job_id, status)


def save_clip_to_db(
    *,
    job_id: str,
    title: str,
    reason: str,
    start_sec: float,
    end_sec: float,
    landscape_path: str,
    portrait_path: str,
) -> str:
    """Insert a Clip row into SQLite and return its clip_id."""
    from sqlalchemy.orm import Session
    from backend.models import Clip

    clip_id = str(uuid.uuid4())
    engine = _sync_engine()
    with Session(engine) as session:
        clip = Clip(
            id=clip_id,
            job_id=job_id,
            title=title,
            reason=reason,
            start_sec=start_sec,
            end_sec=end_sec,
            landscape_path=landscape_path,
            portrait_path=portrait_path,
            created_at=datetime.now(timezone.utc),
        )
        session.add(clip)
        session.commit()
        logger.info("Saved Clip %s for Job %s [%.1fs - %.1fs]", clip_id, job_id, start_sec, end_sec)
    return clip_id


def _safe_filename(text: str, max_len: int = 40) -> str:
    """Convert text to safe filename slug."""
    slug = re.sub(r"[^\w\s-]", "", text)
    slug = re.sub(r"[\s]+", "_", slug).strip("_-")
    return slug[:max_len] or "clip"


def _filter_words(words: List[dict], start: float, end: float) -> List[dict]:
    """Extract words within the [start, end] window."""
    result = []
    for w in words:
        w_start = float(w.get("start", 0))
        w_end = float(w.get("end", w_start))
        mid = (w_start + w_end) / 2.0
        if start <= mid <= end:
            result.append(w)
    return result


def execute_pipeline(job_id: str, video_path: str) -> dict:
    """
    Execute full end-to-end pipeline:
      1. Transcription with word timestamps
      2. AI Content Analysis & Segment Selection
      3. Video Clipping, Subtitle Burning & 9:16 Portrait Generation
      4. Database persistence
    """
    settings = get_settings()
    video = Path(video_path)
    job_dir = Path(settings.outputs_dir) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=== START PIPELINE for job=%s file=%s ===", job_id, video)

    if not video.exists():
        err_msg = f"Video file not found on disk: {video}"
        logger.error(err_msg)
        update_job_status(job_id, "error", error=err_msg)
        return {"job_id": job_id, "status": "error", "clips_created": 0}

    # ── Stage 1: Transcription ──────────────────────────────────────────
    try:
        update_job_status(job_id, "transcribing")
        words, transcript_text = transcribe_video(job_id, str(video))
        logger.info("[%s] Transcribed %d words.", job_id, len(words))
    except Exception as exc:
        err_msg = f"Transcription failed: {exc}"
        logger.error("[%s] %s\n%s", job_id, err_msg, traceback.format_exc())
        update_job_status(job_id, "error", error=err_msg)
        return {"job_id": job_id, "status": "error", "clips_created": 0}

    # ── Stage 2: AI Analysis ───────────────────────────────────────────
    try:
        update_job_status(job_id, "analyzing")
        duration = words[-1]["end"] if words else 0.0
        segments = analyze_transcript(transcript_text, duration)
        logger.info("[%s] AI returned %d clip segments.", job_id, len(segments))

        if not segments:
            logger.warning("[%s] No clip segments identified.", job_id)
            update_job_status(job_id, "done")
            return {"job_id": job_id, "status": "done", "clips_created": 0}

    except Exception as exc:
        err_msg = f"Analysis failed: {exc}"
        logger.error("[%s] %s\n%s", job_id, err_msg, traceback.format_exc())
        update_job_status(job_id, "error", error=err_msg)
        return {"job_id": job_id, "status": "error", "clips_created": 0}

    # ── Stage 3: Clipping & Formatting ──────────────────────────────────
    try:
        update_job_status(job_id, "clipping")
        clips_created = 0

        for idx, seg in enumerate(segments, start=1):
            s_start = float(seg["start_seconds"])
            s_end = float(seg["end_seconds"])
            title = seg.get("title", f"Clip {idx}")
            reason = seg.get("reason", "")
            slug = f"{idx:02d}_{_safe_filename(title)}"

            logger.info("[%s] Processing clip %d/%d '%s' (%.1fs - %.1fs)", job_id, idx, len(segments), title, s_start, s_end)

            # 3a. Cut raw segment
            raw_clip = job_dir / f"{slug}_raw.mp4"
            try:
                cut_clip(str(video), s_start, s_end, str(raw_clip))
            except Exception as e:
                logger.error("[%s] Failed to cut clip %d: %s", job_id, idx, e)
                continue

            # 3b. Burn subtitles -> landscape MP4
            landscape_path = job_dir / f"{slug}_landscape.mp4"
            clip_words = _filter_words(words, s_start, s_end)
            try:
                burn_subtitles(str(raw_clip), clip_words, s_start, str(landscape_path))
            except Exception as e:
                logger.warning("[%s] Subtitle burn failed for clip %d: %s", job_id, idx, e)
                if raw_clip.exists():
                    raw_clip.rename(landscape_path)

            # Clean up raw clip
            if raw_clip.exists():
                try:
                    raw_clip.unlink()
                except OSError:
                    pass

            # 3c. Generate 9:16 portrait video for Reels / Shorts
            portrait_path = job_dir / f"{slug}_portrait.mp4"
            try:
                generate_portrait_crop(str(landscape_path), str(portrait_path))
            except Exception as e:
                logger.warning("[%s] Portrait conversion failed for clip %d: %s", job_id, idx, e)
                portrait_path = Path("")

            # 3d. Save clip record to DB
            save_clip_to_db(
                job_id=job_id,
                title=title,
                reason=reason,
                start_sec=s_start,
                end_sec=s_end,
                landscape_path=str(landscape_path),
                portrait_path=str(portrait_path) if portrait_path.name else "",
            )
            clips_created += 1

        update_job_status(job_id, "done")
        logger.info("=== FINISHED PIPELINE for job=%s: %d clips created ===", job_id, clips_created)
        return {"job_id": job_id, "status": "done", "clips_created": clips_created}

    except Exception as exc:
        err_msg = f"Clipping failed: {exc}"
        logger.error("[%s] %s\n%s", job_id, err_msg, traceback.format_exc())
        update_job_status(job_id, "error", error=err_msg)
        return {"job_id": job_id, "status": "error", "clips_created": 0}
