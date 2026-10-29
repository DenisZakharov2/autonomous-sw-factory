"""Command-line interface for the Autonomous Software Factory."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import load_artifacts, project_root
from .engine import (
    gate_approve,
    gate_reject,
    import_plan,
    init_run,
    model_for,
    next_action,
    status_summary,
    submit_work,
    task_accept,
    task_claim,
    task_reject,
    task_submit,
)
from .errors import FactoryError
from .journal import read_events, verify_chain
from .metrics import compute_metrics, write_metrics
from .paths import run_dir as safe_run_dir
from .quarantine import (
    AUTO_ALLOW,
    BLOCK_INCOMPLETE,
    BLOCK_RETRY,
    ESCALATE_HUMAN,
    REJECT_AND_PURGE,
    quarantine_skill,
    verify_lock,
)
from .state import load_state
from .trace import build_matrix
from .verify import doctor_report, verify_run


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _runs_root(args: argparse.Namespace) -> Path | None:
    value = getattr(args, "runs_root", None)
    return Path(value) if value else None


def cmd_doctor(args: argparse.Namespace) -> int:
    report = doctor_report()
    errors = list(report["errors"])
    version_file = project_root() / "VERSION"
    if version_file.exists() and version_file.read_text(encoding="utf-8").strip() != __version__:
        errors.append("VERSION does not match factory.__version__")
    license_file = project_root() / "LICENSE"
    if license_file.exists() and "Permission is hereby granted" not in license_file.read_text(encoding="utf-8"):
        errors.append("LICENSE is missing the MIT grant text")
    report["errors"] = errors
    if args.json:
        _print_json(report)
    else:
        print(
            f"roles={report['roles']} templates={report['templates']} schemas={report['schemas']} "
            f"stages={report['stages']} artifacts={report['artifacts']} modes={','.join(report['modes'])}"
        )
        if errors:
            print("DOCTOR FAILED:")
            for error in errors:
                print(f"- {error}")
        else:
            print("DOCTOR OK: configuration, roles, schemas, templates and pipeline are consistent.")
    return 1 if errors else 0


def cmd_init(args: argparse.Namespace) -> int:
    run_dir, state, event = init_run(args.run_id, args.mode, root=_runs_root(args), force=args.force)
    if args.json:
        _print_json({"run_dir": str(run_dir), "state": state, "event": event})
    else:
        print(f"INIT_OK: {run_dir} mode={state['mode']} stage={state['current_stage']} event={event['event_id']}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    summary = status_summary(args.run_id, root=_runs_root(args))
    if args.json:
        _print_json(summary)
    else:
        print(f"run: {summary['run_id']}  mode: {summary['mode']}  stage: {summary['current_stage']} ({summary['stage_title']})")
        print(f"events: {summary['event_count']}  tasks: {summary['tasks']}")
        print(f"human_spec_approved: {summary['human_spec_approved']}  restore_drill_passed: {summary['restore_drill_passed']}")
        print("gates:")
        for name, status in summary["gate_status"].items():
            print(f"  {name}: {status}")
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    order = next_action(args.run_id, root=_runs_root(args))
    _print_json(order)
    return 0 if order["action"] != "blocked" else 1


def cmd_submit(args: argparse.Namespace) -> int:
    event = submit_work(
        args.run_id, stage_id=args.stage, role=args.role, artifacts=args.artifact or [],
        task_id=args.task_id, root=_runs_root(args),
    )
    print(f"SUBMITTED {event['event_id']} stage={args.stage} role={args.role}")
    return 0


def cmd_task(args: argparse.Namespace) -> int:
    if args.task_command not in {"import", "list"} and not args.task_id:
        raise FactoryError("--task-id is required for this task command")
    if args.task_command == "import":
        tasks = import_plan(args.run_id, root=_runs_root(args))
        print(f"imported {len(tasks)} tasks")
        return 0
    if args.task_command == "list":
        state = load_state(safe_run_dir(args.run_id, _runs_root(args)))
        _print_json(state["tasks"])
        return 0
    if args.task_command == "claim":
        event = task_claim(args.run_id, args.task_id, args.role, root=_runs_root(args))
    elif args.task_command == "submit":
        event = task_submit(args.run_id, args.task_id, args.role, args.artifact or [], root=_runs_root(args))
    elif args.task_command == "accept":
        event = task_accept(args.run_id, args.task_id, root=_runs_root(args))
    elif args.task_command == "reject":
        event = task_reject(args.run_id, args.task_id, args.reason, root=_runs_root(args))
    else:
        raise FactoryError(f"unknown task command: {args.task_command}")
    print(f"{event['event_type']} {event['event_id']} task={args.task_id}")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    if args.gate_command == "approve":
        event = gate_approve(
            args.run_id, gate_id=args.gate, approver=args.approver,
            evidence=args.evidence or [], root=_runs_root(args),
        )
    else:
        event = gate_reject(
            args.run_id, gate_id=args.gate, approver=args.approver,
            evidence=args.evidence or [], root=_runs_root(args),
        )
    print(f"{event['verdict']['status']} {event['event_id']} gate={args.gate} by={args.approver}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    run_dir = safe_run_dir(args.run_id, _runs_root(args))
    errors = verify_run(run_dir, args.stage, require_gate=args.require_gate)
    if args.json:
        _print_json({"run_id": args.run_id, "stage": args.stage, "ok": not errors, "errors": errors})
    elif errors:
        print("VERIFY FAILED:")
        for error in errors:
            print(f"- {error}")
    else:
        print(f"VERIFY OK: {run_dir} stage={args.stage or 'current'}")
    return 1 if errors else 0


def cmd_log(args: argparse.Namespace) -> int:
    run_dir = safe_run_dir(args.run_id, _runs_root(args))
    if args.log_command == "show":
        _print_json(read_events(run_dir / "blackbox.jsonl"))
        return 0
    errors = verify_chain(run_dir / "blackbox.jsonl", run_dir / "state.json")
    if errors:
        print("CHAIN VERIFICATION FAILED:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"CHAIN OK: {run_dir / 'blackbox.jsonl'}")
    return 0


def cmd_quarantine(args: argparse.Namespace) -> int:
    if args.quarantine_command == "verify-lock":
        errors = verify_lock(Path(args.lock), Path(args.skills_root) if args.skills_root else None)
        if errors:
            print("LOCK VERIFICATION FAILED:")
            for error in errors:
                print(f"- {error}")
            return 1
        print(f"LOCK OK: {args.lock}")
        return 0
    if not args.path:
        raise FactoryError("a skill path is required for quarantine scan")
    action, result, entry = quarantine_skill(
        Path(args.path), name=args.name, pack=args.pack, commit=args.commit,
        license=args.license, lock_path=Path(args.lock), approved_by=args.approved_by,
        scanner=args.scanner, min_coverage=args.min_coverage, no_llm=args.no_llm,
    )
    print(f"{action}: {args.name} recommendation={result.recommendation} severity={result.severity} "
          f"complete={result.is_complete} coverage={result.coverage_percent}")
    if result.failed_analyzers:
        print("failed analyzers: " + ", ".join(result.failed_analyzers))
    if entry:
        print(f"lock entry written: {args.lock}")
    return {
        AUTO_ALLOW: 0,
        ESCALATE_HUMAN: 2,
        BLOCK_INCOMPLETE: 3,
        BLOCK_RETRY: 4,
        REJECT_AND_PURGE: 5,
    }.get(action, 1)


def cmd_trace(args: argparse.Namespace) -> int:
    matrix = build_matrix(safe_run_dir(args.run_id, _runs_root(args)))
    if args.json:
        _print_json(matrix)
    else:
        print(f"traceability: {matrix['covered']}/{matrix['total']} ({matrix['coverage']:.0%})")
        for row in matrix["requirements"]:
            mark = "OK" if row["covered"] else "GAP"
            print(f"  {mark} {row['req']}: tasks={row['tasks']} tests={row['tests']} acceptance={row['acceptance']!r}")
    return 0 if matrix["covered"] == matrix["total"] else 1


def cmd_metrics(args: argparse.Namespace) -> int:
    run_dir = safe_run_dir(args.run_id, _runs_root(args))
    metrics = write_metrics(run_dir) if args.write else compute_metrics(run_dir)
    _print_json(metrics)
    return 0


def cmd_artifacts(args: argparse.Namespace) -> int:
    registry = load_artifacts()["artifacts"]
    rows = []
    for artifact_id, spec in registry.items():
        rows.append({"id": artifact_id, **{k: spec.get(k) for k in ("path", "owner", "template", "schema")}})
    if args.json:
        _print_json(rows)
    else:
        for row in rows:
            print(f"{row['id']:24} {row['path']:55} owner={row['owner']}")
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    _print_json({tier: model_for(tier) for tier in ("tier-1-economy", "tier-2-balanced", "tier-3-frontier")})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factory", description="Autonomous Software Factory")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="validate config, roles, schemas and templates")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    init = sub.add_parser("init", help="create a new isolated run")
    init.add_argument("--run-id", required=True)
    init.add_argument("--mode", default="nano", choices=["nano", "standard", "enterprise", "brownfield"])
    init.add_argument("--runs-root")
    init.add_argument("--force", action="store_true", help="archive an existing run and start over")
    init.add_argument("--json", action="store_true")
    init.set_defaults(func=cmd_init)

    status = sub.add_parser("status", help="show run state")
    status.add_argument("--run-id", required=True)
    status.add_argument("--runs-root")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    nxt = sub.add_parser("next", help="print the next work order as JSON")
    nxt.add_argument("--run-id", required=True)
    nxt.add_argument("--runs-root")
    nxt.set_defaults(func=cmd_next)

    submit = sub.add_parser("submit", help="submit artifacts for a stage")
    submit.add_argument("--run-id", required=True)
    submit.add_argument("--stage", required=True)
    submit.add_argument("--role", required=True)
    submit.add_argument("--artifact", action="append")
    submit.add_argument("--task-id")
    submit.add_argument("--runs-root")
    submit.set_defaults(func=cmd_submit)

    task = sub.add_parser("task", help="task commands")
    task.add_argument("task_command", choices=["import", "list", "claim", "submit", "accept", "reject"])
    task.add_argument("--run-id", required=True)
    task.add_argument("--task-id")
    task.add_argument("--role")
    task.add_argument("--artifact", action="append")
    task.add_argument("--reason", default="verification failed")
    task.add_argument("--runs-root")
    task.set_defaults(func=cmd_task)

    gate = sub.add_parser("gate", help="approve or reject a gate")
    gate.add_argument("gate_command", choices=["approve", "reject"])
    gate.add_argument("--run-id", required=True)
    gate.add_argument("--gate", required=True)
    gate.add_argument("--approver", required=True, help="human:<id> or the role that owns the gate")
    gate.add_argument("--evidence", action="append")
    gate.add_argument("--runs-root")
    gate.set_defaults(func=cmd_gate)

    verify = sub.add_parser("verify", help="deterministic run verification")
    verify.add_argument("--run-id", required=True)
    verify.add_argument("--stage")
    verify.add_argument("--require-gate", action="store_true")
    verify.add_argument("--runs-root")
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=cmd_verify)

    log = sub.add_parser("log", help="journal commands")
    log.add_argument("log_command", choices=["verify", "show"])
    log.add_argument("--run-id", required=True)
    log.add_argument("--runs-root")
    log.set_defaults(func=cmd_log)

    quarantine = sub.add_parser("quarantine", help="SkillSpector gate")
    quarantine.add_argument("quarantine_command", choices=["scan", "verify-lock"])
    quarantine.add_argument("path", nargs="?")
    quarantine.add_argument("--name", default="")
    quarantine.add_argument("--pack", default="local")
    quarantine.add_argument("--commit", default="local")
    quarantine.add_argument("--license", default="unknown")
    quarantine.add_argument("--lock", default="skills-lock.json")
    quarantine.add_argument("--skills-root")
    quarantine.add_argument("--approved-by", default="auto:skillspector")
    quarantine.add_argument("--scanner", default="skillspector")
    quarantine.add_argument("--min-coverage", type=float, default=95.0)
    quarantine.add_argument("--no-llm", action="store_true")
    quarantine.set_defaults(func=cmd_quarantine)

    trace = sub.add_parser("trace", help="REQ traceability matrix")
    trace.add_argument("--run-id", required=True)
    trace.add_argument("--runs-root")
    trace.add_argument("--json", action="store_true")
    trace.set_defaults(func=cmd_trace)

    metrics = sub.add_parser("metrics", help="compute the eight health metrics")
    metrics.add_argument("--run-id", required=True)
    metrics.add_argument("--runs-root")
    metrics.add_argument("--write", action="store_true")
    metrics.set_defaults(func=cmd_metrics)

    artifacts = sub.add_parser("artifacts", help="list the artifact registry")
    artifacts.add_argument("--json", action="store_true")
    artifacts.set_defaults(func=cmd_artifacts)

    models = sub.add_parser("models", help="show resolved model tiers")
    models.set_defaults(func=cmd_models)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FactoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
