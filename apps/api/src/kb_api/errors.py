"""application/problem+json error envelope (contract C8)."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from kb_contracts.state_machines import InvalidTransition
from kb_core.db.page import InvalidCursor
from kb_core.db.protocols import OptimisticLockError
from kb_core.errors import (
    DomainConflict,
    EntityNotFound,
    GuardFailed,
    IdempotencyKeyReused,
    InvalidInput,
    PolicyBlocked,
    PolicyUnavailable,
)
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

    _install_domain_handlers(app)

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


def _install_domain_handlers(app: FastAPI) -> None:
    """Map service-layer errors to problem+json so an expected failure is never a 500."""

    @app.exception_handler(InvalidTransition)
    async def _invalid_transition(request: Request, exc: InvalidTransition) -> JSONResponse:
        return problem(
            request,
            409,
            "Illegal state transition",
            str(exc),
            {
                "code": "INVALID_TRANSITION",
                "machine": exc.machine,
                "from_state": exc.src.value,
                "to_state": exc.dst.value,
            },
        )

    @app.exception_handler(PolicyBlocked)
    async def _blocked(request: Request, exc: PolicyBlocked) -> JSONResponse:
        return problem(
            request,
            409,
            "Blocked by policy",
            str(exc),
            {"code": exc.code, "block_codes": [c.value for c in exc.decision.block_codes]},
        )

    @app.exception_handler(GuardFailed)
    async def _guard(request: Request, exc: GuardFailed) -> JSONResponse:
        return problem(request, 409, "Transition guard failed", str(exc), {"code": exc.code})

    @app.exception_handler(DomainConflict)
    async def _conflict(request: Request, exc: DomainConflict) -> JSONResponse:
        return problem(request, 409, "Conflict", str(exc), {"code": exc.code})

    @app.exception_handler(OptimisticLockError)
    async def _lock(request: Request, exc: OptimisticLockError) -> JSONResponse:
        return problem(
            request,
            409,
            "Version conflict",
            "the resource changed; reload and retry",
            {"code": "VERSION_CONFLICT"},
        )

    @app.exception_handler(EntityNotFound)
    async def _missing(request: Request, exc: EntityNotFound) -> JSONResponse:
        return problem(request, 404, "Not Found", str(exc))

    @app.exception_handler(InvalidInput)
    async def _invalid(request: Request, exc: InvalidInput) -> JSONResponse:
        return problem(request, 422, "Invalid input", str(exc), {"code": "INVALID_INPUT"})

    @app.exception_handler(IdempotencyKeyReused)
    async def _reused(request: Request, exc: IdempotencyKeyReused) -> JSONResponse:
        return problem(
            request, 422, "Idempotency-Key reused", str(exc), {"code": "IDEMPOTENCY_KEY_REUSED"}
        )

    @app.exception_handler(InvalidCursor)
    async def _cursor(request: Request, exc: InvalidCursor) -> JSONResponse:
        return problem(request, 400, "Bad Request", "invalid pagination cursor")

    @app.exception_handler(PolicyUnavailable)
    async def _no_policy(request: Request, exc: PolicyUnavailable) -> JSONResponse:
        return problem(request, 503, "No effective policy", None, {"code": "POLICY_UNAVAILABLE"})
