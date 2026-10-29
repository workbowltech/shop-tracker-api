"""Shapes of the data the app sends and receives (checked automatically)."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import settings
from .models import CLOSE_REASONS, HOLDER_OF_STATUS, JOB_STATUSES, JOB_TYPES, PHOTO_KINDS, PHOTO_TYPES, ROLES

PHONE = r"^\d{10}$"


def _clean(v: str) -> str:
    return " ".join(v.split())


# ---------- auth / users ----------
class LoginIn(BaseModel):
    phone: str = Field(pattern=PHONE)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    phone: str
    role: str
    is_active: bool
    must_change_password: bool

    @field_validator("must_change_password")
    @classmethod
    def _only_if_forced(cls, v: bool) -> bool:
        # When FORCE_PASSWORD_CHANGE=false (testing), the app does not ask for a new password.
        return v and settings.force_password_change


class UserCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(pattern=PHONE)
    role: str

    @field_validator("name")
    @classmethod
    def tidy_name(cls, v: str) -> str:
        return _clean(v)

    @field_validator("role")
    @classmethod
    def valid_role(cls, v: str) -> str:
        if v not in ROLES:
            raise ValueError(f"role must be one of: {', '.join(ROLES)}")
        return v


class UserUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    role: str | None = None
    is_active: bool | None = None

    @field_validator("role")
    @classmethod
    def valid_role(cls, v: str | None) -> str | None:
        if v is not None and v not in ROLES:
            raise ValueError(f"role must be one of: {', '.join(ROLES)}")
        return v


class TempPasswordOut(BaseModel):
    user: UserOut
    temporary_password: str


# ---------- customers ----------
class CustomerIn(BaseModel):
    phone: str = Field(pattern=PHONE)
    name: str = Field(min_length=1, max_length=120)
    district: str = Field(default="", max_length=80)
    block: str = Field(min_length=1, max_length=80)
    village: str = Field(min_length=1, max_length=80)

    @field_validator("name", "district", "block", "village")
    @classmethod
    def tidy(cls, v: str) -> str:
        return _clean(v)


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    phone: str
    name: str
    district: str
    block: str
    village: str


class CustomerListOut(CustomerOut):
    jobs: int
    last_visit: datetime | None
    created_at: datetime | None = None


# ---------- job cards ----------
class ProductIn(BaseModel):
    category: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=60)
    serial: str = Field(default="", max_length=60)


class JobCardIn(BaseModel):
    team: str | None = None    # service | parts — the team that gets the job card (the app sends this)
    job_type: str = "service"  # older apps: service | spare_sale | spare_replace — decides the team when no team is sent
    customer: CustomerIn
    product: ProductIn
    accessories: str = Field(default="", max_length=1000)
    complaints: list[str] = Field(default_factory=list, max_length=20)
    warranty: bool = False
    warranty_note: str = Field(default="", max_length=200)
    rework_of: str = Field(default="", max_length=12)  # earlier job card no. when the same complaint comes back
    # Customer needs spare parts only (no repair): the parts go straight to the Spare parts team.
    parts_only: bool = False
    parts: list["PartIn"] = Field(default_factory=list, max_length=30)

    @field_validator("warranty_note", "rework_of")
    @classmethod
    def tidy_text(cls, v: str) -> str:
        return _clean(v)

    @field_validator("team")
    @classmethod
    def valid_team(cls, v: str | None) -> str | None:
        if v is not None and v not in ("service", "parts"):
            raise ValueError("team must be service or parts")
        return v

    @field_validator("job_type")
    @classmethod
    def valid_type(cls, v: str) -> str:
        if v not in JOB_TYPES:
            raise ValueError(f"job_type must be one of: {', '.join(JOB_TYPES)}")
        return v

    @field_validator("complaints")
    @classmethod
    def tidy_complaints(cls, v: list[str]) -> list[str]:
        out = [_clean(c)[:300] for c in v if c and c.strip()]
        return out


class StatusIn(BaseModel):
    status: str
    note: str = Field(default="", max_length=500)  # required for "Pending" statuses

    @field_validator("status")
    @classmethod
    def valid_status(cls, v: str) -> str:
        if v not in JOB_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(JOB_STATUSES)}")
        return v


class AssignIn(BaseModel):
    user_id: int


class WorkIn(BaseModel):
    work_done: str | None = Field(default=None, max_length=4000)
    labour: int | None = Field(default=None, ge=0, le=10_000_000)
    warranty: bool | None = None
    warranty_note: str | None = Field(default=None, max_length=200)


# ---------- spare part lines and the parts workflow ----------
class PartIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    part_no: str = Field(default="", max_length=60)
    qty: int = Field(default=1, ge=1, le=999)
    price: int = Field(default=0, ge=0, le=10_000_000)

    @field_validator("name", "part_no")
    @classmethod
    def tidy(cls, v: str) -> str:
        return _clean(v)


class PartEditIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    part_no: str | None = Field(default=None, max_length=60)
    qty: int | None = Field(default=None, ge=1, le=999)
    price: int | None = Field(default=None, ge=0, le=10_000_000)
    note: str = Field(default="", max_length=300)


PART_ACTIONS = ("issue", "need_buy", "return_stock", "order", "no_dealer", "receive", "reject",
                "claim_sent", "claim_ok", "claim_no", "defective", "decline")


class PartActionIn(BaseModel):
    action: str
    note: str = Field(default="", max_length=300)
    vendor: str = Field(default="", max_length=120)
    po_no: str = Field(default="", max_length=40)
    eta: str = Field(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    stock_item_id: int | None = None  # "issue": the inventory item it is taken from

    @field_validator("action")
    @classmethod
    def valid(cls, v: str) -> str:
        if v not in PART_ACTIONS:
            raise ValueError(f"action must be one of: {', '.join(PART_ACTIONS)}")
        return v


FLOW_ACTIONS = ("send_spares", "ask_approval", "approved", "declined", "declined_extra", "start_repair", "to_qc",
                "qc_pass", "qc_fail", "not_repairable", "cancel", "deliver", "hand_over")


class FlowIn(BaseModel):
    action: str
    note: str = Field(default="", max_length=500)
    estimate: int | None = Field(default=None, ge=0, le=100_000_000)
    close_reason: str = ""  # for not_repairable: ber (customer takes it back) | scrapped

    @field_validator("action")
    @classmethod
    def valid(cls, v: str) -> str:
        if v not in FLOW_ACTIONS:
            raise ValueError(f"action must be one of: {', '.join(FLOW_ACTIONS)}")
        return v

    @field_validator("close_reason")
    @classmethod
    def valid_reason(cls, v: str) -> str:
        if v not in CLOSE_REASONS:
            raise ValueError("unknown close reason")
        return v


class PhotoIn(BaseModel):
    kind: str = "before"
    content_type: str = "image/jpeg"
    data: str = Field(min_length=10, max_length=2_100_000)  # base64

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, v: str) -> str:
        if v not in PHOTO_KINDS:
            raise ValueError("kind must be before or after")
        return v

    @field_validator("content_type")
    @classmethod
    def valid_type(cls, v: str) -> str:
        if v not in PHOTO_TYPES:
            raise ValueError("Only JPEG, PNG or WebP photos")
        return v


class PhotoOut(BaseModel):
    id: int
    kind: str
    by: str
    by_id: int | None
    at: datetime


class StockIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    part_no: str = Field(default="", max_length=60)
    qty: int = Field(default=0, ge=0, le=1_000_000)
    price: int = Field(default=0, ge=0, le=10_000_000)
    min_qty: int = Field(default=0, ge=0, le=1_000_000)

    @field_validator("name", "part_no")
    @classmethod
    def tidy(cls, v: str) -> str:
        return _clean(v)


class StockEditIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    part_no: str | None = Field(default=None, max_length=60)
    price: int | None = Field(default=None, ge=0, le=10_000_000)
    min_qty: int | None = Field(default=None, ge=0, le=1_000_000)
    add: int | None = Field(default=None, ge=-1_000_000, le=1_000_000)  # + received into stock / − taken out


class StockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    part_no: str
    qty: int
    price: int
    min_qty: int
    updated_at: datetime | None


class PartOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    part_no: str
    qty: int
    price: int
    status: str
    during_repair: bool
    replaces_id: int | None
    bought: bool
    vendor: str
    po_no: str
    eta: str
    claim: str
    note: str
    stock_item_id: int | None = None
    updated_at: datetime | None


class MechanicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class StatusLogOut(BaseModel):
    from_status: str
    to_status: str
    note: str
    by: str
    at: datetime


class JobCardOut(BaseModel):
    id: int
    no: str
    job_type: str
    team: str
    status: str
    pending_note: str
    customer: CustomerOut
    product: ProductIn
    accessories: str
    complaints: list[str]
    assignee: MechanicOut | None
    work_done: str
    labour: int
    warranty: bool
    warranty_note: str
    estimate: int
    approval: str
    approval_note: str
    close_reason: str
    rework_of: str
    holder: str  # team that has the job card in its hands right now
    parts: list[PartOut]
    photos: list[PhotoOut]
    opened_at: datetime
    started_at: datetime | None
    done_at: datetime | None
    delivered_at: datetime | None
    history: list[StatusLogOut]

    @classmethod
    def of(cls, j) -> "JobCardOut":
        return cls(
            id=j.id, no=j.no, job_type=j.job_type, team=j.team, status=j.status, pending_note=j.pending_note,
            customer=CustomerOut.model_validate(j.customer),
            product=ProductIn(category=j.product_category, model=j.product_model, serial=j.product_serial),
            accessories=j.accessories,
            complaints=[c.text for c in j.complaints],
            assignee=MechanicOut.model_validate(j.mechanic) if j.mechanic else None,
            work_done=j.work_done, labour=j.labour,
            warranty=j.warranty, warranty_note=j.warranty_note, estimate=j.estimate, approval=j.approval,
            approval_note=j.approval_note, close_reason=j.close_reason, rework_of=j.rework_of,
            holder=HOLDER_OF_STATUS.get(j.status, j.team),
            parts=[PartOut.model_validate(p) for p in j.parts],
            photos=[PhotoOut(id=p.id, kind=p.kind, by=p.user.name if p.user else "", by_id=p.user_id, at=p.at)
                    for p in j.photos],
            opened_at=j.opened_at, started_at=j.started_at, done_at=j.done_at, delivered_at=j.delivered_at,
            history=[
                StatusLogOut(from_status=h.from_status, to_status=h.to_status, note=h.note,
                             by=h.user.name if h.user else "", at=h.at)
                for h in j.history
            ],
        )


JobCardIn.model_rebuild()  # it refers to PartIn, which is defined further down
