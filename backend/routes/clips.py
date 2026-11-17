"""
backend/routes/clips.py
-----------------------
Endpoints for listing and downloading clips:
  GET /api/jobs/{job_id}/clips                - List all clips for a job
  GET /api/clips/{clip_id}/download/landscape - Download landscape 16:9 MP4
  GET /api/clips/{clip_id}/download/portrait  - Download portrait 9:16 MP4
  GET /api/clips/{clip_id}/stream/landscape   - Stream landscape video
  GET /api/clips/{clip_id}/stream/portrait    - Stream portrait video
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Clip, Job

router = APIRouter()


def _clip_to_dict(clip: Clip) -> Dict[str, Any]:
    duration = round(clip.end_sec - clip.start_sec, 2)
    return {
        "id": clip.id,
        "job_id": clip.job_id,
        "title": clip.title,
        "reason": clip.reason,
        "start_sec": clip.start_sec,
        "end_sec": clip.end_sec,
        "duration_sec": duration,
        "landscape_path": clip.landscape_path,
        "portrait_path": clip.portrait_path,
        "created_at": clip.created_at.isoformat() if clip.created_at else None,
        "download_landscape": f"/api/clips/{clip.id}/download/landscape",
        "download_portrait": f"/api/clips/{clip.id}/download/portrait",
        "stream_landscape": f"/api/clips/{clip.id}/stream/landscape",
        "stream_portrait": f"/api/clips/{clip.id}/stream/portrait",
    }


async def _get_clip_or_404(clip_id: str, db: AsyncSession) -> Clip:
    result = await db.execute(select(Clip).where(Clip.id == clip_id))
    clip = result.scalar_one_or_none()
    if clip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Clip '{clip_id}' not found.",
        )
    return clip


@router.get(
    "/jobs/{job_id}/clips",
    summary="List all clips generated for a job",
    response_model=List[Dict[str, Any]],
)
async def list_clips_for_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    # Verify job exists
    job_res = await db.execute(select(Job).where(Job.id == job_id))
    job = job_res.scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    # Fetch clips
    clips_res = await db.execute(
        select(Clip).where(Clip.job_id == job_id).order_by(Clip.start_sec)
    )
    clips = clips_res.scalars().all()
    return [_clip_to_dict(c) for c in clips]


@router.get(
    "/clips/{clip_id}/download/landscape",
    summary="Download the landscape (16:9) MP4 with burned subtitles",
)
async def download_landscape(
    clip_id: str,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    clip = await _get_clip_or_404(clip_id, db)
    if not clip.landscape_path:
        raise HTTPException(status_code=404, detail="Landscape clip not generated.")

    path = Path(clip.landscape_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Landscape file missing on disk.")

    filename = f"{clip.title or 'clip'}_landscape.mp4"
    return FileResponse(
        str(path),
        media_type="video/mp4",
        filename=filename,
    )


@router.get(
    "/clips/{clip_id}/download/portrait",
    summary="Download the 9:16 portrait MP4 with burned subtitles",
)
async def download_portrait(
    clip_id: str,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    clip = await _get_clip_or_404(clip_id, db)
    if not clip.portrait_path:
        raise HTTPException(status_code=404, detail="Portrait clip not available.")

    path = Path(clip.portrait_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Portrait file missing on disk.")

    filename = f"{clip.title or 'clip'}_portrait_9x16.mp4"
    return FileResponse(
        str(path),
        media_type="video/mp4",
        filename=filename,
    )


@router.get("/clips/{clip_id}/stream/landscape")
async def stream_landscape(
    clip_id: str,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    clip = await _get_clip_or_404(clip_id, db)
    if not clip.landscape_path or not Path(clip.landscape_path).exists():
        raise HTTPException(status_code=404, detail="Clip video file not found.")
    return FileResponse(str(clip.landscape_path), media_type="video/mp4")


@router.get("/clips/{clip_id}/stream/portrait")
async def stream_portrait(
    clip_id: str,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    clip = await _get_clip_or_404(clip_id, db)
    if not clip.portrait_path or not Path(clip.portrait_path).exists():
        raise HTTPException(status_code=404, detail="Clip video file not found.")
    return FileResponse(str(clip.portrait_path), media_type="video/mp4")
