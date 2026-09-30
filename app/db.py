"""Database models and session handling (SQLAlchemy 2.x). SQLite locally, Postgres on Render."""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    claim_no: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="received")
    stage: Mapped[int] = mapped_column(Integer, default=0)
    # stage results
    extraction: Mapped[dict | None] = mapped_column(JSON)
    detection: Mapped[dict | None] = mapped_column(JSON)
    decision: Mapped[dict | None] = mapped_column(JSON)
    demand: Mapped[dict | None] = mapped_column(JSON)
    review: Mapped[list | None] = mapped_column(JSON)
    # stages 4-7
    channel: Mapped[str | None] = mapped_column(String(16))        # hub | email
    neg: Mapped[dict | None] = mapped_column(JSON)                 # negotiation state
    messages: Mapped[list | None] = mapped_column(JSON)            # correspondence log
    action: Mapped[dict | None] = mapped_column(JSON)              # what the user must do, if anything
    escalation: Mapped[dict | None] = mapped_column(JSON)          # arbitration / litigation packet
    recovery: Mapped[dict | None] = mapped_column(JSON)            # payments, reconciliation, refund
    error: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class File(Base):
    __tablename__ = "files"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # upload | letter | bundle | arbitration | litigation | user_doc
    filename: Mapped[str] = mapped_column(String(200))
    content: Mapped[bytes] = mapped_column(LargeBinary)


class Page(Base):
    __tablename__ = "pages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str | None] = mapped_column(String(40))


class Event(Base):
    """Append-only audit log. The live feed on screen reads this table."""
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    stage: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # status | model | tool | guard | human | error | carrier | system
    name: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict | None] = mapped_column(JSON)


class Payment(Base):
    """Incoming payments as a bank or finance feed would report them (simulated in v1)."""
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    received_on: Mapped[str] = mapped_column(String(10))
    payer: Mapped[str] = mapped_column(String(120))
    amount: Mapped[float] = mapped_column(Float)
    reference: Mapped[str] = mapped_column(String(120))
    case_id: Mapped[str | None] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(16), default="unmatched")  # unmatched | matched | short


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def add_event(case_id: str, stage: int, kind: str, name: str, payload: dict | None = None) -> None:
    with session() as s:
        s.add(Event(case_id=case_id, stage=stage, kind=kind, name=name, payload=payload or {}))


def events_after(case_id: str, after_id: int) -> list[dict]:
    with session() as s:
        rows = s.scalars(select(Event).where(Event.case_id == case_id, Event.id > after_id).order_by(Event.id)).all()
        return [
            {"id": e.id, "ts": e.ts.isoformat(), "stage": e.stage, "kind": e.kind, "name": e.name, "payload": e.payload}
            for e in rows
        ]


def get_setting(key: str, default):
    with session() as s:
        row = s.get(Setting, key)
        return row.value.get("v", default) if row else default


def set_setting(key: str, value) -> None:
    with session() as s:
        row = s.get(Setting, key)
        if row:
            row.value = {"v": value}
        else:
            s.add(Setting(key=key, value={"v": value}))


def update_case(case_id: str, **fields) -> None:
    with session() as s:
        c = s.get(Case, case_id)
        for k, v in fields.items():
            setattr(c, k, v)


def get_case(case_id: str) -> Case | None:
    with session() as s:
        return s.get(Case, case_id)
