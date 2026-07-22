"""Safe HTTP error helpers — never expose raw exception strings to clients."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


def new_request_id() -> str:
    return uuid.uuid4().hex


def safe_http_exception(
    status_code: int,
    *,
    code: str,
    message: str,
    request_id: Optional[str] = None,
) -> HTTPException:
    detail: Dict[str, Any] = {"code": code, "message": message}
    if request_id:
        detail["request_id"] = request_id
    return HTTPException(status_code=status_code, detail=detail)


def safe_error_response(
    status_code: int,
    *,
    code: str,
    message: str,
    request_id: Optional[str] = None,
) -> JSONResponse:
    body: Dict[str, Any] = {"ok": False, "code": code, "message": message}
    if request_id:
        body["request_id"] = request_id
    headers = {"X-Request-Id": request_id} if request_id else {}
    return JSONResponse(status_code=status_code, content=body, headers=headers)


async def attach_request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id") or new_request_id()
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response
