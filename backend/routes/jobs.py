"""
backend/routes/jobs.py
----------------------
GET /api/jobs/{job_id} - Fetch job status and details
GET /api/jobs          - List all tracked jobs
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Job

router = APIRouter()


@router.get(
    "/jobs/{job_id}",
    summary="Get status and details for a specific job",
    response_model=Dict[str, Any],
)
async def get_job_status(
    job_id: str,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    stmt = select(Job).where(Job.id == job_id)
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    return {
        "job_id": job.id,
        "filename": job.filename,
        "status": job.status,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


@router.get(
    "/jobs",
    summary="List all jobs ordered by newest first",
    response_model=List[Dict[str, Any]],
)
async def list_jobs(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    stmt = select(Job).order_by(desc(Job.created_at)).limit(limit)
    result = await db.execute(stmt)
    jobs = result.scalars().all()

    return [
        {
            "job_id": j.id,
            "filename": j.filename,
            "status": j.status,
            "error": j.error,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        }
        for j in jobs
    ]
