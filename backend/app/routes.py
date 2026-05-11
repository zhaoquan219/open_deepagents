from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from agents.middleware.audit_middleware import attachment_context_content
from app import agent as runtime_agent
from app.auth import CurrentUser, DbSession, create_token, verify_password
from app.catalog import default_model_id, model_options, resolve_agent
from app.db import EventRecord, RunRecord, SessionRecord, UserRecord, append_event, now
from app.settings import Settings

router = APIRouter()
LOGGER = logging.getLogger("app.routes")
PLACEHOLDER_SESSION_TITLES = {"", "new session", "untitled", "untitled session", "新会话"}
MAX_DERIVED_TITLE_CHARS = 32
TRANSCRIPT_MESSAGE_KINDS = ("system.message", "user.message", "assistant.message")

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
    attachments: list[dict[str, Any]] = Field(default_factory=list)
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


@router.post("/sessions/{session_id}/uploads", status_code=201)
async def upload_session_file(
    session_id: str,
    file: UploadFile,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> dict[str, Any]:
    _owned_session(db, user, session_id)
    if not file.filename:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Upload filename is required")
    record = _store_upload(
        settings=request.app.state.settings,
        user=user,
        session_id=session_id,
        filename=file.filename,
        source=file.file,
    )
    return record


@router.delete("/uploads/{upload_id}", status_code=204)
def delete_upload(
    upload_id: str,
    request: Request,
    user: CurrentUser,
) -> Response:
    upload_target = _find_upload_target(request.app.state.settings, user, upload_id)
    if upload_target is not None:
        if upload_target.is_dir():
            shutil.rmtree(upload_target)
        else:
            upload_target.unlink(missing_ok=True)
    return Response(status_code=204)


@router.get("/uploads/{upload_id}/content")
def download_upload(
    upload_id: str,
    request: Request,
    user: CurrentUser,
) -> FileResponse:
    upload_target = _find_upload_target(request.app.state.settings, user, upload_id)
    if upload_target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    upload_file = _upload_file_from_target(upload_target)
    if upload_file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload file not found")
    return FileResponse(upload_file, filename=upload_file.name)


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
        if _is_placeholder_title(session.title):
            session.title = _derive_session_title(payload.prompt)
        db.add(run)
        db.flush()
        attachments = _resolve_run_attachments(settings, user, session.id, payload.attachments)
        events = _initial_run_events(
            db,
            settings,
            session,
            run,
            payload.prompt,
            attachments,
        )
        LOGGER.info(
            "run started run_id=%s session_id=%s model_id=%s attachments=%s",
            run.id,
            session.id,
            model_id,
            len(attachments),
        )
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
        current_attachments=tuple(state["attachments"]),
        attachments=tuple(state["attachments"]),
    )
    try:
        graph = runtime_agent.build_deep_agent(settings, state["model_id"])
        config = {
            "configurable": {"thread_id": state["thread_id"]},
            "recursion_limit": settings.deepagents_recursion_limit,
        }
        messages = await _runtime_input_messages(settings, config, state)
        LOGGER.debug(
            "agent input run_id=%s session_id=%s messages=%s",
            run_id,
            state["session_id"],
            _json_for_log(messages),
        )
        async for raw in graph.astream_events(
            {"messages": messages},
            config=config,
            context=context,
        ):
            LOGGER.debug(
                "agent update run_id=%s session_id=%s raw_event=%s",
                run_id,
                state["session_id"],
                _json_for_log(raw),
            )
            envelope = runtime_agent.normalize_runtime_event(
                raw,
                bridge_run_id=run_id,
                session_id=state["session_id"],
                sequence=sequence,
            )
            sequence += 1
            if envelope is None:
                continue
            _log_runtime_envelope(run_id, state["session_id"], envelope)
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
        LOGGER.info("run cancelled run_id=%s session_id=%s", run_id, state["session_id"])
        event = _terminal_event(request, state["session_id"], run_id, "cancelled")
        yield sse_payload(event)
        raise
    except Exception as exc:
        LOGGER.exception("run failed run_id=%s session_id=%s", run_id, state["session_id"])
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
    attachments: list[dict[str, Any]],
) -> list[EventRecord]:
    attachment_context = attachment_context_content(tuple(attachments))
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
            payload={
                "message": {"role": "user", "content": prompt},
                "attachments": attachments,
            },
        ),
    ]
    if attachment_context:
        events.append(
            append_event(
                db,
                session_id=session.id,
                run_id=run.id,
                kind="system.message",
                type="step",
                role="system",
                content=attachment_context,
                visibility="internal",
                payload={
                    "message": {"role": "system", "content": attachment_context},
                    "source": "middleware.InjectAttachmentContextMessage",
                    "attachment_count": len(attachments),
                    "attachment_paths": [attachment["path"] for attachment in attachments],
                },
            )
        )
    events.append(
        append_event(
            db,
            session_id=session.id,
            run_id=run.id,
            kind="middleware.applied",
            type="step",
            visibility="internal",
            redaction="hash",
            payload={
                "middleware": "DeepAgentsRunContext",
                "target": "runtime.context",
                "operation": "attach_upload_metadata",
                "content_hash": _sha256(prompt),
                "attachment_count": len(attachments),
                "attachment_paths": [attachment["path"] for attachment in attachments],
                "redacted": True,
            },
        )
    )
    audit = _prompt_audit(
        db,
        settings,
        session.id,
        run.id,
        run.agent_id,
        run.model_id,
        prompt,
        attachments,
    )
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
    attachments: list[dict[str, Any]],
) -> EventRecord | None:
    mode = settings.audit_compiled_prompts
    if mode == "off":
        return None
    payload: dict[str, Any] = {
        "agent_id": agent_id,
        "model_id": model_id,
        "message_count": 1 + int(bool(attachments)),
        "system_prompt_hash": _sha256(resolve_agent(settings)["system_prompt"]),
        "compiled_prompt_hash": _sha256(prompt),
        "context": {
            "attachment_count": len(attachments),
            "attachments": [
                {
                    "id": attachment.get("id"),
                    "name": attachment.get("name"),
                    "path": attachment.get("path"),
                    "size": attachment.get("size"),
                }
                for attachment in attachments
            ],
        },
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
            "messages": _session_transcript_until(db, run, prompt_event),
            "attachments": _event_attachments(prompt_event),
        }


async def _runtime_input_messages(
    settings: Settings,
    config: dict[str, Any],
    state: dict[str, Any],
) -> list[dict[str, str]]:
    current = [{"role": "user", "content": str(state["prompt"])}]
    if await _runtime_checkpoint_exists(settings, config):
        return current
    messages = state.get("messages")
    if isinstance(messages, list) and messages:
        return messages
    return current


async def _runtime_checkpoint_exists(settings: Settings, config: dict[str, Any]) -> bool:
    checkpointer = settings.runtime_checkpointer()
    aget_tuple = getattr(checkpointer, "aget_tuple", None)
    if callable(aget_tuple):
        try:
            return await aget_tuple(config) is not None
        except Exception:
            return False
    get_tuple = getattr(checkpointer, "get_tuple", None)
    if not callable(get_tuple):
        return False
    try:
        return get_tuple(config) is not None
    except Exception:
        return False


def _session_transcript_until(
    db: DbSession,
    run: RunRecord,
    prompt_event: EventRecord | None,
) -> list[dict[str, str]]:
    if prompt_event is None:
        return []
    rows = db.scalars(
        select(EventRecord)
        .where(
            EventRecord.session_id == run.session_id,
            EventRecord.seq <= prompt_event.seq,
            EventRecord.kind.in_(TRANSCRIPT_MESSAGE_KINDS),
        )
        .order_by(EventRecord.seq)
    ).all()
    messages = [_message_from_event(row) for row in rows]
    return [message for message in messages if message is not None]


def _message_from_event(event: EventRecord) -> dict[str, str] | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    raw_message = payload.get("message")
    payload_message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}
    role = str(event.role or payload_message.get("role") or "")
    content = _event_message_content(event, payload_message)
    if role not in {"system", "user", "assistant"} or not content:
        return None
    return {"role": role, "content": content}


def _event_message_content(event: EventRecord, payload_message: dict[str, Any]) -> str:
    value = event.content
    if value is None:
        value = payload_message.get("content")
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _event_attachments(event: EventRecord | None) -> list[dict[str, Any]]:
    payload = event.payload if event is not None and isinstance(event.payload, dict) else {}
    attachments = payload.get("attachments")
    return list(attachments) if isinstance(attachments, list) else []


def _resolve_run_attachments(
    settings: Settings,
    user: UserRecord,
    session_id: str,
    attachments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    resolved = []
    seen_upload_ids: set[str] = set()
    for raw in attachments:
        upload_id = str(raw.get("id") or "")
        if not upload_id or upload_id in seen_upload_ids:
            continue
        seen_upload_ids.add(upload_id)
        upload_target = _find_upload_target(settings, user, upload_id, session_id=session_id)
        if upload_target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Upload not found: {upload_id}")
        file_path = _upload_file_from_target(upload_target)
        if file_path is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Upload file not found: {upload_id}")
        resolved.append(
            {
                "id": upload_id,
                "name": file_path.name,
                "size": int(file_path.stat().st_size),
                "status": "uploaded",
                "path": _upload_model_path(settings, file_path),
            }
        )
    return resolved


def _store_upload(
    *,
    settings: Settings,
    user: UserRecord,
    session_id: str,
    filename: str,
    source: Any,
) -> dict[str, Any]:
    safe_name = _safe_filename(filename)
    upload_root = settings.upload_root_dir()
    upload_dir = upload_root / _session_path_segment(session_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / safe_name
    with destination.open("wb") as target:
        shutil.copyfileobj(source, target)
    (upload_dir / ".owner.json").write_text(
        json.dumps(
            {
                "user_id": user.id,
                "user_name": user.username,
                "session_id": session_id,
                "session_ref": _session_path_segment(session_id),
                "filename": safe_name,
            }
        ),
        encoding="utf-8",
    )
    upload_id = _upload_record_id(session_id, safe_name)
    return {
        "id": upload_id,
        "name": safe_name,
        "size": int(destination.stat().st_size),
        "status": "uploaded",
        "path": _upload_model_path(settings, destination),
        "download_url": f"/api/uploads/{upload_id}/content",
    }


def _find_upload_target(
    settings: Settings,
    user: UserRecord,
    upload_id: str,
    *,
    session_id: str | None = None,
) -> Path | None:
    if "/" in upload_id or "\\" in upload_id or not upload_id:
        return None
    root = settings.upload_root_dir()
    new_upload = _new_upload_candidate(root, upload_id, session_id)
    if new_upload is not None and new_upload.is_file() and _upload_owned_by(
        new_upload.parent,
        user,
        session_id,
    ):
        return new_upload
    for candidate in _upload_dir_candidates(root, user, upload_id, session_id):
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_dir() and _upload_owned_by(candidate, user, session_id):
            return candidate
    return None


def _upload_dir_candidates(
    root: Path,
    user: UserRecord,
    upload_id: str,
    session_id: str | None,
) -> list[Path]:
    candidates: list[Path] = []
    if session_id:
        session_ref = _session_path_segment(session_id)
        candidates.append(root / _user_path_segment(user) / session_ref / upload_id)
        candidates.extend(root.glob(f"*/{session_ref}/{upload_id}"))
        candidates.append(root / session_id / upload_id)
    else:
        candidates.extend(root.glob(f"*/*/{upload_id}"))
        candidates.extend(root.glob(f"*/{upload_id}"))
    return candidates


def _new_upload_candidate(root: Path, upload_id: str, session_id: str | None) -> Path | None:
    session_ref = ""
    filename = ""
    if ":" in upload_id:
        session_ref, _, filename = upload_id.partition(":")
    elif session_id:
        session_ref = _session_path_segment(session_id)
        filename = upload_id
    if not session_ref or not filename or _safe_lookup_name(session_ref) != session_ref:
        return None
    safe_name = _safe_lookup_name(filename)
    if not safe_name:
        return None
    return root / session_ref / safe_name


def _upload_record_id(session_id: str, filename: str) -> str:
    return f"{_session_path_segment(session_id)}:{filename}"


def _upload_model_path(settings: Settings, file_path: Path) -> str:
    relative = file_path.relative_to(settings.upload_root_dir())
    return "/uploads/" + "/".join(relative.parts)


def _user_path_segment(user: UserRecord) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", user.username.strip()).strip(".-")
    return cleaned[:64] or "user"


def _session_path_segment(session_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", session_id)
    return (cleaned or session_id.replace("/", "")).lower()[:8]


def _upload_owned_by(candidate: Path, user: UserRecord, session_id: str | None) -> bool:
    metadata_path = candidate / ".owner.json"
    if not metadata_path.is_file():
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if metadata.get("user_id") != user.id:
        return False
    return session_id is None or metadata.get("session_id") == session_id


def _upload_files(upload_dir: Path) -> list[Path]:
    return sorted(
        path for path in upload_dir.iterdir() if path.is_file() and not path.name.startswith(".")
    )


def _upload_file_from_target(upload_target: Path) -> Path | None:
    if upload_target.is_file():
        return upload_target
    files = _upload_files(upload_target)
    return files[0] if files else None


def _safe_filename(filename: str) -> str:
    cleaned = Path(filename).name.strip().replace("\x00", "")
    if not cleaned or cleaned in {".", ".."}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid upload filename")
    return cleaned


def _safe_lookup_name(filename: str) -> str:
    cleaned = Path(filename).name.strip().replace("\x00", "")
    if not cleaned or cleaned in {".", ".."}:
        return ""
    return cleaned


def _is_placeholder_title(value: str | None) -> bool:
    return " ".join(str(value or "").split()).lower() in PLACEHOLDER_SESSION_TITLES


def _derive_session_title(prompt: str) -> str:
    lines = [" ".join(line.split()) for line in prompt.splitlines()]
    title = next((line for line in lines if line), "")
    if not title:
        return "New session"
    if len(title) <= MAX_DERIVED_TITLE_CHARS:
        return title
    return f"{title[: MAX_DERIVED_TITLE_CHARS - 3].rstrip()}..."


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
        LOGGER.info(
            "run finished run_id=%s session_id=%s status=%s error=%s",
            run_id,
            run.session_id,
            status_,
            error or "",
        )


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


def _log_runtime_envelope(run_id: str, session_id: str, envelope: Any) -> None:
    if envelope.type == "message.delta":
        LOGGER.debug(
            "agent delta run_id=%s session_id=%s detail=%s",
            run_id,
            session_id,
            envelope.detail,
        )
        return
    LOGGER.info(
        "agent event run_id=%s session_id=%s label=%s type=%s detail=%s",
        run_id,
        session_id,
        envelope.label,
        envelope.type,
        envelope.detail,
    )


def _json_for_log(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return repr(value)


def _record_out(record: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in fields:
        value = getattr(record, "metadata_" if field == "metadata" else field)
        payload[field] = value.isoformat() if hasattr(value, "isoformat") else value
    return payload


def sse_payload(event: EventRecord) -> str:
    data = dict(event.payload or {})
    if event.type == "message.final":
        message = dict(data.get("message") or {})
        message.setdefault("id", event.id)
        message.setdefault("created_at", event.created_at.isoformat())
        data["message"] = message
    payload = {
        "id": str(event.seq),
        "event_id": str(event.seq),
        "type": event.type,
        "run_id": event.run_id,
        "session_id": event.session_id,
        "timestamp": event.created_at.isoformat(),
        "label": event.kind,
        "detail": event.content or event.tool_name or event.kind,
        "data": data,
    }
    return f"id: {event.seq}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()
