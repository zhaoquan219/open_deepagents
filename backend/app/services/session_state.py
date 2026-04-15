from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import case, or_, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import SessionRecord, SessionStateRecord, utc_now

STATE_STATUS_PENDING = "pending"
STATE_STATUS_READY = "ready"
STATE_STATUS_CONSUMED = "consumed"
CONSUME_POLICY_ONCE = "once"
CONSUME_POLICY_KEEP = "keep"


class SessionStateError(ValueError):
    pass


class SessionStateMergeError(SessionStateError):
    pass


class SessionStateSessionNotFoundError(SessionStateError):
    pass


@dataclass(frozen=True)
class SessionStateConsumeResult:
    outcome: Literal["consumed", "pending", "not_ready", "expired"]
    state: SessionStateRecord


@dataclass(frozen=True)
class SessionStateSnapshot:
    session_id: str
    namespace: str
    key: str
    status: str
    consume_policy: str
    value: Any
    version: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    last_consumed_at: datetime | None
    last_consumed_run_id: str | None

    @classmethod
    def from_record(cls, record: SessionStateRecord) -> SessionStateSnapshot:
        return cls(
            session_id=record.session_id,
            namespace=record.namespace,
            key=record.key,
            status=record.status,
            consume_policy=record.consume_policy,
            value=record.value,
            version=record.version,
            created_at=record.created_at,
            updated_at=record.updated_at,
            expires_at=record.expires_at,
            last_consumed_at=record.last_consumed_at,
            last_consumed_run_id=record.last_consumed_run_id,
        )


class SessionStateService:
    def __init__(self, db: Session):
        self.db = db

    def get(
        self,
        *,
        session_id: str,
        namespace: str,
        key: str,
        include_expired: bool = False,
    ) -> SessionStateRecord | None:
        query = self.db.query(SessionStateRecord).filter(
            SessionStateRecord.session_id == session_id,
            SessionStateRecord.namespace == namespace,
            SessionStateRecord.key == key,
        )
        if not include_expired:
            query = query.filter(_is_not_expired())
        return query.first()

    def list(
        self,
        *,
        session_id: str,
        namespace: str | None = None,
        status: str | None = None,
        include_expired: bool = False,
        limit: int | None = None,
    ) -> list[SessionStateRecord]:
        query = self.db.query(SessionStateRecord).filter(
            SessionStateRecord.session_id == session_id
        )
        if namespace is not None:
            query = query.filter(SessionStateRecord.namespace == namespace)
        if status is not None:
            query = query.filter(SessionStateRecord.status == status)
        if not include_expired:
            query = query.filter(_is_not_expired())
        query = query.order_by(
            SessionStateRecord.namespace.asc(),
            SessionStateRecord.key.asc(),
        )
        if limit is not None:
            query = query.limit(limit)
        return list(query.all())

    def put(
        self,
        *,
        session_id: str,
        namespace: str,
        key: str,
        status: str,
        consume_policy: str,
        value: Any,
        expires_at: datetime | None,
    ) -> SessionStateRecord:
        self.ensure_session_exists(session_id)
        _validate_consume_policy(consume_policy)
        record = self.get(
            session_id=session_id,
            namespace=namespace,
            key=key,
            include_expired=True,
        )
        if record is None:
            record = SessionStateRecord(
                session_id=session_id,
                namespace=namespace,
                key=key,
                status=status,
                consume_policy=consume_policy,
                value=value,
                version=1,
                expires_at=expires_at,
            )
            self.db.add(record)
            self.db.flush()
            return record

        record.status = status
        record.consume_policy = consume_policy
        record.value = value
        record.expires_at = expires_at
        record.last_consumed_at = None
        record.last_consumed_run_id = None
        record.version += 1
        self.db.add(record)
        self.db.flush()
        return record

    def patch(
        self,
        *,
        session_id: str,
        namespace: str,
        key: str,
        status: str | None = None,
        consume_policy: str | None = None,
        value: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
        expires_at_is_set: bool = False,
    ) -> SessionStateRecord | None:
        record = self.get(
            session_id=session_id,
            namespace=namespace,
            key=key,
            include_expired=True,
        )
        if record is None:
            return None

        if status is not None:
            record.status = status
            if status in {STATE_STATUS_READY, STATE_STATUS_PENDING}:
                record.last_consumed_at = None
                record.last_consumed_run_id = None
        if consume_policy is not None:
            _validate_consume_policy(consume_policy)
            record.consume_policy = consume_policy
        if value is not None:
            record.value = _merge_json_objects(record.value, value)
        if expires_at_is_set:
            record.expires_at = expires_at

        record.version += 1
        self.db.add(record)
        self.db.flush()
        return record

    def delete(self, *, session_id: str, namespace: str, key: str) -> bool:
        record = self.get(
            session_id=session_id,
            namespace=namespace,
            key=key,
            include_expired=True,
        )
        if record is None:
            return False
        self.db.delete(record)
        self.db.flush()
        return True

    def consume(
        self,
        *,
        session_id: str,
        namespace: str,
        key: str,
        run_id: str | None = None,
    ) -> SessionStateConsumeResult | None:
        record = self.get(
            session_id=session_id,
            namespace=namespace,
            key=key,
            include_expired=True,
        )
        if record is None:
            return None

        now = utc_now()
        if _is_expired(record.expires_at, now=now):
            return SessionStateConsumeResult(outcome="expired", state=record)
        if record.status == STATE_STATUS_PENDING:
            return SessionStateConsumeResult(outcome="pending", state=record)
        if record.status != STATE_STATUS_READY:
            return SessionStateConsumeResult(outcome="not_ready", state=record)

        execution = self.db.execute(
            update(SessionStateRecord)
            .where(
                SessionStateRecord.session_id == session_id,
                SessionStateRecord.namespace == namespace,
                SessionStateRecord.key == key,
                SessionStateRecord.status == STATE_STATUS_READY,
                _is_not_expired(now),
            )
            .values(
                status=case(
                    (
                        SessionStateRecord.consume_policy == CONSUME_POLICY_KEEP,
                        SessionStateRecord.status,
                    ),
                    else_=STATE_STATUS_CONSUMED,
                ),
                last_consumed_at=now,
                last_consumed_run_id=run_id,
                updated_at=now,
                version=SessionStateRecord.version + 1,
            )
        )
        self.db.flush()
        rowcount = getattr(execution, "rowcount", None)

        refreshed = self.get(
            session_id=session_id,
            namespace=namespace,
            key=key,
            include_expired=True,
        )
        if refreshed is None:
            return None
        if rowcount is not None and rowcount > 0:
            return SessionStateConsumeResult(outcome="consumed", state=refreshed)
        if _is_expired(refreshed.expires_at, now=now):
            return SessionStateConsumeResult(outcome="expired", state=refreshed)
        if refreshed.status == STATE_STATUS_PENDING:
            return SessionStateConsumeResult(outcome="pending", state=refreshed)
        return SessionStateConsumeResult(outcome="not_ready", state=refreshed)

    def ensure_session_exists(self, session_id: str) -> None:
        exists = (
            self.db.query(SessionRecord.id)
            .filter(SessionRecord.id == session_id)
            .first()
        )
        if exists is None:
            raise SessionStateSessionNotFoundError("Session not found")


class SessionStateHookHelper:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def consume_ready_states(
        self,
        *,
        session_id: str,
        namespace: str,
        run_id: str | None = None,
        limit: int = 5,
    ) -> list[SessionStateSnapshot]:
        with self._session_factory() as db:
            service = SessionStateService(db)
            snapshots: list[SessionStateSnapshot] = []
            for record in service.list(
                session_id=session_id,
                namespace=namespace,
                status=STATE_STATUS_READY,
                include_expired=False,
                limit=limit,
            ):
                result = service.consume(
                    session_id=session_id,
                    namespace=namespace,
                    key=record.key,
                    run_id=run_id,
                )
                if result is None or result.outcome != "consumed":
                    continue
                snapshots.append(SessionStateSnapshot.from_record(result.state))
            db.commit()
            return snapshots


def _merge_json_objects(current: Any, patch: Mapping[str, Any]) -> dict[str, Any]:
    if current is None:
        base: dict[str, Any] = {}
    elif isinstance(current, Mapping):
        base = dict(current)
    else:
        raise SessionStateMergeError("Existing state value must be a JSON object to merge")
    return {**base, **dict(patch)}


def _validate_consume_policy(consume_policy: str) -> None:
    if consume_policy not in {CONSUME_POLICY_ONCE, CONSUME_POLICY_KEEP}:
        raise SessionStateError("consume_policy must be 'once' or 'keep'")


def _is_expired(expires_at: datetime | None, *, now: datetime | None = None) -> bool:
    if expires_at is None:
        return False
    reference = _normalize_datetime(now or utc_now())
    return _normalize_datetime(expires_at) <= reference


def _is_not_expired(now: datetime | None = None) -> Any:
    reference = now or utc_now()
    return or_(
        SessionStateRecord.expires_at.is_(None),
        SessionStateRecord.expires_at > reference,
    )


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
