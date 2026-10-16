"""application/problem+json error envelope (contract C8)."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("kb_api.errors")
PROBLEM = "application/problem+json"


def problem(
    request: Request,
    status: int,
    title: str,
    detail: str | None = None,
    extra: dict[str, object] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "correlation_id": getattr(request.state, "correlation_id", None),
    }
    if detail:
        body["detail"] = detail
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        resp = problem(request, exc.status_code, _title(exc.status_code), str(exc.detail))
        for k, v in (exc.headers or {}).items():
            resp.headers[k] = v
        return resp

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errs = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return problem(request, 422, "Validation error", extra={"errors": errs})

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", extra={"path": request.url.path})
        return problem(request, 500, "Internal Server Error")  # no internals leaked


def _title(status: int) -> str:
    return {
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        400: "Bad Request",
        501: "Not Implemented",
    }.get(status, "Error")
