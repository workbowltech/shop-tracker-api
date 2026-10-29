"""Inventory (spare parts in the shop).

Everyone logged in can look at the stock (the engineer may check before asking).
The Spare parts team and the owner add items and change quantities and prices.
Issuing a part to a job card from stock reduces the quantity (see job_parts.py).
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import current_user, log_activity, require_roles
from ..models import StockItem, User
from ..schemas import StockEditIn, StockIn, StockOut

router = APIRouter(prefix="/stock", tags=["inventory"])
stock_keepers = require_roles("owner", "parts")


@router.get("", response_model=list[StockOut])
def list_stock(q: str | None = Query(default=None, max_length=60), db: Session = Depends(get_db),
               user: User = Depends(current_user)):
    stmt = select(StockItem).order_by(StockItem.name)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(StockItem.name).like(like), func.lower(StockItem.part_no).like(like)))
    return db.scalars(stmt).all()


@router.post("", response_model=StockOut, status_code=201)
def add_item(data: StockIn, db: Session = Depends(get_db), user: User = Depends(stock_keepers)):
    item = StockItem(name=data.name, part_no=data.part_no.upper(), qty=data.qty, price=data.price, min_qty=data.min_qty)
    db.add(item)
    db.flush()
    log_activity(db, user, "create", "stock", item.id, f"{item.name} qty {item.qty}")
    db.commit()
    return item


@router.patch("/{item_id}", response_model=StockOut)
def edit_item(item_id: int, data: StockEditIn, db: Session = Depends(get_db), user: User = Depends(stock_keepers)):
    item = db.get(StockItem, item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inventory item not found.")
    for field in ("name", "price", "min_qty"):
        v = getattr(data, field)
        if v is not None:
            setattr(item, field, " ".join(v.split()) if isinstance(v, str) else v)
    if data.part_no is not None:
        item.part_no = " ".join(data.part_no.split()).upper()
    if data.add:
        if item.qty + data.add < 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Stock cannot go below zero.")
        item.qty += data.add
    log_activity(db, user, "update", "stock", item.id, f"{item.name} qty {item.qty}")
    db.commit()
    return item
