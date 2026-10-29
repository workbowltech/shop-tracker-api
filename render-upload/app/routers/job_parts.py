"""Spare parts on a job card, and the workflow between the three teams.

The flow (see the "Workshop Job Card Flows" chart):
  Service engineer diagnoses → adds part lines → (asks customer approval) → sends to Spare parts
  Spare parts: each line → "issue from stock" or "need to buy"
  Procurement: buys the "need to buy" lines → order (dealer, PO, ETA) → receive  (or: no dealer / wrong part)
  Spare parts: issues the received parts
  When every line is issued the job card goes back to the engineer ("Spares ready")
  Engineer repairs → QC check → Completed → Delivered

The job card status follows its part lines automatically while it is in the spares stage:
  any line to buy / ordered      → pending_procurement   (Procurement has it)
  else any line with no dealer   → pending_no_part       (on hold, Procurement follows up)
  else any line requested/received → pending_spares      (Spare parts has it)
  else every line issued         → spares_ready          (back to the engineer)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import current_user, log_activity
from ..models import PART_DONE, JobCard, JobPart, StockItem, User
from ..schemas import FlowIn, JobCardOut, PartActionIn, PartEditIn, PartIn
from .job_cards import _get_job, _set_status, team_of

router = APIRouter(tags=["job card parts"])

SPARES_STAGE = ("pending_spares", "pending_procurement", "pending_no_part", "spares_ready")
CLOSED = ("completed", "returned_unrepaired", "delivered")
WAITING = ("requested", "to_buy", "ordered", "not_available", "received")  # not yet with the engineer

# Who may do each part action (team names; the owner may do everything).
PART_ACTION_TEAMS = {
    "issue": ("parts",), "need_buy": ("parts",), "return_stock": ("parts",),
    "order": ("procurement",), "no_dealer": ("procurement",), "receive": ("procurement",), "reject": ("procurement",),
    "claim_sent": ("procurement",), "claim_ok": ("procurement",), "claim_no": ("procurement",),
    "defective": ("service", "parts"), "decline": ("service", "parts"),
}
# Part status before → after, for each action.
PART_MOVES = {
    "issue": (("requested", "received"), "issued"),
    "need_buy": (("requested",), "to_buy"),
    "return_stock": (("issued", "received"), "returned"),
    "order": (("to_buy", "not_available"), "ordered"),
    "no_dealer": (("to_buy", "ordered"), "not_available"),
    "receive": (("ordered",), "received"),
    "reject": (("ordered", "received"), "to_buy"),
    "defective": (("issued",), "defective"),
    "decline": (("requested", "to_buy", "not_available"), "declined"),
}
CLAIM_MOVES = {"claim_sent": ("to_claim", "claimed"), "claim_ok": ("claimed", "accepted"), "claim_no": ("claimed", "rejected")}
NOTE_NEEDED = ("no_dealer", "reject", "defective")

LABEL = {"pending_spares": "Waiting for spare parts", "pending_procurement": "Buying from dealer",
         "pending_no_part": "Part not available"}


def _clean(t: str) -> str:
    return " ".join(t.split())


def _short(parts: list[JobPart]) -> str:
    return ", ".join(f"{p.name} ×{p.qty}" for p in parts)[:300]


def sync_flow(db: Session, job: JobCard, user: User, force: bool = False) -> None:
    """Move the job card to the status its part lines call for (only while it is in the spares stage)."""
    if not force and job.status not in SPARES_STAGE:
        return
    active = [p for p in job.parts if p.status not in PART_DONE]
    buying = [p for p in active if p.status in ("to_buy", "ordered")]
    missing = [p for p in active if p.status == "not_available"]
    spares = [p for p in active if p.status in ("requested", "received")]
    if buying:
        new, note = "pending_procurement", f"{LABEL['pending_procurement']}: {_short(buying)}"
        etas = sorted(p.eta for p in buying if p.eta)
        if etas and all(p.eta for p in buying):
            note += f" (expected {etas[-1]})"
    elif missing:
        new, note = "pending_no_part", f"{LABEL['pending_no_part']}: {_short(missing)}"
    elif spares:
        new, note = "pending_spares", f"{LABEL['pending_spares']}: {_short(spares)}"
    elif active:
        new, note = "spares_ready", "All spare parts issued"
    else:
        new, note = "in_progress", "No spare parts needed"
    note = note[:300]
    if new != job.status:
        _set_status(db, job, new, user, note)
    elif new.startswith("pending_"):
        job.pending_note = note


def _back_to_stock(db: Session, p: JobPart) -> None:
    """A part taken from the inventory comes back unused: add it to the stock again."""
    if p.stock_item_id and p.status == "issued":
        item = db.get(StockItem, p.stock_item_id)
        if item:
            item.qty += p.qty


def _release_parts(job: JobCard, db: Session | None = None) -> None:
    """Job closed without repair: parts not yet bought are cancelled, parts in hand go back to stock."""
    for p in job.parts:
        if db is not None:
            _back_to_stock(db, p)
        if p.status in ("requested", "to_buy", "not_available"):
            p.status = "cancelled"
        elif p.status == "ordered":
            p.status, p.note = "cancelled", "Job closed – part on order goes to stock when it comes"
        elif p.status in ("received", "issued"):
            p.status = "returned"


def _need_team(user: User, teams: tuple[str, ...]) -> None:
    t = team_of(user)
    if t is not None and t not in teams:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your team cannot do this step.")


def _open_job(db: Session, job_id: int, user: User) -> JobCard:
    job = _get_job(db, job_id, user)
    if job.status == "delivered":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This job is already delivered.")
    return job


def _line(job: JobCard, part_id: int) -> JobPart:
    for p in job.parts:
        if p.id == part_id:
            return p
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Spare part line not found.")


def _done(db: Session, job: JobCard) -> JobCardOut:
    db.commit()
    db.refresh(job)
    return JobCardOut.of(job)


# ---------- part lines ----------
@router.post("/job-cards/{job_id}/parts", response_model=JobCardOut, status_code=201)
def add_part(job_id: int, data: PartIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """The engineer (the job's own team) lists a part the job needs."""
    job = _open_job(db, job_id, user)
    _need_team(user, (job.team,))
    if job.status in CLOSED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This job is closed.")
    during = job.status in ("in_progress", "quality_check") and any(p.status == "issued" for p in job.parts)
    job.parts.append(JobPart(name=data.name, part_no=data.part_no, qty=data.qty, price=data.price, during_repair=during))
    db.flush()
    log_activity(db, user, "add_part", "job_card", job.no, f"{data.name} x{data.qty}")
    sync_flow(db, job, user)  # while in the spares stage a new line goes straight to Spare parts
    return _done(db, job)


@router.patch("/job-cards/{job_id}/parts/{part_id}", response_model=JobCardOut)
def edit_part(job_id: int, part_id: int, data: PartEditIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Service: change a requested line. Spare parts: price / part no. Procurement: alternate part, price."""
    job = _open_job(db, job_id, user)
    p = _line(job, part_id)
    t = team_of(user)
    allowed = {
        None: ("requested", "to_buy", "ordered", "not_available", "received"),
        job.team: ("requested",),
        "parts": ("requested", "received", "to_buy"),
        "procurement": ("to_buy", "ordered", "not_available"),
    }.get(t, ())
    if p.status not in allowed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This part can no longer be changed by your team.")
    changes = []
    for field in ("name", "part_no", "qty", "price"):
        v = getattr(data, field)
        if v is not None and v != getattr(p, field):
            setattr(p, field, _clean(v) if isinstance(v, str) else v)
            changes.append(field)
    if data.note.strip():
        p.note = _clean(data.note)
    log_activity(db, user, "edit_part", "job_card", job.no, f"{p.name}: {', '.join(changes)}")
    sync_flow(db, job, user)
    return _done(db, job)


@router.delete("/job-cards/{job_id}/parts/{part_id}", response_model=JobCardOut)
def delete_part(job_id: int, part_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Remove a line added by mistake (only while it is still just requested)."""
    job = _open_job(db, job_id, user)
    _need_team(user, (job.team,))
    p = _line(job, part_id)
    if p.status != "requested":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only a part that is still requested can be removed.")
    job.parts.remove(p)
    db.flush()
    log_activity(db, user, "remove_part", "job_card", job.no, p.name)
    sync_flow(db, job, user)
    return _done(db, job)


@router.post("/job-cards/{job_id}/parts/{part_id}/action", response_model=JobCardOut)
def part_action(job_id: int, part_id: int, data: PartActionIn, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    job = _open_job(db, job_id, user)
    p = _line(job, part_id)
    a, note = data.action, _clean(data.note)
    teams = tuple(job.team if t == "service" else t for t in PART_ACTION_TEAMS[a])
    _need_team(user, teams)
    if a in NOTE_NEEDED and len(note) < 3:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please write the reason.")
    if job.status in ("returned_unrepaired",) and a not in CLAIM_MOVES and a != "return_stock":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This job is closed.")

    if a in CLAIM_MOVES:
        before, after = CLAIM_MOVES[a]
        if p.claim != before:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This claim is not at that step.")
        p.claim = after
    else:
        before, after = PART_MOVES[a]
        if p.status not in before:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This part is not at that step.")
        if a == "order" and not data.vendor.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please write the dealer name.")
        if a == "issue" and p.status == "requested" and data.stock_item_id:
            # Taken from the inventory: check there is enough, then reduce the stock.
            item = db.get(StockItem, data.stock_item_id)
            if not item:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Inventory item not found.")
            if item.qty < p.qty:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Only {item.qty} of {item.name} in stock. Use “Not in stock – buy”.")
            item.qty -= p.qty
            p.stock_item_id = item.id
            if not p.price:
                p.price = item.price
            if not p.part_no:
                p.part_no = item.part_no
        if a == "return_stock":
            _back_to_stock(db, p)
        p.status = after
        if a == "need_buy":
            p.bought = True
        if a == "order":
            p.vendor, p.po_no, p.eta = _clean(data.vendor), _clean(data.po_no), data.eta
        if a == "issue" and job.warranty:
            p.claim = "to_claim"  # the removed old part goes back to the maker for the warranty claim
        if a == "reject":
            p.po_no, p.eta = "", ""
            if p.claim == "":
                p.claim = "to_claim"  # wrong / damaged part returned to the dealer
        if a == "defective":
            p.claim = "to_claim"
            job.parts.append(JobPart(name=p.name, part_no=p.part_no, qty=p.qty, price=p.price,
                                     during_repair=True, replaces_id=p.id, note="Replacement for a defective part"))
            db.flush()
            if job.status in ("in_progress", "quality_check"):
                sync_flow(db, job, user, force=True)  # straight back to Spare parts for the replacement
    if note:
        p.note = note
    log_activity(db, user, f"part_{a}", "job_card", job.no, f"{p.name}" + (f" ({note})" if note else ""))
    sync_flow(db, job, user)
    return _done(db, job)


# ---------- job card workflow buttons ----------
@router.post("/job-cards/{job_id}/flow", response_model=JobCardOut)
def flow(job_id: int, data: FlowIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    job = _open_job(db, job_id, user)
    a, note, cur = data.action, _clean(data.note), job.status
    _need_team(user, (job.team,))
    requested = [p for p in job.parts if p.status == "requested"]
    waiting = [p for p in job.parts if p.status in WAITING]

    def need(statuses: tuple[str, ...]):
        if cur not in statuses:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This step is not possible at the job's current status.")

    def need_note():
        if len(note) < 3:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please write the reason.")

    if a == "send_spares":
        need(("in_progress", "pending_approval", "quality_check"))
        if not requested:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add the spare parts needed first.")
        if cur == "pending_approval":
            job.approval, job.approval_note = "approved", note or "Approved"
        sync_flow(db, job, user, force=True)
    elif a == "ask_approval":
        need(("in_progress", "quality_check"))
        if not data.estimate:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please enter the estimate amount.")
        job.estimate, job.approval, job.approval_note = data.estimate, "waiting", ""
        _set_status(db, job, "pending_approval", user, f"Estimate ₹{data.estimate:,} sent to customer" + (f" – {note}" if note else ""))
    elif a == "approved":
        need(("pending_approval",))
        job.approval, job.approval_note = "approved", note or "Approved"
        if requested:
            sync_flow(db, job, user, force=True)
        else:
            _set_status(db, job, "in_progress", user, "Customer approved")
    elif a == "declined":
        # Customer does not want the repair → give the device back unrepaired.
        need(("pending_approval",))
        job.approval, job.approval_note = "declined", note
        _release_parts(job, db)
        job.close_reason = "not_approved"
        _set_status(db, job, "returned_unrepaired", user, "Customer did not approve" + (f" – {note}" if note else ""))
    elif a == "declined_extra":
        # Customer does not want the extra parts → finish the work already approved.
        need(("pending_approval",))
        for p in job.parts:
            if p.status == "requested":
                p.status, p.note = "declined", "Customer declined"
        job.approval, job.approval_note = "approved", ("Extra parts declined" + (f" – {note}" if note else ""))[:300]
        sync_flow(db, job, user, force=True)
    elif a == "start_repair":
        need(("spares_ready",))
        _set_status(db, job, "in_progress", user, note)
    elif a == "to_qc":
        need(("in_progress",))
        if waiting:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Some spare parts are still not issued.")
        _set_status(db, job, "quality_check", user, note)
    elif a == "qc_pass":
        need(("quality_check",))
        _set_status(db, job, "completed", user, note or "Checked and OK")
    elif a == "qc_fail":
        need(("quality_check",))
        need_note()
        _set_status(db, job, "in_progress", user, f"Check failed – {note}")
    elif a in ("not_repairable", "cancel"):
        need(tuple(s for s in ("not_assigned", "in_progress", "pending_spares", "pending_procurement", "pending_no_part",
                               "pending_not_found", "pending_technician", "pending_approval", "pending_outsourced",
                               "spares_ready", "quality_check")))
        need_note()
        if a == "cancel":
            job.close_reason, head = "cancelled", "Cancelled by customer"
        else:
            job.close_reason = data.close_reason if data.close_reason in ("ber", "scrapped") else "ber"
            head = "Scrapped with customer consent" if job.close_reason == "scrapped" else "Not worth repairing"
        _release_parts(job, db)
        _set_status(db, job, "returned_unrepaired", user, f"{head} – {note}")
    elif a == "hand_over":
        # "Spare parts only" job: every part is ready → completed (no repair or check needed).
        need(("spares_ready",))
        if job.job_type != "spare_sale":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This job needs a repair; use Start repair.")
        _set_status(db, job, "completed", user, note or "Spare parts ready for the customer")
    elif a == "deliver":
        need(("completed", "returned_unrepaired"))
        _set_status(db, job, "delivered", user, note)
    log_activity(db, user, f"flow_{a}", "job_card", job.no, note)
    return _done(db, job)
