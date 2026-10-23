"""Import-boundary guards, independent of import-linter.

import-linter enforces these at gate time, but its contracts live in `pyproject.toml` and a
rule can be deleted as easily as it was added. These tests fail if a required contract goes
missing, and they re-check the two load-bearing boundaries directly from the source tree by
AST, so a dynamic import or a service locator cannot be used to dodge them either.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = {
    "kb_contracts": ROOT / "contracts/python/src/kb_contracts",
    "kb_core": ROOT / "services/core/src/kb_core",
    "kb_providers": ROOT / "services/providers/src/kb_providers",
    "kb_agents": ROOT / "agents/src/kb_agents",
    "kb_api": ROOT / "apps/api/src/kb_api",
    "kb_runtime": ROOT / "apps/runtime/src/kb_runtime",
}

# (source package, forbidden prefix) pairs that define the architecture, not a style choice.
FORBIDDEN: tuple[tuple[str, str], ...] = (
    ("kb_core", "kb_agents"),  # core must not depend on the layer above it
    ("kb_core", "kb_api"),
    ("kb_core", "kb_runtime"),
    ("kb_agents", "kb_core.db"),  # agents reach persistence only via injected capabilities
    ("kb_agents", "kb_providers.crawler"),
    ("kb_agents", "kb_providers.email"),
    ("kb_agents", "kb_providers.browser"),
    ("kb_agents", "kb_runtime"),
    ("kb_api", "kb_agents"),
    ("kb_contracts", "kb_core"),
    ("kb_contracts", "kb_agents"),
)

REQUIRED_CONTRACTS = {
    "kb_contracts imports no other kb package",
    "agents act only through ToolGateway (no db/email/crawler/browser)",
    "core does not import the agent layer above it",
    "agents do not import the composition root",
    "api does not import agent internals",
}


def _imported_modules(package_dir: Path) -> set[tuple[str, Path, int]]:
    """Every module named by an import anywhere in the package, including imports nested
    inside functions — a deferred import is still a dependency."""
    found: set[tuple[str, Path, int]] = set()
    for path in sorted(package_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add((alias.name, path, node.lineno))
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add((node.module, path, node.lineno))
    return found


@pytest.mark.req("SEC-04")
@pytest.mark.parametrize(("source", "forbidden"), FORBIDDEN, ids=lambda v: str(v))
def test_package_does_not_import(source: str, forbidden: str) -> None:
    offenders = [
        f"{path.relative_to(ROOT)}:{line} imports {module}"
        for module, path, line in _imported_modules(SRC[source])
        if module == forbidden or module.startswith(f"{forbidden}.")
    ]
    assert not offenders, f"{source} must not import {forbidden}:\n" + "\n".join(offenders)


@pytest.mark.req("SEC-04")
def test_required_import_linter_contracts_are_configured() -> None:
    """A deleted contract is a silently relaxed boundary, so the set is pinned here."""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    linter = cfg["tool"]["importlinter"]
    names = {c["name"] for c in linter["contracts"]}
    assert names >= REQUIRED_CONTRACTS, f"missing contracts: {REQUIRED_CONTRACTS - names}"
    assert "kb_runtime" in linter["root_packages"], "kb_runtime must be analysed by import-linter"


@pytest.mark.req("SEC-04")
def test_nothing_depends_on_the_composition_root() -> None:
    """kb_runtime is a leaf. If something imported it, it would become a back door between
    the agent layer and the database for every package that imported it."""
    offenders = []
    for name, directory in SRC.items():
        if name == "kb_runtime":
            continue
        offenders += [
            f"{path.relative_to(ROOT)}:{line}"
            for module, path, line in _imported_modules(directory)
            if module == "kb_runtime" or module.startswith("kb_runtime.")
        ]
    assert not offenders, "only tests may import kb_runtime:\n" + "\n".join(offenders)


@pytest.mark.req("SEC-04")
def test_composition_root_declares_what_it_wires() -> None:
    deps = tomllib.loads((ROOT / "apps/runtime/pyproject.toml").read_text())["project"][
        "dependencies"
    ]
    assert {"kb-contracts", "kb-core", "kb-providers", "kb-agents"} <= set(deps)


@pytest.mark.req("SEC-04")
def test_core_does_not_declare_a_dependency_on_agents() -> None:
    """The manifest must tell the truth too, not just the import graph."""
    deps = tomllib.loads((ROOT / "services/core/pyproject.toml").read_text())["project"][
        "dependencies"
    ]
    assert "kb-agents" not in set(deps)
