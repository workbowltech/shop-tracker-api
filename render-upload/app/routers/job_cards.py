"""Job cards for the three teams (Service, Spare parts, Procurement).

Rules
- Only the Service team and the owner open job cards, and every new job card belongs to the Service team.
  The Spare parts and Procurement teams have no job cards of their own: they work only on the spare part
  lines the engineer asks for (see job_parts.py). Older job cards of the Spare parts team still open.
- Staff work on their own team's job cards. The Spare parts and Procurement teams also see
  any job card that has spare part lines for them (see job_parts.py for the parts workflow).
- Status flow:  not_assigned → in_progress ⇄ pending_… → completed → delivered
  * a job leaves "not_assigned" when a team member is assigned (it then becomes "in_progress")
  * every "pending_…" status needs a reason note
  * Service jobs: repair → quality_check → completed (the QC step cannot be skipped)
  * statuses of the parts workflow (pending_spares, pending_procurement, spares_ready …) are set by
    the workflow buttons, not by hand, on Service job cards
  * "delivered" only after "completed" or "returned_unrepaired"; a delivered job is locked
    (only the owner can reopen it)
- Every status change is kept in the job's history (who, when, note).
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import current_user, log_activity, require_roles
from ..models import (
    FLOW_STATUSES, JOB_STATUSES, PENDING_STATUSES, TEAM_OF_ROLE, TEAMS,
    Customer, JobCard, JobComplaint, JobCounter, JobPart, JobStatusLog, User,
)
from ..schemas import AssignIn, CustomerIn, CustomerListOut, CustomerOut, JobCardIn, JobCardOut, StatusIn, WorkIn

router = APIRouter(tags=["job cards"])

REWORK_DAYS = 30  # a repeat complaint within this many days of delivery is repaired free (warranty)
PROCUREMENT_STATUSES = ("pending_procurement", "pending_no_part")


def now() -> datetime:
    return datetime.now(timezone.utc)


def next_job_no(db: Session, when: datetime | None = None) -> str:
    """YYMM-0001, YYMM-0002 … — restarts at 0001 every month (India time). Made only on the server."""
    local = (when or now()).astimezone(ZoneInfo(settings.timezone))
    prefix = local.strftime("%y%m")
    counter = db.scalar(select(JobCounter).where(JobCounter.prefix == prefix).with_for_update())
    if counter is None:
        counter = JobCounter(prefix=prefix, last=0)
        db.add(counter)
        db.flush()
    # Never reuse a number that already exists (e.g. job cards added before this counter was made).
    highest = db.scalar(select(func.max(JobCard.no)).where(JobCard.no.like(f"{prefix}-%")))
    if highest:
        counter.last = max(counter.last, int(highest.split("-")[1]))
    counter.last += 1
    return f"{prefix}-{counter.last:04d}"


def team_of(user: User) -> str | None:
    """None = owner (all teams)."""
    return None if user.role == "owner" else TEAM_OF_ROLE.get(user.role)


def can_see(job: JobCard, team: str | None) -> bool:
    """Owner: all. Service: its own job cards. Spare parts: its own + any with part lines.
    Procurement: any job card waiting on a purchase or that ever had a part bought / claimed."""
    if team is None or job.team == team:
        return True
    if team == "parts":
        return bool(job.parts) or job.status == "pending_spares"
    if team == "procurement":
        return job.status in PROCUREMENT_STATUSES or any(p.bought or p.claim for p in job.parts)
    return False


def visible_filter(team: str):
    """The same rule as can_see(), as a database filter (for the list)."""
    has_parts = exists().where(JobPart.job_card_id == JobCard.id)
    if team == "parts":
        return or_(JobCard.team == "parts", JobCard.status == "pending_spares", has_parts)
    if team == "procurement":
        bought = exists().where(and_(JobPart.job_card_id == JobCard.id, or_(JobPart.bought.is_(True), JobPart.claim != "")))
        return or_(JobCard.team == "procurement", JobCard.status.in_(PROCUREMENT_STATUSES), bought)
    return JobCard.team == team


def _get_job(db: Session, job_id: int, user: User, home_only: bool = False) -> JobCard:
    """home_only: only the job's own team (and the owner) may do this, e.g. assign or write work done."""
    job = db.get(JobCard, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job card not found.")
    team = team_of(user)
    if not can_see(job, team):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This job card belongs to another team.")
    if home_only and team is not None and job.team != team:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the job's own team can do this.")
    return job


def _set_status(db: Session, job: JobCard, new: str, user: User, note: str = "") -> None:
    old = job.status
    job.status = new
    job.pending_note = note if new in PENDING_STATUSES else ""
    t = now()
    if new == "in_progress" and job.started_at is None:
        job.started_at = t
    if new == "completed":
        job.done_at = t
    if new == "delivered":
        job.delivered_at = t
    if old == "delivered":
        job.delivered_at = None
    db.add(JobStatusLog(job_card_id=job.id, from_status=old, to_status=new, note=note, user_id=user.id))
    log_activity(db, user, "status", "job_card", job.no, f"{old} -> {new}" + (f" ({note})" if note else ""))


@router.get("/customers", response_model=list[CustomerListOut])
def list_customers(
    q: str | None = Query(default=None, max_length=60),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("owner")),
):
    """All customers with how many job cards they have and their last visit (owner only)."""
    stmt = (
        select(Customer, func.count(JobCard.id), func.max(JobCard.opened_at))
        .outerjoin(JobCard, JobCard.customer_id == Customer.id)
        .group_by(Customer.id)
        .order_by(func.max(JobCard.opened_at).desc().nulls_last(), Customer.name)
    )
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Customer.name).like(like), Customer.phone.like(like),
                              func.lower(Customer.village).like(like), func.lower(Customer.block).like(like)))
    return [
        CustomerListOut(id=c.id, phone=c.phone, name=c.name, district=c.district, block=c.block, village=c.village,
                        jobs=n, last_visit=last, created_at=c.created_at)
        for c, n, last in db.execute(stmt).all()
    ]


@router.post("/customers", response_model=CustomerListOut, status_code=201)
def create_customer(data: CustomerIn, db: Session = Depends(get_db), user: User = Depends(require_roles("owner"))):
    """Add a customer without a job card (owner only). A mobile number can be used only once."""
    if db.scalar(select(Customer).where(Customer.phone == data.phone)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A customer with this mobile number already exists.")
    c = Customer(phone=data.phone, name=data.name, district=data.district, block=data.block, village=data.village)
    db.add(c)
    db.flush()
    log_activity(db, user, "create", "customer", c.id, c.name)
    db.commit()
    return CustomerListOut(id=c.id, phone=c.phone, name=c.name, district=c.district, block=c.block, village=c.village,
                           jobs=0, last_visit=None, created_at=c.created_at)


@router.get("/customers/by-phone/{phone}", response_model=CustomerOut)
def customer_by_phone(phone: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    c = db.scalar(select(Customer).where(Customer.phone == phone))
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "New customer.")
    return c


@router.get("/job-cards", response_model=list[JobCardOut])
def list_job_cards(
    team: str | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, max_length=60),
    limit: int = Query(default=300, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    stmt = select(JobCard).join(Customer)
    my_team = team_of(user)
    if my_team is not None:
        team = my_team  # staff only ever see their own team
    if team:
        if team not in TEAMS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown team.")
        stmt = stmt.where(visible_filter(team))
    if status_:
        if status_ not in JOB_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown status.")
        stmt = stmt.where(JobCard.status == status_)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(
            func.lower(Customer.name).like(like), Customer.phone.like(like), func.lower(Customer.village).like(like),
            func.lower(JobCard.product_model).like(like), func.lower(JobCard.product_serial).like(like), JobCard.no.like(like),
        ))
    stmt = stmt.order_by((JobCard.status == "delivered"), JobCard.opened_at.desc()).limit(limit)
    return [JobCardOut.of(j) for j in db.scalars(stmt).unique().all()]


@router.get("/job-cards/next-number")
def peek_next_number(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """The number the next job card will most likely get (shown on the form; the real one is given on save)."""
    prefix = now().astimezone(ZoneInfo(settings.timezone)).strftime("%y%m")
    counter = db.get(JobCounter, prefix)
    highest = db.scalar(select(func.max(JobCard.no)).where(JobCard.no.like(f"{prefix}-%")))
    last = max(counter.last if counter else 0, int(highest.split("-")[1]) if highest else 0)
    return {"no": f"{prefix}-{last + 1:04d}"}


@router.post("/job-cards", response_model=JobCardOut, status_code=201)
def create_job_card(data: JobCardIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if team_of(user) not in (None, "service"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the Service team and the owner can open job cards.")
    team = "service"  # every new job card belongs to the Service team
    # "Spare parts only": no repair, the listed parts go straight to the Spare parts team.
    job_type = "spare_sale" if data.parts_only else "service"
    if data.parts_only and not data.parts:
        raise HTTPException(422, "Add the spare parts the customer needs.")
    if not data.parts_only and not data.accessories.strip():
        raise HTTPException(422, "Attached accessories are required for a service job.")
    warranty, warranty_note, rework_of = data.warranty, data.warranty_note, ""
    if data.rework_of:
        prev = db.scalar(select(JobCard).where(JobCard.no == data.rework_of.strip()))
        if not prev or prev.customer_phone != data.customer.phone:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Earlier job card not found for this customer's mobile number.")
        rework_of = prev.no
        delivered = prev.delivered_at
        if delivered is not None and delivered.tzinfo is None:
            delivered = delivered.replace(tzinfo=timezone.utc)
        if delivered and now() - delivered <= timedelta(days=REWORK_DAYS):
            warranty = True  # same complaint soon after delivery → free rework
            warranty_note = warranty_note or f"Rework of {prev.no}"
    for attempt in range(3):
        try:
            c = db.scalar(select(Customer).where(Customer.phone == data.customer.phone))
            if c is None:
                c = Customer(phone=data.customer.phone)
                db.add(c)
            c.name, c.block, c.village = data.customer.name, data.customer.block, data.customer.village
            if data.customer.district:
                c.district = data.customer.district
            db.flush()
            job = JobCard(
                no=next_job_no(db), customer_id=c.id, created_by=user.id,
                customer_name=c.name, customer_phone=c.phone, customer_district=c.district,
                customer_block=c.block, customer_village=c.village,
                job_type=job_type, team=team, status="not_assigned",
                product_category=data.product.category, product_model=data.product.model,
                product_serial=data.product.serial.strip().upper(), accessories=data.accessories.strip(),
                warranty=warranty, warranty_note=warranty_note, rework_of=rework_of,
            )
            job.complaints = [JobComplaint(position=i + 1, text=t) for i, t in enumerate(data.complaints)]
            db.add(job)
            db.flush()
            db.add(JobStatusLog(job_card_id=job.id, from_status="", to_status="not_assigned", note="", user_id=user.id))
            log_activity(db, user, "create", "job_card", job.no, f"{job.job_type} -> {job.team}")
            if data.parts_only:
                from .job_parts import sync_flow  # (imported here: job_parts imports this module)
                if user.role != "owner":
                    job.mechanic_id = user.id  # the person who opened it hands the parts over later
                    _set_status(db, job, "in_progress", user, f"Assigned to {user.name}")
                job.parts = [JobPart(name=p.name, part_no=p.part_no, qty=p.qty, price=p.price) for p in data.parts]
                db.flush()
                sync_flow(db, job, user, force=True)  # → Pending for spares (Spare parts team)
            db.commit()
            db.refresh(job)
            return JobCardOut.of(job)
        except IntegrityError:
            db.rollback()  # two job cards at the same moment → try the next number
            if attempt == 2:
                raise HTTPException(status.HTTP_409_CONFLICT, "Could not save. Please try again.")


@router.get("/job-cards/{job_id}", response_model=JobCardOut)
def get_job_card(job_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return JobCardOut.of(_get_job(db, job_id, user))


@router.post("/job-cards/{job_id}/status", response_model=JobCardOut)
def change_status(job_id: int, data: StatusIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    job = _get_job(db, job_id, user)
    cur, new, note = job.status, data.status, " ".join(data.note.split())
    if new == cur and new not in PENDING_STATUSES:
        return JobCardOut.of(job)
    if cur == "delivered" and user.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This job is delivered and locked. Only the owner can reopen it.")
    if new == "not_assigned":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A job cannot go back to Not Assigned.")
    if new in PENDING_STATUSES and len(note) < 3:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please write the reason for this pending status.")
    if new == "delivered" and cur not in ("completed", "returned_unrepaired"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mark the job Completed before Delivered.")
    if team_of(user) is not None and job.team != team_of(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the job's own team can change its status.")
    if job.team == "service" and user.role != "owner":
        # The parts workflow moves these by itself; staff use the workflow buttons.
        if new in FLOW_STATUSES or (cur in FLOW_STATUSES and cur not in ("pending_approval", "quality_check") and new != "delivered"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Use the job card buttons for spare parts, approval and checking.")
        if new == "completed" and cur != "quality_check":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Send the job for checking (QC) before Completed.")
    if cur == "not_assigned" and job.mechanic_id is None:
        if user.role == "owner":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Assign a staff member first.")
        job.mechanic_id = user.id  # the staff member who starts it takes it
    _set_status(db, job, new, user, note)
    db.commit()
    db.refresh(job)
    return JobCardOut.of(job)


@router.post("/job-cards/{job_id}/assign", response_model=JobCardOut)
def assign(job_id: int, data: AssignIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    job = _get_job(db, job_id, user, home_only=True)
    if job.status == "delivered":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This job is already delivered.")
    person = db.get(User, data.user_id)
    if not person or not person.is_active or TEAM_OF_ROLE.get(person.role) != job.team:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose an active member of this job's team.")
    job.mechanic_id = person.id
    log_activity(db, user, "assign", "job_card", job.no, person.name)
    if job.status == "not_assigned":
        _set_status(db, job, "in_progress", user, f"Assigned to {person.name}")
    db.commit()
    db.refresh(job)
    return JobCardOut.of(job)


@router.patch("/job-cards/{job_id}/work", response_model=JobCardOut)
def update_work(job_id: int, data: WorkIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    job = _get_job(db, job_id, user, home_only=True)
    if job.status == "delivered":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This job is already delivered.")
    if data.work_done is not None:
        job.work_done = data.work_done.strip()
    if data.labour is not None:
        job.labour = data.labour
    if data.warranty is not None and data.warranty != job.warranty:
        job.warranty = data.warranty
        log_activity(db, user, "warranty", "job_card", job.no, "on" if data.warranty else "off")
    if data.warranty_note is not None:
        job.warranty_note = " ".join(data.warranty_note.split())
    log_activity(db, user, "work", "job_card", job.no)
    db.commit()
    db.refresh(job)
    return JobCardOut.of(job)
