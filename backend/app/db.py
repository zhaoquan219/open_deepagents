from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from queue import Empty, Queue
from threading import Event, Lock, Thread
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
    insert,
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

LOGGER = logging.getLogger("app.db")

PRODUCT_TABLES = frozenset({"users", "sessions", "runs", "events", "uploads"})
_EVENT_LOCK = Lock()
# In-process high-water mark of the last allocated event seq per session. This lets
# append_event allocate the next seq without a per-event ``SELECT max(seq)`` round
# trip, which is the dominant latency cost when the product database is a remote
# server (every streamed token would otherwise pay multiple network round trips).
# All writes go through _EVENT_LOCK in a single process, so this stays authoritative;
# the UniqueConstraint(session_id, seq) is the durable backstop.
_EVENT_SEQ_HIGH_WATER: dict[str, int] = {}

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
    def __init__(
        self,
        database_url: str,
        *,
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_recycle: int = 1800,
    ) -> None:
        self.database_url = normalize_database_url(database_url)
        family = make_url(self.database_url).get_backend_name()
        if family in {"mysql", "mariadb"}:
            self._ensure_mysql_database()
        elif family in {"postgresql", "postgres"}:
            ensure_postgres_database(self.database_url)
        is_sqlite = self.database_url.startswith("sqlite")
        engine_kwargs: dict[str, Any] = {
            "future": True,
            "connect_args": {"check_same_thread": False} if is_sqlite else {},
        }
        if not is_sqlite:
            # Networked databases: validate pooled connections before use (survives
            # server restarts / idle timeouts) and size the pool so the streaming
            # status reads and HTTP requests do not starve each other.
            engine_kwargs.update(
                pool_pre_ping=True,
                pool_recycle=pool_recycle,
                pool_size=pool_size,
                max_overflow=max_overflow,
            )
        self.engine = create_engine(self.database_url, **engine_kwargs)
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
    """Allocate the next per-session event seq. Must be called while holding _EVENT_LOCK.

    The first allocation for a session in this process primes the in-memory
    high-water mark from the database; subsequent allocations increment it in
    memory, avoiding a ``SELECT max(seq)`` network round trip per event.
    """
    cached = _EVENT_SEQ_HIGH_WATER.get(session_id)
    if cached is None:
        current = db.execute(
            select(func.max(EventRecord.seq)).where(EventRecord.session_id == session_id)
        ).scalar_one_or_none()
        cached = int(current or 0)
    seq = cached + 1
    _EVENT_SEQ_HIGH_WATER[session_id] = seq
    return seq


def _allocate_seq_via_writer(database: Database, session_id: str) -> int:
    """Allocate the next seq for a background-persisted event.

    On a cache hit this is a pure in-memory increment (no database round trip). On a
    miss the priming ``SELECT max(seq)`` is issued *outside* ``_EVENT_LOCK`` so a
    network round trip never blocks every other session's seq allocation; the lock is
    only held for the in-memory increment. Shares the counter with the synchronous
    :func:`append_event` path so seq stays monotonic across both writers.
    """
    with _EVENT_LOCK:
        cached = _EVENT_SEQ_HIGH_WATER.get(session_id)
        if cached is not None:
            seq = cached + 1
            _EVENT_SEQ_HIGH_WATER[session_id] = seq
            return seq
    with database.session() as db:
        primed = db.execute(
            select(func.max(EventRecord.seq)).where(EventRecord.session_id == session_id)
        ).scalar_one_or_none()
    base = int(primed or 0)
    with _EVENT_LOCK:
        # Another writer may have primed/advanced the counter while we read the DB;
        # prefer the in-memory value if present so we never hand out a stale seq.
        current = _EVENT_SEQ_HIGH_WATER.get(session_id, base)
        seq = current + 1
        _EVENT_SEQ_HIGH_WATER[session_id] = seq
        return seq


def forget_event_seq(session_id: str) -> None:
    """Drop a session's in-memory seq high-water mark.

    Called when a run's stream ends (after the writer has flushed): with at most one
    active run per session, the next run re-primes from the now-complete database max,
    so this bounds memory to currently-active sessions instead of every session ever
    seen by the process.
    """
    with _EVENT_LOCK:
        _EVENT_SEQ_HIGH_WATER.pop(session_id, None)


@dataclass(frozen=True)
class EventSpec:
    """A fully-allocated event awaiting durable persistence.

    Carries the same attributes ``sse_payload`` reads from an ``EventRecord`` (seq, id,
    created_at, type, kind, content, ...), so the SSE chunk can be built and streamed
    to the client immediately, before the row is written to the database.
    """

    id: str
    session_id: str
    run_id: str | None
    seq: int
    kind: str
    type: str
    role: str | None
    content: str | None
    tool_name: str | None
    tool_call_id: str | None
    visibility: str
    redaction: str
    payload: dict[str, Any]
    created_at: datetime

    def as_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "seq": self.seq,
            "kind": self.kind,
            "type": self.type,
            "role": self.role,
            "content": self.content,
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "visibility": self.visibility,
            "redaction": self.redaction,
            "payload": self.payload,
            "created_at": self.created_at,
        }


class _FlushSignal:
    """Queue marker that forces a synchronous flush and reports back to the caller."""

    __slots__ = ("done", "error")

    def __init__(self) -> None:
        self.done = Event()
        self.error: BaseException | None = None


_SHUTDOWN = object()


class EventWriter:
    """Persists events on a dedicated background thread, batching inserts.

    ``enqueue`` allocates the durable seq/id/timestamp synchronously in memory and
    returns immediately, so the SSE hot path never blocks on a database round trip.
    A worker thread flushes batches when they reach ``batch_size`` or after
    ``flush_interval`` seconds of inactivity. ``flush`` forces a synchronous drain for
    read-after-write points (for example when a run reaches a terminal state and the
    durable transcript must be complete before the stream closes).
    """

    def __init__(
        self,
        database: Database,
        *,
        batch_size: int = 100,
        flush_interval: float = 0.05,
    ) -> None:
        self._database = database
        self._batch_size = max(1, batch_size)
        self._flush_interval = flush_interval
        self._queue: Queue[Any] = Queue()
        self._closed = False
        self._thread = Thread(
            target=self._run,
            name="deepagents-event-writer",
            daemon=True,
        )
        self._thread.start()

    def enqueue(
        self,
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
    ) -> EventSpec:
        safe_payload = payload or {}
        json.dumps(safe_payload, ensure_ascii=False)
        seq = _allocate_seq_via_writer(self._database, session_id)
        spec = EventSpec(
            id=new_id(),
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
            created_at=now(),
        )
        self._queue.put(spec)
        return spec

    def flush(self) -> None:
        """Block until every event enqueued before this call is committed."""
        if self._closed:
            return
        signal = _FlushSignal()
        self._queue.put(signal)
        signal.done.wait()
        if signal.error is not None:
            raise signal.error

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(_SHUTDOWN)
        self._thread.join(timeout=10)

    def _run(self) -> None:
        batch: list[EventSpec] = []
        while True:
            try:
                item = self._queue.get(timeout=self._flush_interval if batch else None)
            except Empty:
                self._commit(batch)
                batch = []
                continue
            if item is _SHUTDOWN:
                self._commit(batch)
                return
            if isinstance(item, _FlushSignal):
                try:
                    self._write(batch)
                except BaseException as exc:  # noqa: BLE001 - reported to flush() caller
                    item.error = exc
                    LOGGER.exception("event writer flush failed for %d event(s)", len(batch))
                finally:
                    batch = []
                    item.done.set()
                continue
            batch.append(item)
            if len(batch) >= self._batch_size:
                self._commit(batch)
                batch = []

    def _commit(self, batch: list[EventSpec]) -> None:
        try:
            self._write(batch)
        except Exception:
            LOGGER.exception("event writer failed to persist %d event(s)", len(batch))

    def _write(self, batch: list[EventSpec]) -> None:
        if not batch:
            return
        with self._database.session() as db:
            db.execute(insert(EventRecord), [spec.as_mapping() for spec in batch])
