"""Shared fixture plumbing for the Lane E mock providers (C12 corpus, ADR-0015/0020).

Mock providers are offline by construction: they read the committed fixture corpus under
``fixtures/`` and never open a socket. The fixture root is injectable so tests can point a
provider at a temporary corpus.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from kb_contracts.providers import InvalidResponse, NotConfigured

FIXTURE_ROOT_ENV = "KB_FIXTURE_ROOT"
"""Environment override for the fixture corpus root."""


def default_fixture_root() -> Path:
    """Resolve the repository fixture corpus root.

    Resolution order: ``KB_FIXTURE_ROOT`` if set, otherwise the nearest ancestor directory of
    this module that contains a ``fixtures/README.md`` (the C12 format contract). The result is
    not required to exist; providers report that through ``health()`` or ``NotConfigured``.
    """
    override = os.environ.get(FIXTURE_ROOT_ENV)
    if override:
        return Path(override).expanduser()
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "fixtures"
        if (candidate / "README.md").is_file():
            return candidate
    return here.parents[-1] / "fixtures"


def resolve_root(root: Path | str | None) -> Path:
    """Normalise a caller-supplied fixture root, falling back to the repository corpus."""
    if root is None:
        return default_fixture_root()
    return Path(root).expanduser()


def require_dir(path: Path, what: str) -> Path:
    """Return ``path`` or raise ``NotConfigured`` when the fixture directory is absent."""
    if not path.is_dir():
        raise NotConfigured(f"{what} not found at {path}")
    return path


def load_json_object(path: Path) -> dict[str, Any]:
    """Read a fixture JSON object, mapping any malformed payload to ``InvalidResponse``."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:  # unreadable fixture file
        raise InvalidResponse(f"cannot read fixture {path}: {exc}") from exc
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidResponse(f"malformed JSON fixture {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise InvalidResponse(f"fixture {path} must contain a JSON object")
    return parsed


def slugify_query(query: str) -> str:
    """Map a search query to its fixture slug: lowercase, non-alphanumerics collapsed to ``-``.

    Deterministic and stable: it is the only mapping between a query and
    ``fixtures/serp/<slug>.json``.
    """
    out: list[str] = []
    for ch in query.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")
