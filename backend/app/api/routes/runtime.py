from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import AdminUserDep
from app.core.config import Settings

router = APIRouter()


@router.get("/runtime/options")
def get_runtime_options(request: Request, _: AdminUserDep) -> dict[str, Any]:
    settings = cast(Settings, request.app.state.settings)
    try:
        return settings.runtime_options()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

