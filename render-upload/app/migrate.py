"""Small, safe upgrades for tables that already exist (new tables are made by create_all).

Adds new job card columns (and the job_parts table) and changes old status names to the new ones.
Runs every time the server starts; does nothing when the database is already up to date.
"""
from sqlalchemy import inspect, text

from . import models  # noqa: F401  (registers all tables)
from .database import Base, engine

NEW_JOB_COLUMNS = {
    "job_type": "VARCHAR(20) NOT NULL DEFAULT 'service'",
    "team": "VARCHAR(10) NOT NULL DEFAULT 'service'",
    "pending_note": "TEXT NOT NULL DEFAULT ''",
    "customer_name": "VARCHAR(120) NOT NULL DEFAULT ''",
    "customer_phone": "VARCHAR(10) NOT NULL DEFAULT ''",
    "customer_district": "VARCHAR(80) NOT NULL DEFAULT ''",
    "customer_block": "VARCHAR(80) NOT NULL DEFAULT ''",
    "customer_village": "VARCHAR(80) NOT NULL DEFAULT ''",
    # parts workflow
    "warranty": "BOOLEAN NOT NULL DEFAULT FALSE",
    "warranty_note": "VARCHAR(200) NOT NULL DEFAULT ''",
    "estimate": "INTEGER NOT NULL DEFAULT 0",
    "approval": "VARCHAR(10) NOT NULL DEFAULT ''",
    "approval_note": "VARCHAR(300) NOT NULL DEFAULT ''",
    "close_reason": "VARCHAR(20) NOT NULL DEFAULT ''",
    "rework_of": "VARCHAR(12) NOT NULL DEFAULT ''",
}
NEW_PART_COLUMNS = {"stock_item_id": "INTEGER"}
OLD_TO_NEW_STATUS = {"open": "not_assigned", "progress": "in_progress", "done": "completed"}


def upgrade() -> None:
    Base.metadata.create_all(engine)
    have = {c["name"] for c in inspect(engine).get_columns("job_cards")}
    have_parts = {c["name"] for c in inspect(engine).get_columns("job_parts")}
    with engine.begin() as conn:
        for name, ddl in NEW_PART_COLUMNS.items():
            if name not in have_parts:
                conn.execute(text(f"ALTER TABLE job_parts ADD COLUMN {name} {ddl}"))
        for name, ddl in NEW_JOB_COLUMNS.items():
            if name not in have:
                conn.execute(text(f"ALTER TABLE job_cards ADD COLUMN {name} {ddl}"))
        for old, new in OLD_TO_NEW_STATUS.items():
            conn.execute(text("UPDATE job_cards SET status = :new WHERE status = :old"), {"new": new, "old": old})
        # Copy customer details into older job cards that do not have them yet.
        for col in ("name", "district", "block", "village", "phone"):  # phone last (it is the "not done yet" check)
            conn.execute(text(
                f"UPDATE job_cards SET customer_{col} = "
                f"(SELECT c.{col} FROM customers c WHERE c.id = job_cards.customer_id) "
                f"WHERE customer_phone = '' OR customer_phone IS NULL"
            ))
        # An old "open" job that already had a mechanic counts as in progress.
        conn.execute(text("UPDATE job_cards SET status = 'in_progress' WHERE status = 'not_assigned' AND mechanic_id IS NOT NULL"))
