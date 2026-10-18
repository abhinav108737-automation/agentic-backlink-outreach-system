"""Deterministic JSON-Schema instance generator for the offline fake model provider.

Produces a value that validates against the (Pydantic-emitted) JSON Schema it is given. It is
a test double: the content is synthetic and meaningless, only its *shape* is guaranteed.
Supported: object/array/string/integer/number/boolean/null, enum, const, anyOf/oneOf, allOf,
``$ref``/``$defs``, numeric bounds, ``minLength``/``maxLength``, ``minItems``/``maxItems``,
uuid/date-time/date formats and simple ``^(a|b|c)$`` alternation patterns.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

_MAX_DEPTH = 12


class SchemaNotSupported(ValueError):
    """The schema uses a construct the generator cannot satisfy."""


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        node: Any = root
        for part in ref[2:].split("/"):
            node = node[part]
        if not isinstance(node, dict):
            raise SchemaNotSupported(f"bad $ref {ref}")
        merged = {k: v for k, v in schema.items() if k != "$ref"}
        return {**node, **merged}
    return schema


def _string(schema: dict[str, Any], rng: random.Random) -> str:
    fmt = schema.get("format")
    if fmt == "uuid":
        return str(uuid.UUID(int=rng.getrandbits(128), version=4))
    if fmt == "date-time":
        return f"2026-01-{rng.randint(1, 28):02d}T00:00:00Z"
    if fmt == "date":
        return f"2026-01-{rng.randint(1, 28):02d}"
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        body = pattern.removeprefix("^").removesuffix("$")
        if body.startswith("(") and body.endswith(")"):
            options = body[1:-1].split("|")
            if all(o.isalnum() or "_" in o for o in options):
                return rng.choice(options)
        raise SchemaNotSupported(f"unsupported pattern {pattern!r}")
    min_len = int(schema.get("minLength", 1))
    max_len = int(schema.get("maxLength", max(min_len, 24)))
    length = min(max(min_len, 8), max_len)
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    return "".join(rng.choice(alphabet) for _ in range(length))


def _number(schema: dict[str, Any], rng: random.Random, *, integer: bool) -> float:
    lo = schema.get("minimum", schema.get("exclusiveMinimum"))
    hi = schema.get("maximum", schema.get("exclusiveMaximum"))
    low = float(lo) if lo is not None else 0.0
    high = float(hi) if hi is not None else low + 100.0
    if "exclusiveMinimum" in schema and lo is not None:
        low += 1e-3 if not integer else 1
    if "exclusiveMaximum" in schema and hi is not None:
        high -= 1e-3 if not integer else 1
    if integer:
        return float(rng.randint(int(low) if low == int(low) else int(low) + 1, int(high)))
    return round(rng.uniform(low, high), 4)


def generate(
    schema: dict[str, Any],
    rng: random.Random,
    *,
    root: dict[str, Any] | None = None,
    depth: int = 0,
) -> Any:
    """Return a deterministic instance of ``schema`` drawn from ``rng``."""
    root = root if root is not None else schema
    if depth > _MAX_DEPTH:
        raise SchemaNotSupported("schema too deep / recursive")
    schema = _resolve(schema, root)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return rng.choice(list(schema["enum"]))
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if o.get("type") != "null"] or schema[key]
            return generate(rng.choice(options), rng, root=root, depth=depth + 1)
    if "allOf" in schema:
        merged: dict[str, Any] = {}
        for part in schema["allOf"]:
            merged.update(_resolve(part, root))
        return generate(merged, rng, root=root, depth=depth + 1)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object" or "properties" in schema:
        props: dict[str, Any] = schema.get("properties", {})
        required = set(schema.get("required", []))
        out: dict[str, Any] = {}
        for name, sub in props.items():
            if name in required:
                out[name] = generate(sub, rng, root=root, depth=depth + 1)
        return out
    if kind == "array":
        items = schema.get("items", {})
        lo = int(schema.get("minItems", 0))
        hi = int(schema.get("maxItems", max(lo, 2)))
        count = min(max(lo, 1 if items else 0), hi) if lo == 0 else lo
        return [generate(items, rng, root=root, depth=depth + 1) for _ in range(count)]
    if kind == "string":
        return _string(schema, rng)
    if kind == "integer":
        return int(_number(schema, rng, integer=True))
    if kind == "number":
        return _number(schema, rng, integer=False)
    if kind == "boolean":
        return rng.random() < 0.5
    if kind == "null":
        return None
    raise SchemaNotSupported(f"cannot generate for schema {schema!r}")


def violate(instance: Any, schema: dict[str, Any], root: dict[str, Any]) -> Any:
    """Return a copy of ``instance`` that is JSON but breaks ``schema`` (never coerced valid)."""
    schema = _resolve(schema, root)
    if isinstance(instance, dict):
        broken = dict(instance)
        required = [k for k in schema.get("required", []) if k in broken]
        if required:
            del broken[required[0]]
        else:
            broken["__schema_violation__"] = True
        return broken
    return {"__schema_violation__": instance}
