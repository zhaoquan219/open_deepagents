from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import AdminUserDep, DatabaseSessionDep, DatabaseStateDep
from app.core.auth import decode_access_token
from app.core.config import Settings
from app.core.runtime_catalog import RuntimeSelection
from app.core.session_scope import get_run_for_user, get_session_for_user
from app.schemas.run import RunCreate, RunRead
from app.services.run_attachments import InvalidRunAttachmentError
from app.services.runs import RunService
from app.services.runtime_overrides import InvalidRuntimeOverrideError, normalize_persisted_extra

router = APIRouter()


def get_run_service(request: Request) -> RunService:
    return cast(RunService, request.app.state.run_service)


@router.post("/runs", response_model=RunRead, status_code=status.HTTP_201_CREATED)
async def create_run(
    payload: RunCreate,
    request: Request,
    username: AdminUserDep,
    db: DatabaseSessionDep,
) -> RunRead:
    settings = cast(Settings, request.app.state.settings)
    get_session_for_user(
        db,
        session_id=payload.session_id,
        username=username,
        settings=settings,
    )

    try:
        normalized_extra = normalize_persisted_extra(payload.extra)
        run_state = get_run_service(request).start_run(
            settings=settings,
            session_id=payload.session_id,
            prompt=payload.prompt,
            attachments=payload.attachments,
            extra=normalized_extra,
            runtime_selection=RuntimeSelection(
                model_id=payload.model_id,
                subagent_profile_id=payload.subagent_profile_id,
            ),
        )
    except InvalidRunAttachmentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except InvalidRuntimeOverrideError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RunRead(
        run_id=run_state.run_id,
        session_id=run_state.session_id,
        status=run_state.status,
        extra=normalized_extra,
        created_at=run_state.created_at,
    )


@router.get("/runs/{run_id}", response_model=RunRead)
def get_run(
    run_id: str,
    request: Request,
    username: AdminUserDep,
    db: DatabaseSessionDep,
) -> RunRead:
    settings = cast(Settings, request.app.state.settings)
    get_run_for_user(db, run_id=run_id, username=username, settings=settings)
    try:
        run_state = get_run_service(request).get_run(run_id=run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
    return RunRead(
        run_id=run_state.run_id,
        session_id=run_state.session_id,
        status=run_state.status,
        extra=getattr(run_state, "extra", {}) or {},
        created_at=run_state.created_at,
    )


@router.post("/runs/{run_id}/cancel", response_model=RunRead)
def cancel_run(
    run_id: str,
    request: Request,
    username: AdminUserDep,
    db: DatabaseSessionDep,
) -> RunRead:
    settings = cast(Settings, request.app.state.settings)
    get_run_for_user(db, run_id=run_id, username=username, settings=settings)
    try:
        run_state = get_run_service(request).cancel_run(run_id=run_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
    return RunRead(
        run_id=run_state.run_id,
        session_id=run_state.session_id,
        status=run_state.status,
        extra=getattr(run_state, "extra", {}) or {},
        created_at=run_state.created_at,
    )


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    request: Request,
    database: DatabaseStateDep,
    last_event_id: str | None = None,
    access_token: str | None = Query(default=None),
    last_event_id_header: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    settings = cast(Settings, request.app.state.settings)
    if settings.admin_auth_enabled and access_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required",
        )
    if settings.admin_auth_enabled:
        username = decode_access_token(settings, access_token or "")
    else:
        username = settings.admin_username

    with database.session_factory() as db:
        get_run_for_user(
            db,
            run_id=run_id,
            username=username,
            settings=settings,
        )

    resume_from = last_event_id or last_event_id_header

    return StreamingResponse(
        get_run_service(request).stream(run_id=run_id, last_event_id=resume_from),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
