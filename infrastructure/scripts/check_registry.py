"""Requirements registry gate (ADR-0013): unique IDs, valid test references, coverage report.

Fails on: duplicate IDs, tests referencing unknown IDs, old-ID mapping gaps.
Warns (until MVP) on: registry rows that no test references yet.
"""

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
text = (ROOT / "docs/01_REQUIREMENTS.md").read_text()
main, _, mapping = text.partition("## Traceability")
ids = re.findall(r"^\| ((?:FR|NFR|SEC)-\d+) \|", main, re.M)
errors: list[str] = []

dups = [k for k, v in Counter(ids).items() if v > 1]
if dups:
    errors.append(f"duplicate requirement IDs: {dups}")
row_lines = re.findall(r"^\| ((?:FR|NFR|SEC)-\S*) \|", main, re.M)
if len(row_lines) != len(ids):
    errors.append(f"malformed registry rows: {sorted(set(row_lines) - set(ids))}")
mapped = set(re.findall(r"^\| (FR-\d+) \|", mapping, re.M))
targets = set(
    re.findall(
        r"((?:FR|NFR|SEC)-\d+)",
        "\n".join(
            line.split("|")[3] for line in mapping.splitlines() if re.match(r"^\| FR-\d+ \|", line)
        ),
    )
)
if targets - set(ids):
    errors.append(f"v0 mapping points at unknown canonical IDs: {sorted(targets - set(ids))}")
if len(mapped) != 20:
    errors.append(f"v0 mapping must cover FR-01..FR-20 (found {len(mapped)})")

referenced: set[str] = set()
ID = r"((?:FR|NFR|SEC)-\d+)"
patterns = [re.compile(rf'req\("{ID}"\)'), re.compile(rf"//\s*req:\s*{ID}")]
skip = {"node_modules", ".venv", "out", ".git"}
for path in [*ROOT.glob("tests/**/*.py"), *ROOT.glob("apps/desktop/tests/**/*.ts")]:
    if skip & set(path.parts):
        continue
    for pat in patterns:
        referenced.update(pat.findall(path.read_text()))
unknown = sorted(referenced - set(ids))
if unknown:
    errors.append(f"tests reference unknown requirement IDs: {unknown}")

uncovered = sorted(set(ids) - referenced)
print(f"registry: {len(ids)} requirements, {len(referenced & set(ids))} referenced by tests")
print(f"WARN (allowed until MVP): {len(uncovered)} requirements have no test yet")
for e in errors:
    print(f"ERROR: {e}", file=sys.stderr)
sys.exit(1 if errors else 0)
