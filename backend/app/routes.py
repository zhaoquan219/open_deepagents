from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import agent as runtime_agent
from app.auth import CurrentUser, DbSession, create_token, verify_password
from app.catalog import default_model_id, model_options, resolve_agent
from app.db import EventRecord, RunRecord, SessionRecord, UserRecord, append_event, now
from app.settings import Settings

router = APIRouter()

USER_FIELDS = ("id", "username", "email", "role", "is_active")
SESSION_FIELDS = (
    "id",
    "owner_user_id",
    "title",
    "thread_id",
    "status",
    "metadata",
    "created_at",
    "updated_at",
    "archived_at",
)
RUN_FIELDS = (
    "id",
    "session_id",
    "user_id",
    "thread_id",
    "agent_id",
    "model_id",
    "status",
    "started_at",
    "ended_at",
    "cancel_requested_at",
    "error_message",
    "metadata",
    "created_at",
    "updated_at",
)
EVENT_FIELDS = (
    "id",
    "session_id",
    "run_id",
    "seq",
    "kind",
    "type",
    "role",
    "content",
    "tool_name",
    "tool_call_id",
    "visibility",
    "redaction",
    "payload",
    "created_at",
)


class LoginIn(BaseModel):
    username: str
    password: str


class SessionIn(BaseModel):
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionPatch(BaseModel):
    title: str | None = None
    metadata: dict[str, Any] | None = None
    status: str | None = None


class RunIn(BaseModel):
    prompt: str
    model_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/auth/login")
def login(payload: LoginIn, request: Request, db: DbSession) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    user = db.scalar(select(UserRecord).where(UserRecord.username == payload.username))
    password_matches = user is not None and verify_password(payload.password, user.password_hash)
    if user is None or not user.is_active or not password_matches:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    user.last_login_at = datetime.now(UTC)
    return {"access_token": create_token(user.username, settings), "token_type": "bearer"}


@router.get("/auth/me")
def me(user: CurrentUser) -> dict[str, Any]:
    return _record_out(user, USER_FIELDS)


@router.get("/models")
def models(request: Request, _: CurrentUser) -> dict[str, Any]:
    return model_options(request.app.state.settings)


@router.get("/sessions")
def list_sessions(db: DbSession, user: CurrentUser) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(SessionRecord)
        .where(SessionRecord.owner_user_id == user.id, SessionRecord.status != "archived")
        .order_by(SessionRecord.updated_at.desc())
    ).all()
    return [_record_out(row, SESSION_FIELDS) for row in rows]


@router.post("/sessions", status_code=201)
def create_session(payload: SessionIn, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    session = SessionRecord(
        owner_user_id=user.id,
        title=payload.title or "New session",
        thread_id=f"thread-{uuid4()}",
        metadata_=payload.metadata,
    )
    db.add(session)
    db.flush()
    return _record_out(session, SESSION_FIELDS)


@router.get("/sessions/{session_id}")
def get_session(session_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    return _record_out(_owned_session(db, user, session_id), SESSION_FIELDS)


@router.patch("/sessions/{session_id}")
def update_session(
    session_id: str,
    payload: SessionPatch,
    db: DbSession,
    user: CurrentUser,
) -> dict[str, Any]:
    session = _owned_session(db, user, session_id)
    if payload.title is not None:
        session.title = payload.title
    if payload.metadata is not None:
        session.metadata_ = payload.metadata
    if payload.status is not None:
        if payload.status not in {"idle", "running", "archived", "error"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported session status")
        session.status = payload.status
        session.archived_at = now() if payload.status == "archived" else session.archived_at
    db.flush()
    return _record_out(session, SESSION_FIELDS)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, db: DbSession, user: CurrentUser) -> Response:
    session = _owned_session(db, user, session_id)
    session.status = "archived"
    session.archived_at = now()
    return Response(status_code=204)


@router.get("/sessions/{session_id}/events")
def list_session_events(
    session_id: str,
    db: DbSession,
    user: CurrentUser,
    after_seq: int = 0,
) -> list[dict[str, Any]]:
    _owned_session(db, user, session_id)
    rows = db.scalars(
        select(EventRecord)
        .where(EventRecord.session_id == session_id, EventRecord.seq > after_seq)
        .order_by(EventRecord.seq)
    ).all()
    return [_record_out(row, EVENT_FIELDS) for row in rows]


@router.post("/sessions/{session_id}/runs", status_code=201)
async def create_run(
    session_id: str,
    payload: RunIn,
    request: Request,
    user: CurrentUser,
) -> dict[str, Any]:
    run_id, _ = _start_run(request, session_id, payload, user)
    async for _ in _execute_run_sse(request, run_id):
        pass
    with request.app.state.database.session() as db:
        run = db.get(RunRecord, run_id)
        assert run is not None
        return _record_out(run, RUN_FIELDS)


@router.post("/sessions/{session_id}/runs/stream")
async def stream_run(
    session_id: str,
    payload: RunIn,
    request: Request,
    user: CurrentUser,
) -> StreamingResponse:
    run_id, initial_events = _start_run(request, session_id, payload, user)

    async def generate() -> AsyncIterator[str]:
        for event in initial_events:
            yield sse_payload(event)
        async for chunk in _execute_run_sse(request, run_id):
            yield chunk

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    return _record_out(_owned_run(db, user, run_id), RUN_FIELDS)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    run = _owned_run(db, user, run_id)
    if run.status == "running":
        run.cancel_requested_at = now()
        run.status = "cancelled"
        run.ended_at = now()
        run.session.status = "idle"
        append_event(
            db,
            session_id=run.session_id,
            run_id=run.id,
            kind="run.cancelled",
            type="status",
            payload={"status": "cancelled", "terminal": True},
        )
    db.flush()
    return _record_out(run, RUN_FIELDS)


@router.get("/runs/{run_id}/events")
def list_run_events(run_id: str, db: DbSession, user: CurrentUser) -> list[dict[str, Any]]:
    run = _owned_run(db, user, run_id)
    rows = db.scalars(
        select(EventRecord).where(EventRecord.run_id == run.id).order_by(EventRecord.seq)
    ).all()
    return [_record_out(row, EVENT_FIELDS) for row in rows]


def _start_run(
    request: Request,
    session_id: str,
    payload: RunIn,
    user: UserRecord,
) -> tuple[str, list[EventRecord]]:
    settings: Settings = request.app.state.settings
    with request.app.state.database.session() as db:
        session = db.get(SessionRecord, session_id)
        if session is None or session.owner_user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
        if _active_run_exists(db, session.id):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "A run is already active for this session"
            )
        agent = resolve_agent(settings)
        model_id = default_model_id(settings, payload.model_id or str(agent.get("model") or ""))
        run = RunRecord(
            session_id=session.id,
            user_id=user.id,
            thread_id=session.thread_id,
            agent_id=str(agent["id"]),
            model_id=model_id,
            status="running",
            started_at=now(),
            metadata_=payload.metadata,
        )
        session.status = "running"
        db.add(run)
        db.flush()
        events = _initial_run_events(db, settings, session, run, payload.prompt)
        return run.id, events


async def _execute_run_sse(request: Request, run_id: str) -> AsyncIterator[str]:
    state = _load_run_state(request, run_id)
    settings: Settings = request.app.state.settings
    final_emitted = False
    terminal_emitted = False
    sequence = 5
    context = runtime_agent.DeepAgentsRunContext(
        session_id=state["session_id"],
        run_id=run_id,
        username=state["username"],
        thread_id=state["thread_id"],
        session_metadata=state["metadata"],
    )
    try:
        graph = runtime_agent.build_deep_agent(settings, state["model_id"])
        config = {
            "configurable": {"thread_id": state["thread_id"]},
            "recursion_limit": settings.deepagents_recursion_limit,
        }
        async for raw in graph.astream_events(
            {"messages": [{"role": "user", "content": state["prompt"]}]},
            version=runtime_agent.DEEPAGENTS_EVENT_STREAM_API,
            config=config,
            context=context,
        ):
            envelope = runtime_agent.normalize_runtime_event(
                raw,
                bridge_run_id=run_id,
                session_id=state["session_id"],
                sequence=sequence,
            )
            sequence += 1
            if envelope is None:
                continue
            chunk, final_emitted, terminal_emitted = _persist_runtime_envelope(
                request,
                run_id=run_id,
                session_id=state["session_id"],
                envelope=envelope,
                final_emitted=final_emitted,
                terminal_emitted=terminal_emitted,
            )
            if chunk:
                yield chunk
            await asyncio.sleep(0)
        if not terminal_emitted:
            event = _append_event(
                request,
                session_id=state["session_id"],
                run_id=run_id,
                kind="run.completed",
                type="status",
                payload={"status": "completed", "terminal": True},
            )
            _finish_run(request, run_id, "completed", None)
            yield sse_payload(event)
    except asyncio.CancelledError:
        event = _terminal_event(request, state["session_id"], run_id, "cancelled")
        yield sse_payload(event)
        raise
    except Exception as exc:
        event = _terminal_event(request, state["session_id"], run_id, "failed", str(exc))
        yield sse_payload(event)


def _persist_runtime_envelope(
    request: Request,
    *,
    run_id: str,
    session_id: str,
    envelope: Any,
    final_emitted: bool,
    terminal_emitted: bool,
) -> tuple[str | None, bool, bool]:
    chunks: list[str] = []
    if envelope.label == "assistant.message":
        final_emitted = True

    if _is_main_completion(request, envelope) and envelope.detail and not final_emitted:
        final_emitted = True
        chunks.append(
            _append_event_chunk(
                request,
                session_id=session_id,
                run_id=run_id,
                kind="assistant.message",
                type="message.final",
                role="assistant",
                content=envelope.detail,
                payload={
                    "message": {"role": "assistant", "content": envelope.detail},
                    "source": "run.output",
                },
            )
        )

    if envelope.label == "run.completed":
        envelope.data["status"] = "completed"
        chunks.append(
            _append_event_chunk(
                request,
                session_id=session_id,
                run_id=run_id,
                kind="step.completed",
                type="step",
                content=_event_content(envelope),
                payload=envelope.data,
            )
        )
        return "".join(chunks), final_emitted, terminal_emitted

    if envelope.label == "run.failed":
        terminal_emitted = True
        envelope.data["terminal"] = True

    chunks.append(
        _append_event_chunk(
            request,
            session_id=session_id,
            run_id=run_id,
            kind=envelope.label,
            type=envelope.type,
            role="assistant" if envelope.type in {"message.delta", "message.final"} else None,
            content=_event_content(envelope),
            tool_name=envelope.data.get("tool_name"),
            payload=envelope.data,
        )
    )
    if terminal_emitted:
        _finish_run(request, run_id, "failed", envelope.detail)
    return "".join(chunks), final_emitted, terminal_emitted


def _initial_run_events(
    db: DbSession,
    settings: Settings,
    session: SessionRecord,
    run: RunRecord,
    prompt: str,
) -> list[EventRecord]:
    events = [
        append_event(
            db,
            session_id=session.id,
            run_id=run.id,
            kind="run.started",
            type="status",
            payload={
                "status": "running",
                "run_id": run.id,
                "agent_id": run.agent_id,
                "model_id": run.model_id,
            },
        ),
        append_event(
            db,
            session_id=session.id,
            run_id=run.id,
            kind="user.message",
            type="step",
            role="user",
            content=prompt,
            payload={"message": {"role": "user", "content": prompt}},
        ),
        append_event(
            db,
            session_id=session.id,
            run_id=run.id,
            kind="middleware.applied",
            type="step",
            visibility="internal",
            redaction="hash",
            payload={
                "middleware": "ConfiguredDeepAgentsMiddleware",
                "target": "model_request",
                "operation": "load_configured_middleware",
                "content_hash": _sha256(prompt),
                "redacted": True,
            },
        ),
    ]
    audit = _prompt_audit(db, settings, session.id, run.id, run.agent_id, run.model_id, prompt)
    if audit is not None:
        events.append(audit)
    return events


def _prompt_audit(
    db: DbSession,
    settings: Settings,
    session_id: str,
    run_id: str,
    agent_id: str,
    model_id: str,
    prompt: str,
) -> EventRecord | None:
    mode = settings.audit_compiled_prompts
    if mode == "off":
        return None
    payload: dict[str, Any] = {
        "agent_id": agent_id,
        "model_id": model_id,
        "message_count": 1,
        "system_prompt_hash": _sha256(resolve_agent(settings)["system_prompt"]),
        "compiled_prompt_hash": _sha256(prompt),
        "redaction": mode,
    }
    if mode == "redacted":
        payload["preview"] = "[redacted prompt preview]"
    if mode == "full":
        payload["compiled_prompt"] = prompt
    return append_event(
        db,
        session_id=session_id,
        run_id=run_id,
        kind="prompt.compiled",
        type="step",
        visibility="internal",
        redaction=mode,
        content=prompt if mode == "full" else None,
        payload=payload,
    )


def _load_run_state(request: Request, run_id: str) -> dict[str, Any]:
    with request.app.state.database.session() as db:
        run = db.get(RunRecord, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
        prompt_event = db.scalar(
            select(EventRecord).where(
                EventRecord.run_id == run.id,
                EventRecord.kind == "user.message",
            )
        )
        return {
            "session_id": run.session_id,
            "thread_id": run.thread_id,
            "model_id": run.model_id,
            "username": run.user.username,
            "metadata": run.session.metadata_,
            "prompt": prompt_event.content if prompt_event else "",
        }


def _terminal_event(
    request: Request,
    session_id: str,
    run_id: str,
    status_: str,
    error: str | None = None,
) -> EventRecord:
    event = _append_event(
        request,
        session_id=session_id,
        run_id=run_id,
        kind=f"run.{status_}",
        type="error" if status_ == "failed" else "status",
        content=error,
        payload={"status": status_, "error": error, "terminal": True}
        if error
        else {"status": status_, "terminal": True},
    )
    _finish_run(request, run_id, status_, error)
    return event


def _owned_session(db: DbSession, user: CurrentUser, session_id: str) -> SessionRecord:
    session = db.get(SessionRecord, session_id)
    if session is None or session.owner_user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return session


def _owned_run(db: DbSession, user: CurrentUser, run_id: str) -> RunRecord:
    run = db.get(RunRecord, run_id)
    if run is None or run.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run


def _active_run_exists(db: DbSession, session_id: str) -> bool:
    return (
        db.scalar(
            select(RunRecord).where(
                RunRecord.session_id == session_id,
                RunRecord.status == "running",
            )
        )
        is not None
    )


def _append_event_chunk(request: Request, **kwargs: Any) -> str:
    return sse_payload(_append_event(request, **kwargs))


def _append_event(request: Request, **kwargs: Any) -> EventRecord:
    with request.app.state.database.session() as db:
        return append_event(db, **kwargs)


def _finish_run(request: Request, run_id: str, status_: str, error: str | None) -> None:
    with request.app.state.database.session() as db:
        run = db.get(RunRecord, run_id)
        if run is None or run.status != "running":
            return
        run.status = status_
        run.ended_at = now()
        run.error_message = error
        run.session.status = "error" if status_ == "failed" else "idle"


def _is_main_completion(request: Request, envelope: Any) -> bool:
    settings: Settings = request.app.state.settings
    return bool(
        envelope.label == "run.completed"
        and envelope.data.get("node") == settings.deepagents_agent_name
    )


def _event_content(envelope: Any) -> str | None:
    value = envelope.data.get("delta") or envelope.data.get("text") or envelope.data.get("error")
    if value is None:
        value = envelope.detail
    return str(value) if value else None


def _record_out(record: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in fields:
        value = getattr(record, "metadata_" if field == "metadata" else field)
        payload[field] = value.isoformat() if hasattr(value, "isoformat") else value
    return payload


def sse_payload(event: EventRecord) -> str:
    payload = {
        "id": str(event.seq),
        "event_id": str(event.seq),
        "type": event.type,
        "run_id": event.run_id,
        "session_id": event.session_id,
        "timestamp": event.created_at.isoformat(),
        "label": event.kind,
        "detail": event.content or event.tool_name or event.kind,
        "data": event.payload,
    }
    return f"id: {event.seq}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()
