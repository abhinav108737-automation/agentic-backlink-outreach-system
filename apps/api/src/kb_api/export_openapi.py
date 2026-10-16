"""Writes the deterministic OpenAPI snapshot (contract C8). `--check` fails on drift."""

import json
import sys
from pathlib import Path

from kb_contracts.config import Settings

from kb_api.app import create_app

SNAPSHOT = Path(__file__).resolve().parents[4] / "contracts" / "openapi" / "openapi.json"


def render() -> str:
    app = create_app(Settings(_env_file=None), "x" * 32, probes=[])
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        if not SNAPSHOT.exists() or SNAPSHOT.read_text() != text:
            print("OpenAPI drift: run `uv run python -m kb_api.export_openapi`", file=sys.stderr)
            return 1
        print("OpenAPI snapshot up to date")
        return 0
    SNAPSHOT.write_text(text)
    print(f"wrote {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
