"""Seed policy v1: the global policy and its ACTIVE default version. Idempotent.

`policy_versions` is guard-triggered (only status/approved_by/approved_at are updatable), so a
version is never edited after insertion. Seeding therefore never "refreshes" a version: if an
ACTIVE global version already exists it is returned untouched; otherwise one new version is
inserted directly as ACTIVE. A changed default is a new, approved version via the governed
optimization path, not a reseed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final

import sqlalchemy as sa
from kb_contracts.domain import PolicyBody, PolicyVersion
from sqlalchemy.dialects import postgresql as pg

from kb_core.db import tables as t
from kb_core.db._common import dto, fetch_one, stamp, ts_now
from kb_core.db.uow import SqlUnitOfWork

GLOBAL_POLICY_KEY: Final = "global.default"
SEED_ACTOR: Final = "system:seed"


def canonical_json(body: PolicyBody) -> str:
    """Deterministic serialisation: sorted keys, no whitespace, ASCII-escaped."""
    data: dict[str, Any] = body.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def policy_content_hash(body: PolicyBody) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


async def seed_policy_v1(uow: SqlUnitOfWork, *, correlation_id: str = "seed") -> PolicyVersion:
    """Ensure the global policy has an ACTIVE version; returns it. Safe to call repeatedly."""
    s = uow.session
    p, pv = t.policies, t.policy_versions

    await s.execute(
        pg.insert(p)
        .values(
            stamp(
                key=GLOBAL_POLICY_KEY,
                name="Global qualification and outreach policy",
                scope="global",
                risk_class="L3",
            )
        )
        .on_conflict_do_nothing(index_elements=[p.c.key])
    )
    # Row lock serialises concurrent seeders so they cannot both allocate version_no 1.
    policy_row = await fetch_one(
        s, sa.select(p.c.id).where(p.c.key == GLOBAL_POLICY_KEY).with_for_update()
    )
    assert policy_row is not None
    policy_id = policy_row["id"]

    active = await fetch_one(
        s,
        sa.select(pv)
        .where(pv.c.policy_id == policy_id, pv.c.status == "ACTIVE")
        .order_by(pv.c.version_no.desc())
        .limit(1),
    )
    if active is not None:
        return dto(PolicyVersion, active, body=PolicyBody.model_validate(active["body"]))

    body = PolicyBody()
    next_no = (
        sa.select(sa.func.coalesce(sa.func.max(pv.c.version_no), 0) + 1)
        .where(pv.c.policy_id == policy_id)
        .scalar_subquery()
    )
    row = (
        (
            await s.execute(
                sa.insert(pv)
                .values(
                    stamp(
                        policy_id=policy_id,
                        version_no=next_no,
                        body=body.model_dump(mode="json"),
                        content_hash=policy_content_hash(body),
                        status="ACTIVE",
                        created_by=SEED_ACTOR,
                        approved_by=SEED_ACTOR,
                        approved_at=ts_now(),
                    )
                )
                .returning(pv)
            )
        )
        .mappings()
        .one()
    )
    version = dto(PolicyVersion, row, body=body)
    await uow.audit.append(
        actor_type="system",
        actor_id=SEED_ACTOR,
        action="policy.version.seeded",
        subject_type="policy_version",
        subject_id=version.id,
        correlation_id=correlation_id,
        new_state={"version_no": version.version_no, "content_hash": version.content_hash},
        reason="initial default policy (DA > 45, spam < 1, missing metric defers)",
    )
    return version
