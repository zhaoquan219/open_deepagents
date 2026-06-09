from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

PRODUCT_TABLES = frozenset({"users", "sessions", "runs", "events", "uploads"})
_EVENT_LOCK = Lock()

# Maintenance databases that always exist on a stock PostgreSQL server. We connect
# to one of these to create the target database when it does not exist yet.
_POSTGRES_MAINTENANCE_DBS = ("postgres", "template1")


def normalize_database_url(database_url: str) -> str:
    """Return a SQLAlchemy URL with an explicit, installed driver.

    Bare ``postgresql://`` / ``postgres://`` URLs resolve to the psycopg2 dialect in
    SQLAlchemy, which is not installed here, so they are rewritten to use psycopg
    (v3). Bare ``mysql://`` URLs are rewritten to use PyMySQL. URLs that already name
    a driver (for example ``postgresql+psycopg``) are left untouched.
    """
    url = make_url(database_url)
    driver = url.drivername.lower()
    base, _, suffix = driver.partition("+")
    if base in {"postgres", "postgresql"}:
        new_driver = f"postgresql+{suffix}" if suffix else "postgresql+psycopg"
        url = url.set(drivername=new_driver)
    elif base in {"mysql", "mariadb"} and not suffix:
        url = url.set(drivername=f"{base}+pymysql")
    return url.render_as_string(hide_password=False)


def to_libpq_conn_string(database_url: str) -> str:
    """Convert a SQLAlchemy URL into a libpq connection string for psycopg.

    LangGraph's Postgres checkpoint/store connect through psycopg directly, which
    rejects SQLAlchemy's ``postgresql+psycopg://`` form. This strips the driver
    suffix so ``postgresql://...`` (optionally with query options such as
    ``?sslmode=require``) is produced.
    """
    url = make_url(normalize_database_url(database_url)).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def _quote_pg_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def ensure_postgres_database(database_url: str) -> None:
    """Create the target PostgreSQL database if it does not already exist.

    Connects to a maintenance database (``postgres`` then ``template1``) and issues
    ``CREATE DATABASE`` when the target is missing. If no maintenance database can be
    reached the call is a no-op so the caller's own connection surfaces the real
    error.
    """
    url = make_url(normalize_database_url(database_url))
    target = url.database
    if not target:
        return
    for maintenance_db in _POSTGRES_MAINTENANCE_DBS:
        admin_engine = create_engine(
            url.set(database=maintenance_db),
            future=True,
            isolation_level="AUTOCOMMIT",
        )
        try:
            with admin_engine.connect() as conn:
                exists = conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": target},
                ).scalar()
                if not exists:
                    conn.exec_driver_sql(
                        f"CREATE DATABASE {_quote_pg_identifier(target)}"
                    )
            return
        except (OperationalError, ProgrammingError):
            continue
        finally:
            admin_engine.dispose()


def now() -> datetime:
    return datetime.now(UTC)


def _supports_partial_index(
    ddl: Any,
    target: Any,
    bind: Any,
    tables: Any = None,
    state: Any = None,
    *,
    dialect: Any,
    compiler: Any = None,
    checkfirst: bool = False,
    **kw: Any,
) -> bool:
    """ddl_if callback: only emit the filtered unique index where it is supported.

    SQLite and PostgreSQL support partial/filtered indexes; MySQL does not.
    """
    return bool(dialect.name in {"sqlite", "postgresql"})


def new_id() -> str:
    return str(uuid4())


def new_short_id() -> str:
    return uuid4().hex[:12]


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_email", "email"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="user", nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionRecord(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_owner_updated", "owner_user_id", "updated_at"),
        Index("ix_sessions_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_short_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="New session", nullable=False)
    thread_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="idle", nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner: Mapped[UserRecord] = relationship()


class RunRecord(Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_session_id", "session_id"),
        Index("ix_runs_user_id", "user_id"),
        Index("ix_runs_status", "status"),
        Index("ix_runs_session_status", "session_id", "status"),
        # Partial unique index: at most one running run per session. MySQL does not
        # support partial/filtered indexes, so this is only emitted on SQLite and
        # PostgreSQL; MySQL relies on the application-level active-run guard. Without
        # the filter a plain unique index on session_id would wrongly forbid more
        # than one run per session over the session's lifetime.
        Index(
            "uq_runs_one_running_per_session",
            "session_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ).ddl_if(callable_=_supports_partial_index),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    session: Mapped[SessionRecord] = relationship()
    user: Mapped[UserRecord] = relationship()


class UploadRecord(Base):
    __tablename__ = "uploads"
    __table_args__ = (
        Index("ix_uploads_session_id", "session_id"),
        Index("ix_uploads_user_id", "user_id"),
        Index("ix_uploads_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_short_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="uploaded", nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[SessionRecord] = relationship()
    user: Mapped[UserRecord] = relationship()


class EventRecord(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_events_session_seq"),
        Index("ix_events_run_id", "run_id"),
        Index("ix_events_kind", "kind"),
        Index("ix_events_type", "type"),
        Index("ix_events_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    visibility: Mapped[str] = mapped_column(String(32), default="public", nullable=False)
    redaction: Mapped[str] = mapped_column(String(32), default="none", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Database:
    def __init__(self, database_url: str) -> None:
        self.database_url = normalize_database_url(database_url)
        family = make_url(self.database_url).get_backend_name()
        if family in {"mysql", "mariadb"}:
            self._ensure_mysql_database()
        elif family in {"postgresql", "postgres"}:
            ensure_postgres_database(self.database_url)
        connect_args = (
            {"check_same_thread": False} if self.database_url.startswith("sqlite") else {}
        )
        self.engine = create_engine(self.database_url, future=True, connect_args=connect_args)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def initialize_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def _ensure_mysql_database(self) -> None:
        url = make_url(self.database_url)
        if not url.database:
            return
        engine = create_engine(url.set(database="mysql"), future=True)
        database = url.database.replace("`", "``")
        with engine.begin() as conn:
            conn.exec_driver_sql(f"CREATE DATABASE IF NOT EXISTS `{database}`")
        engine.dispose()

    @contextmanager
    def session(self) -> Iterator[Session]:
        db = self.session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def dispose(self) -> None:
        self.engine.dispose()


def append_event(
    db: Session,
    *,
    session_id: str,
    kind: str,
    type: str,
    run_id: str | None = None,
    role: str | None = None,
    content: str | None = None,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    visibility: str = "public",
    redaction: str = "none",
    payload: dict[str, Any] | None = None,
) -> EventRecord:
    safe_payload = payload or {}
    json.dumps(safe_payload, ensure_ascii=False)
    with _EVENT_LOCK:
        db.execute(select(SessionRecord.id).where(SessionRecord.id == session_id).with_for_update())
        seq = _next_event_seq(db, session_id)
        event = EventRecord(
            session_id=session_id,
            run_id=run_id,
            seq=seq,
            kind=kind,
            type=type,
            role=role,
            content=content,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            visibility=visibility,
            redaction=redaction,
            payload=safe_payload,
        )
        db.add(event)
        db.flush()
        return event


def _next_event_seq(db: Session, session_id: str) -> int:
    current = db.execute(
        select(func.max(EventRecord.seq)).where(EventRecord.session_id == session_id)
    ).scalar_one_or_none()
    return int(current or 0) + 1
