"""ADR-0014 handshake against the real backend process (the same one Electron spawns)."""

import os
import queue
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest
from kb_contracts.config import Settings

pytestmark = [pytest.mark.db, pytest.mark.req("SEC-01")]


def _readline_with_timeout(p: subprocess.Popen[str], seconds: float) -> str:
    q: queue.Queue[str] = queue.Queue()
    assert p.stdout
    threading.Thread(target=lambda: q.put(p.stdout.readline()), daemon=True).start()  # type: ignore[union-attr]
    try:
        return q.get(timeout=seconds)
    except queue.Empty:
        p.kill()
        raise AssertionError(f"backend did not print READY within {seconds}s") from None


@contextmanager
def backend(
    token: str, env_extra: dict[str, str] | None = None
) -> Iterator[tuple[subprocess.Popen[str], int]]:
    env = {**os.environ, **(env_extra or {})}
    err_file = tempfile.TemporaryFile("w+")  # noqa: SIM115 - must outlive this scope; never fills a pipe
    p = subprocess.Popen(
        [sys.executable, "-m", "kb_api"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=err_file,
        text=True,
        env=env,
    )
    p.err_file = err_file  # type: ignore[attr-defined]
    try:
        assert p.stdin and p.stdout
        p.stdin.write(token + "\n")
        p.stdin.flush()
        line = _readline_with_timeout(p, 30).strip()
        m = re.fullmatch(r"READY port=(\d+)", line)
        assert m, f"unexpected handshake line: {line!r}"
        yield p, int(m.group(1))
    finally:
        if p.poll() is None:
            p.kill()
        p.wait(timeout=10)


def test_handshake_auth_loopback_only_and_clean_logs(pg_available):
    token = secrets.token_urlsafe(48)
    with backend(token) as (p, port):
        base = f"http://127.0.0.1:{port}"
        assert httpx.get(f"{base}/health/live").json() == {"status": "ok"}
        assert httpx.get(f"{base}/health/ready").status_code == 401
        assert (
            httpx.get(f"{base}/health/ready", headers={"Authorization": "Bearer nope"}).status_code
            == 401
        )
        r = httpx.get(f"{base}/health/ready", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code in (200, 503)
        names = {c["name"] for c in r.json()["components"]}
        assert names == {"postgresql", "qdrant", "ollama"}
        if pg_available:
            pg = next(c for c in r.json()["components"] if c["name"] == "postgresql")
            assert pg["status"] == "ok"
            assert r.json()["status"] in ("ok", "degraded")

        # bound to loopback only (never 0.0.0.0 / external)
        lsof = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True
        ).stdout
        assert f"127.0.0.1:{port}" in lsof and "*:" not in lsof and "0.0.0.0" not in lsof

        # parent "dies" (stdin closed) -> backend exits by itself
        assert p.stdin
        p.stdin.close()
        deadline = time.time() + 10
        while p.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        assert p.poll() == 0, "backend must exit when its parent's stdin closes"
        p.err_file.seek(0)  # type: ignore[attr-defined]
        err = p.err_file.read()  # type: ignore[attr-defined]
        out_rest = p.stdout.read() if p.stdout else ""
    assert token not in err and token not in out_rest
    assert out_rest == "", "stdout is reserved for the READY line"


def test_missing_token_aborts_without_echoing_anything_sensitive():
    p = subprocess.run(
        [sys.executable, "-m", "kb_api"],
        input="zq7-sentinel-9\n",
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert p.returncode == 2
    assert "zq7-sentinel-9" not in p.stdout + p.stderr and "READY" not in p.stdout


def test_ready_reports_degraded_when_optional_services_are_down(pg_available):
    if not pg_available:
        pytest.skip("dev PostgreSQL not reachable")
    token = secrets.token_urlsafe(48)
    env = {"OLLAMA_URL": "http://127.0.0.1:9", "QDRANT_URL": "http://127.0.0.1:9"}
    with backend(token, env) as (_, port):
        r = httpx.get(
            f"http://127.0.0.1:{port}/health/ready", headers={"Authorization": f"Bearer {token}"}
        )
    body = r.json()
    assert r.status_code == 200 and body["status"] == "degraded"
    st = {c["name"]: c["status"] for c in body["components"]}
    assert st == {"postgresql": "ok", "qdrant": "down", "ollama": "down"}


def test_defaults_are_safe_in_the_running_process_config():
    s = Settings(_env_file=None)
    assert s.real_send_enabled is False and s.autonomy_mode.value == "shadow"
