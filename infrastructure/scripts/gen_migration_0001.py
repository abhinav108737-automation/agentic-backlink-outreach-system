"""Regenerates migrations/versions/0001_baseline.py with enum literals inlined.

Run: uv run python infrastructure/scripts/gen_migration_0001.py
Only meaningful before 0001 is released; afterwards schema changes are new migrations.
"""

import pathlib

from kb_contracts import enums as e

NAMES = {
    "campaign": e.CampaignStatus,
    "autonomy": e.AutonomyMode,
    "risk": e.RiskClass,
    "prospect": e.ProspectStatus,
    "sequence": e.SequenceStatus,
    "message": e.MessageStatus,
    "opportunity": e.OpportunityStatus,
    "backlink": e.BacklinkStatus,
    "optimization": e.OptimizationStatus,
    "workflow": e.WorkflowStatus,
    "job": e.JobStatus,
    "qual": e.QualificationOutcome,
    "reply": e.ReplyClass,
    "supp_scope": e.SuppressionScope,
    "supp_reason": e.SuppressionReason,
    "approval_kind": e.ApprovalKind,
    "approval_status": e.ApprovalStatus,
    "action": e.OperatorAction,
    "delivery": e.DeliveryMode,
    "actor": e.ActorType,
    "memory": e.MemoryType,
}


def main() -> None:
    block = (
        "ENUMS = {\n"
        + "".join(
            f'    "{k}": "' + ", ".join(f"'{m.value}'" for m in v) + '",\n'
            for k, v in NAMES.items()
        )
        + "}\n"
    )
    root = pathlib.Path(__file__).resolve().parents[2]
    tmpl = (root / "infrastructure/scripts/migration_0001.tmpl").read_text()
    (root / "migrations/versions/0001_baseline.py").write_text(tmpl.replace("@@ENUMS@@", block))
    import subprocess
    import sys

    subprocess.run(  # keep generated output gate-clean
        [sys.executable, "-m", "ruff", "format", "-q", "migrations/versions/0001_baseline.py"],
        cwd=root,
        check=True,
    )
    print("wrote migrations/versions/0001_baseline.py")


if __name__ == "__main__":
    main()
