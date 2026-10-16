"""Per-launch bearer-token auth (ADR-0014). Constant-time comparison; token never logged."""

import hmac
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request

MIN_TOKEN_CHARS = 32


def make_token_dependency(token: str) -> Callable[[Request], Awaitable[None]]:
    if len(token) < MIN_TOKEN_CHARS:
        raise ValueError(f"API token must be at least {MIN_TOKEN_CHARS} characters")
    expected = token.encode()

    async def require_token(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, supplied = header.partition(" ")
        # compare_digest on bytes: constant time w.r.t. content; always executes the compare
        ok = scheme.lower() == "bearer" and hmac.compare_digest(supplied.encode(), expected)
        if not ok:
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return require_token
