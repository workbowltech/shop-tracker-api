"""Database tables. They are created automatically the first time the server starts."""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

ROLES = ("owner", "advisor", "mechanic", "cashier", "sales", "parts", "procurement")
SERVICE_ROLES = ("owner", "advisor", "mechanic", "cashier")

# Three teams. Each staff role belongs to one team; the owner sees all.
TEAMS = ("service", "parts", "procurement")
TEAM_OF_ROLE = {"advisor": "service", "mechanic": "service", "cashier": "service", "sales": "service", "parts": "parts",
                "procurement": "procurement"}

# What the customer came for → which team gets the job card.
JOB_TYPES = ("service", "spare_sale", "spare_replace")
TEAM_OF_JOB_TYPE = {"service": "service", "spare_sale": "parts", "spare_replace": "parts"}

JOB_STATUSES = (
    "not_assigned",
    "in_progress",
    "pending_spares",        # waiting for the Spare parts team
    "pending_procurement",   # a part is being bought from a dealer
    "pending_no_part",       # no dealer has the part (on hold)
    "pending_not_found",
    "pending_technician",
    "pending_approval",
    "pending_outsourced",
    "spares_ready",          # every part issued → back to the engineer
    "quality_check",         # repair done, waiting for test / check
    "completed",
    "returned_unrepaired",   # estimate rejected, not worth repairing, or cancelled
    "delivered",
)
PENDING_STATUSES = tuple(s for s in JOB_STATUSES if s.startswith("pending_"))
# Statuses the parts workflow sets by itself (not chosen by hand on a Service job card).
FLOW_STATUSES = ("pending_spares", "pending_procurement", "pending_no_part", "pending_approval", "spares_ready",
                 "quality_check", "returned_unrepaired")
# Which team has the job card in its hands at each status (otherwise the job's own team).
HOLDER_OF_STATUS = {"pending_spares": "parts", "pending_procurement": "procurement", "pending_no_part": "procurement"}

# Spare part lines on a job card.
PART_STATUSES = (
    "requested",      # engineer asked for it
    "to_buy",         # not in stock → Procurement
    "ordered",        # PO given to a dealer
    "not_available",  # no dealer has it
    "received",       # arrived in the shop (GRN ok) → Spare parts issues it
    "issued",         # given to the engineer
    "returned",       # unused, back to stock
    "defective",      # fitted / tested and failed → replacement line made
    "declined",       # customer did not approve this part
    "cancelled",      # no longer needed (job closed unrepaired)
)
PART_DONE = ("returned", "defective", "declined", "cancelled")  # not counted any more
CLAIM_STATUSES = ("", "to_claim", "claimed", "accepted", "rejected")
CLOSE_REASONS = ("", "not_approved", "ber", "scrapped", "cancelled")
APPROVAL_STATUSES = ("", "waiting", "approved", "declined")


def now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=1)  # +1 = logs the user out everywhere
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoginLog(Base):
    __tablename__ = "login_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(String(40), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    district: Mapped[str] = mapped_column(String(80), default="")
    block: Mapped[str] = mapped_column(String(80), default="")
    village: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class JobCounter(Base):
    """Last job card number used in each month (prefix = YYMM)."""
    __tablename__ = "job_counters"
    prefix: Mapped[str] = mapped_column(String(4), primary_key=True)
    last: Mapped[int] = mapped_column(Integer, default=0)


class JobCard(Base):
    __tablename__ = "job_cards"
    id: Mapped[int] = mapped_column(primary_key=True)
    no: Mapped[str] = mapped_column(String(12), unique=True, index=True)  # e.g. 2610-0001
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    # Customer details as they were on the day the job card was made (also kept in "customers").
    customer_name: Mapped[str] = mapped_column(String(120), default="")
    customer_phone: Mapped[str] = mapped_column(String(10), default="")
    customer_district: Mapped[str] = mapped_column(String(80), default="")
    customer_block: Mapped[str] = mapped_column(String(80), default="")
    customer_village: Mapped[str] = mapped_column(String(80), default="")
    product_category: Mapped[str] = mapped_column(String(40))
    product_model: Mapped[str] = mapped_column(String(60))
    product_serial: Mapped[str] = mapped_column(String(60), default="")
    accessories: Mapped[str] = mapped_column(Text, default="")
    job_type: Mapped[str] = mapped_column(String(20), default="service")
    team: Mapped[str] = mapped_column(String(10), default="service", index=True)
    status: Mapped[str] = mapped_column(String(20), default="not_assigned", index=True)
    pending_note: Mapped[str] = mapped_column(Text, default="")  # reason for the current "Pending" status
    mechanic_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # assigned staff member
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    work_done: Mapped[str] = mapped_column(Text, default="")
    labour: Mapped[int] = mapped_column(Integer, default=0)
    # Workflow (see the Job Card Flows chart)
    warranty: Mapped[bool] = mapped_column(Boolean, default=False)        # no charge to the customer
    warranty_note: Mapped[str] = mapped_column(String(200), default="")   # invoice date / warranty card no.
    estimate: Mapped[int] = mapped_column(Integer, default=0)             # parts + labour quoted to the customer
    approval: Mapped[str] = mapped_column(String(10), default="")         # "" | waiting | approved | declined
    approval_note: Mapped[str] = mapped_column(String(300), default="")
    close_reason: Mapped[str] = mapped_column(String(20), default="")     # why it went back unrepaired
    rework_of: Mapped[str] = mapped_column(String(12), default="")        # earlier job card no. (repeat complaint)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    customer: Mapped[Customer] = relationship(lazy="joined")
    mechanic: Mapped[User | None] = relationship(foreign_keys=[mechanic_id], lazy="joined")
    complaints: Mapped[list["JobComplaint"]] = relationship(
        order_by="JobComplaint.position", cascade="all, delete-orphan", lazy="selectin"
    )
    history: Mapped[list["JobStatusLog"]] = relationship(
        order_by="JobStatusLog.id", cascade="all, delete-orphan", lazy="selectin"
    )
    parts: Mapped[list["JobPart"]] = relationship(
        order_by="JobPart.id", cascade="all, delete-orphan", lazy="selectin"
    )
    photos: Mapped[list["JobPhoto"]] = relationship(
        order_by="JobPhoto.id", cascade="all, delete-orphan", lazy="selectin"
    )


PHOTO_KINDS = ("before", "after")
PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp")
MAX_PHOTO_BYTES = 1_500_000   # the app shrinks photos to about 150 KB before sending
MAX_PHOTOS_PER_JOB = 12


class JobPhoto(Base):
    """Photo on a job card: the device when it came in ("before") and after the repair ("after")."""
    __tablename__ = "job_photos"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_card_id: Mapped[int] = mapped_column(ForeignKey("job_cards.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10), default="before")
    content_type: Mapped[str] = mapped_column(String(20), default="image/jpeg")
    size: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)  # loaded only when the photo itself is opened
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    user: Mapped[User | None] = relationship(lazy="joined")


class StockItem(Base):
    """Inventory: spare parts kept in the shop. Issuing a part to a job card takes it out of stock."""
    __tablename__ = "stock_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    part_no: Mapped[str] = mapped_column(String(60), default="")
    qty: Mapped[int] = mapped_column(Integer, default=0)
    price: Mapped[int] = mapped_column(Integer, default=0)       # selling price per piece (₹)
    min_qty: Mapped[int] = mapped_column(Integer, default=0)     # show "low stock" at or below this
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class JobPart(Base):
    """One spare part needed for a job card. It moves Service → Spare parts → Procurement → Spare parts → Service."""
    __tablename__ = "job_parts"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_card_id: Mapped[int] = mapped_column(ForeignKey("job_cards.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    part_no: Mapped[str] = mapped_column(String(60), default="")
    qty: Mapped[int] = mapped_column(Integer, default=1)
    price: Mapped[int] = mapped_column(Integer, default=0)            # selling price per piece (₹)
    status: Mapped[str] = mapped_column(String(20), default="requested", index=True)
    during_repair: Mapped[bool] = mapped_column(Boolean, default=False)  # extra fault found while repairing
    replaces_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # defective line this one replaces
    stock_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # inventory item it was issued from
    bought: Mapped[bool] = mapped_column(Boolean, default=False)      # went through Procurement
    vendor: Mapped[str] = mapped_column(String(120), default="")
    po_no: Mapped[str] = mapped_column(String(40), default="")
    eta: Mapped[str] = mapped_column(String(10), default="")          # YYYY-MM-DD
    claim: Mapped[str] = mapped_column(String(10), default="")        # warranty / vendor claim for the old or bad part
    note: Mapped[str] = mapped_column(String(300), default="")        # last reason written on this part
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class JobComplaint(Base):
    __tablename__ = "job_complaints"
    __table_args__ = (UniqueConstraint("job_card_id", "position"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_card_id: Mapped[int] = mapped_column(ForeignKey("job_cards.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(String(300))


class JobStatusLog(Base):
    """Every status change of a job card: from, to, reason note, who and when."""
    __tablename__ = "job_status_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_card_id: Mapped[int] = mapped_column(ForeignKey("job_cards.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str] = mapped_column(String(20), default="")
    to_status: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(Text, default="")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    user: Mapped[User | None] = relationship(lazy="joined")


class ActivityLog(Base):
    """Who did what, and when."""
    __tablename__ = "activity_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(40), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
