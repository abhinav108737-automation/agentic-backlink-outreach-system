"""Correlation IDs and browser-origin rejection."""

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from kb_api.context import correlation_id_var, new_correlation_id

Next = Callable[[Request], Awaitable[Response]]


async def correlation_middleware(request: Request, call_next: Next) -> Response:
    cid = new_correlation_id(request.headers.get("x-correlation-id"))
    token = correlation_id_var.set(cid)
    request.state.correlation_id = cid
    try:
        response = await call_next(request)
    finally:
        correlation_id_var.reset(token)
    response.headers["X-Correlation-ID"] = cid
    return response


async def reject_browser_origin(request: Request, call_next: Next) -> Response:
    """The renderer talks to main over IPC; main's fetch carries no Origin. Any Origin header
    means a web page is calling us directly, which is never legitimate (CORS is deny-all)."""
    if "origin" in request.headers:
        return JSONResponse(
            {
                "type": "about:blank",
                "title": "Forbidden",
                "status": 403,
                "detail": "Cross-origin requests are not permitted",
            },
            status_code=403,
            media_type="application/problem+json",
        )
    return await call_next(request)
