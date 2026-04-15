from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import DatabaseSessionDep, require_admin
from app.db.models import SessionStateRecord
from app.schemas.session_state import (
    SessionStateConsumeRequest,
    SessionStateConsumeResponse,
    SessionStatePatch,
    SessionStatePut,
    SessionStateRead,
)
from app.services.session_state import (
    SessionStateMergeError,
    SessionStateService,
    SessionStateSessionNotFoundError,
)

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/sessions/{session_id}/state", response_model=list[SessionStateRead])
def list_session_states(
    session_id: str,
    db: DatabaseSessionDep,
    namespace: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    include_expired: bool = Query(default=False),
) -> list[SessionStateRecord]:
    service = SessionStateService(db)
    try:
        service.ensure_session_exists(session_id)
    except SessionStateSessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return service.list(
        session_id=session_id,
        namespace=namespace,
        status=status_filter,
        include_expired=include_expired,
    )


@router.get(
    "/sessions/{session_id}/state/{namespace}/{key}",
    response_model=SessionStateRead,
)
def get_session_state(
    session_id: str,
    namespace: str,
    key: str,
    db: DatabaseSessionDep,
    include_expired: bool = Query(default=False),
) -> SessionStateRecord:
    record = SessionStateService(db).get(
        session_id=session_id,
        namespace=namespace,
        key=key,
        include_expired=include_expired,
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session state not found")
    return record


@router.put(
    "/sessions/{session_id}/state/{namespace}/{key}",
    response_model=SessionStateRead,
)
def put_session_state(
    session_id: str,
    namespace: str,
    key: str,
    payload: SessionStatePut,
    db: DatabaseSessionDep,
) -> SessionStateRecord:
    service = SessionStateService(db)
    try:
        record = service.put(
            session_id=session_id,
            namespace=namespace,
            key=key,
            status=payload.status,
            consume_policy=payload.consume_policy,
            value=payload.value,
            expires_at=payload.expires_at,
        )
    except SessionStateSessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return record


@router.patch(
    "/sessions/{session_id}/state/{namespace}/{key}",
    response_model=SessionStateRead,
)
def patch_session_state(
    session_id: str,
    namespace: str,
    key: str,
    payload: SessionStatePatch,
    db: DatabaseSessionDep,
) -> SessionStateRecord:
    service = SessionStateService(db)
    try:
        record = service.patch(
            session_id=session_id,
            namespace=namespace,
            key=key,
            status=payload.status,
            consume_policy=payload.consume_policy,
            value=payload.value,
            expires_at=payload.expires_at,
            expires_at_is_set="expires_at" in payload.model_fields_set,
        )
    except SessionStateMergeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session state not found")
    db.commit()
    return record


@router.delete(
    "/sessions/{session_id}/state/{namespace}/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_session_state(
    session_id: str,
    namespace: str,
    key: str,
    db: DatabaseSessionDep,
) -> Response:
    deleted = SessionStateService(db).delete(
        session_id=session_id,
        namespace=namespace,
        key=key,
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session state not found")
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/sessions/{session_id}/state/{namespace}/{key}/consume",
    response_model=SessionStateConsumeResponse,
)
def consume_session_state(
    session_id: str,
    namespace: str,
    key: str,
    payload: SessionStateConsumeRequest,
    db: DatabaseSessionDep,
) -> SessionStateConsumeResponse:
    result = SessionStateService(db).consume(
        session_id=session_id,
        namespace=namespace,
        key=key,
        run_id=payload.run_id,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session state not found")
    db.commit()
    return SessionStateConsumeResponse(
        outcome=result.outcome,
        state=SessionStateRead.model_validate(result.state),
    )
