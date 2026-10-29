"""Photos on a job card ("before" when the device comes in, "after" the repair).

The app shrinks each photo before sending it, so they are kept in the database itself
(no separate file storage to set up). Anyone who can open the job card can see and add photos;
a photo can be removed by the person who added it or by the owner, until the job is delivered.
"""
import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from ..database import get_db
from ..deps import current_user, log_activity
from ..models import MAX_PHOTO_BYTES, MAX_PHOTOS_PER_JOB, JobPhoto, User
from ..schemas import JobCardOut, PhotoIn
from .job_cards import _get_job

router = APIRouter(tags=["job card photos"])

# First bytes of each allowed image type, so only real photos are stored.
_MAGIC = {"image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG",), "image/webp": (b"RIFF",)}


@router.post("/job-cards/{job_id}/photos", response_model=JobCardOut, status_code=201)
def add_photo(job_id: int, data: PhotoIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    job = _get_job(db, job_id, user)
    if job.status == "delivered":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This job is already delivered.")
    if len(job.photos) >= MAX_PHOTOS_PER_JOB:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"A job card can have up to {MAX_PHOTOS_PER_JOB} photos.")
    try:
        raw = base64.b64decode(data.data.split(",", 1)[-1], validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The photo could not be read.")
    if len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The photo is too large.")
    if not raw.startswith(_MAGIC[data.content_type]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This file is not a photo.")
    job.photos.append(JobPhoto(kind=data.kind, content_type=data.content_type, size=len(raw), data=raw, user_id=user.id))
    log_activity(db, user, "add_photo", "job_card", job.no, data.kind)
    db.commit()
    db.refresh(job)
    return JobCardOut.of(job)


@router.get("/job-cards/{job_id}/photos/{photo_id}")
def get_photo(job_id: int, photo_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _get_job(db, job_id, user)
    photo = db.scalar(select(JobPhoto).options(undefer(JobPhoto.data))
                      .where(JobPhoto.id == photo_id, JobPhoto.job_card_id == job_id))
    if not photo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo not found.")
    return Response(content=photo.data, media_type=photo.content_type,
                    headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/job-cards/{job_id}/photos/{photo_id}", response_model=JobCardOut)
def delete_photo(job_id: int, photo_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    job = _get_job(db, job_id, user)
    if job.status == "delivered":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This job is already delivered.")
    photo = next((p for p in job.photos if p.id == photo_id), None)
    if not photo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo not found.")
    if user.role != "owner" and photo.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the person who added the photo (or the owner) can remove it.")
    job.photos.remove(photo)
    log_activity(db, user, "remove_photo", "job_card", job.no, photo.kind)
    db.commit()
    db.refresh(job)
    return JobCardOut.of(job)
