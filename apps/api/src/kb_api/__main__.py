"""Backend entrypoint used by Electron main.

Handshake (ADR-0014): the parent writes the per-launch token as the first line on stdin.
Once listening on 127.0.0.1:<free port>, we print exactly `READY port=<n>` on stdout.
stdout is reserved for that line; logs go to stderr. Parent death (stdin EOF) stops the server.
"""

import asyncio
import sys
import threading

import uvicorn
from kb_contracts.config import Settings
from kb_contracts.logging import configure_logging
from kb_core.db.engine import create_db_engine
from kb_core.db.uow import create_uow_factory

from kb_api.app import create_app
from kb_api.auth import MIN_TOKEN_CHARS


def _read_token() -> str:
    token = sys.stdin.readline().strip()
    if len(token) < MIN_TOKEN_CHARS:
        print("FATAL missing or short token on stdin", file=sys.stderr)  # never echo the value
        raise SystemExit(2)
    return token


async def serve() -> None:
    settings = Settings()
    token = _read_token()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings)
    app = create_app(settings, token, uow_factory=create_uow_factory(engine))
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_config=None, access_log=False, lifespan="on"
    )
    server = uvicorn.Server(config)

    def _watch_parent() -> None:
        while sys.stdin.readline():  # drain; EOF means the parent is gone
            pass
        server.should_exit = True

    threading.Thread(target=_watch_parent, daemon=True).start()

    task = asyncio.create_task(server.serve())
    while not server.started and not task.done():
        await asyncio.sleep(0.02)
    if server.started:
        port = server.servers[0].sockets[0].getsockname()[1]
        print(f"READY port={port}", flush=True)
    try:
        await task
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
