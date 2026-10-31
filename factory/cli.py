"""Command-line interface for the Autonomous Software Factory."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import (
    PACKAGE_ROOT,
    load_artifacts,
    load_budget,
    load_pipeline,
    resolve_model,
)
from .engine import (
    accept_risk,
    gate_approve,
    gate_reject,
    import_plan,
    init_run,
    model_for,
    next_action,
    stage_reset,
    status_summary,
    submit_work,
    task_accept,
    task_claim,
    task_reject,
    task_reset,
    task_submit,
    veto,
)
from .checkpoint import checkpoint_state, sign_run, verify_checkpoint
from .errors import FactoryError
from .journal import append_event, read_events, verify_chain
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
    repo_root = PACKAGE_ROOT.parent
    if (repo_root / "pyproject.toml").exists():
        version_file = repo_root / "VERSION"
        if version_file.exists() and version_file.read_text(encoding="utf-8").strip() != __version__:
            errors.append("VERSION does not match factory.__version__")
        license_file = repo_root / "LICENSE"
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
    run_dir, state, event = init_run(
        args.run_id, args.mode, root=_runs_root(args), force=args.force, idea=args.idea
    )
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


def _build_executor(args: argparse.Namespace):
    if args.executor == "fake":
        from .runtime.fake import FakeExecutor

        return FakeExecutor(scenario=args.fake_scenario)
    # A real CLI is untrusted code: the sandbox is the trust boundary and is on
    # unless the operator explicitly turns it off.
    sandbox = True if args.sandbox is None else bool(args.sandbox)
    if not sandbox:
        print(
            "WARNING: --no-sandbox disables the trust boundary; the executor can "
            "write state.json and close its own gates (tripwire detects it, but "
            "after the fact).",
            file=sys.stderr,
        )
    if args.executor == "opencode":
        from .runtime.opencode import OpenCodeExecutor

        return OpenCodeExecutor(
            model=args.executor_model,
            mode=args.opencode_mode,
            timeout=args.timeout,
            sandbox=sandbox,
            read_write=tuple(args.sandbox_rw or ()),
        )
    from .runtime.hermes import HermesExecutor

    toolset = None if (args.executor_tools or "").strip().lower() in {"", "all"} else args.executor_tools
    return HermesExecutor(
        model=args.executor_model,
        reasoning=args.reasoning,
        timeout=args.timeout,
        sandbox=sandbox,
        read_write=tuple(args.sandbox_rw or ()),
        yolo=args.yolo,
        toolset=toolset,
        skills=args.executor_skills,
    )


def cmd_run(args: argparse.Namespace) -> int:
    from .runtime.loop import run_loop

    root = _runs_root(args)
    run_dir = safe_run_dir(args.run_id, root)
    if not run_dir.is_dir():
        if args.no_init:
            raise FactoryError(f"run {args.run_id!r} does not exist; run `factory init` first")
        init_run(args.run_id, args.mode, root=root, idea=args.idea)
    executor = _build_executor(args)
    if not args.json:
        print(f"RUN {args.run_id} executor={args.executor} max_steps={args.max_steps}")

    def progress(step: int, order: dict) -> None:
        if args.json:
            return
        print(
            f"[{step:02d}] {order['action']:<8} {str(order.get('stage') or '-'):<15} "
            f"{str(order.get('next_role') or '-'):<24} pending={len(order.get('pending_artifacts') or [])}"
        )

    budget = None
    if any(value is not None for value in (args.budget_tokens, args.budget_minutes, args.budget_usd)):
        state = load_state(run_dir)
        mode_budget = dict(load_pipeline()["modes"][state["mode"]].get("budget") or {})
        for key, value in (
            ("tokens", args.budget_tokens),
            ("minutes", args.budget_minutes),
            ("usd", args.budget_usd),
        ):
            if value is not None:
                mode_budget[key] = value
        budget = mode_budget
    result = run_loop(
        args.run_id,
        executor,
        max_steps=args.max_steps,
        root=root,
        budget=budget,
        parallel_tasks=args.parallel_tasks,
        progress=progress,
    )
    if args.json:
        _print_json(
            {
                "run_id": result.run_id,
                "status": result.status,
                "code": result.code,
                "steps": result.steps,
                "stage": result.stage,
                "message": result.message,
                "usage": result.usage,
                "evidence": result.evidence_path,
            }
        )
        return result.code
    print()
    print(f"RESULT: {result.status} after {result.steps} step(s) (exit {result.code})")
    if result.code == 2:
        print(f"NEXT: {result.message}")
        print("A human gate is never closed by the runtime, with or without --yes.")
    elif result.message:
        print(f"DETAIL: {result.message}")
    if result.evidence_path:
        print(f"EVIDENCE: {result.evidence_path}")
    print(f"USAGE: {result.usage}")
    return result.code


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
    elif args.task_command == "reset":
        event = task_reset(
            args.run_id, task_id=args.task_id, approver=args.approver or "",
            reason=args.reason, root=_runs_root(args),
        )
    else:
        raise FactoryError(f"unknown task command: {args.task_command}")
    print(f"{event['event_type']} {event['event_id']} task={args.task_id}")
    return 0


def cmd_stage(args: argparse.Namespace) -> int:
    event = stage_reset(
        args.run_id, stage_id=args.stage, approver=args.approver,
        reason=args.reason, root=_runs_root(args),
    )
    print(f"{event['event_type']} {event['event_id']} stage={args.stage}")
    return 0


def cmd_veto(args: argparse.Namespace) -> int:
    event = veto(
        args.run_id, stage_id=args.stage, role=args.role, code=args.code,
        evidence=args.evidence or [], root=_runs_root(args),
    )
    print(f"{event['verdict']['status']} {event['event_id']} veto={args.code} stage={args.stage}")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    token = getattr(args, "approval_token", None)
    if args.gate_command == "approve":
        event = gate_approve(
            args.run_id, gate_id=args.gate, approver=args.approver,
            evidence=args.evidence or [], root=_runs_root(args), approval_token=token,
        )
    elif args.gate_command == "accept-risk":
        event = accept_risk(
            args.run_id, gate_id=args.gate, approver=args.approver,
            risk=args.risk or "", mitigation=args.mitigation or "",
            expires=args.expires, root=_runs_root(args), approval_token=token,
        )
    else:
        event = gate_reject(
            args.run_id, gate_id=args.gate, approver=args.approver,
            evidence=args.evidence or [], root=_runs_root(args), approval_token=token,
        )
    print(f"{event['verdict']['status']} {event['event_id']} gate={args.gate} by={args.approver}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    run_dir = safe_run_dir(args.run_id, _runs_root(args))
    errors = verify_run(run_dir, args.stage, require_gate=args.require_gate)
    errors.extend(
        verify_checkpoint(
            run_dir,
            key_file=Path(args.key_file) if args.key_file else None,
            store=Path(args.store) if args.store else None,
        )
    )
    status = checkpoint_state(
        run_dir, Path(args.store) if args.store else None
    )
    if args.json:
        _print_json(
            {
                "run_id": args.run_id,
                "stage": args.stage,
                "ok": not errors,
                "errors": errors,
                "checkpoint": status,
            }
        )
    elif errors:
        print("VERIFY FAILED:")
        for error in errors:
            print(f"- {error}")
    else:
        print(f"VERIFY OK: {run_dir} stage={args.stage or 'current'}")
        if status["status"] == "unsigned":
            print("checkpoint: unsigned (factory sign --run-id ... anchors the journal head)")
        elif status["status"] == "stale":
            print(
                f"checkpoint: stale (signed {status['signed_event_count']} events, "
                f"journal has {status['event_count']}) - re-sign to move the anchor"
            )
        else:
            print(f"checkpoint: {status['status']} ({status['event_count']} events)")
    return 1 if errors else 0


def cmd_sign(args: argparse.Namespace) -> int:
    run_dir = safe_run_dir(args.run_id, _runs_root(args))
    result = sign_run(
        run_dir,
        key_file=Path(args.key_file) if args.key_file else None,
        store=Path(args.store) if args.store else None,
    )
    if args.json:
        _print_json(result)
    else:
        print(
            f"SIGNED {args.run_id}: events={result['event_count']} "
            f"key={result['key_id']} store={result['store']}"
        )
    return 0


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
    display = args.name or result.skill_name or Path(args.path).name
    print(f"{action}: {display} recommendation={result.recommendation} severity={result.severity} "
          f"complete={result.is_complete} coverage={result.coverage_percent}")
    if result.failed_analyzers:
        print("failed analyzers: " + ", ".join(result.failed_analyzers))
    if entry:
        print(f"lock entry written: {args.lock}")
        if args.run_id:
            run_dir = safe_run_dir(args.run_id, _runs_root(args))
            model = resolve_model("tier-2-balanced")
            append_event(
                run_dir,
                stage="s15_quarantine",
                task_id=None,
                role="00-orchestrator",
                event_type="SKILL_INSTALL",
                why="Quarantine scan auto-allowed the skill",
                for_what=f"Install {args.name}",
                action=f"Installed {display} sha256={entry['sha256'][:12]}",
                verdict_status="DRAFT",
                verified_by="00-orchestrator",
                evidence=[f"lock: {args.lock}", f"scan: {result.recommendation}/{result.severity}"],
                model_tier=model["tier"],
                model_id=model["model_id"],
                reasoning_effort=model["effort"],
            )
            print(f"SKILL_INSTALL logged: {args.run_id}")
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


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval import format_eval_report, run_eval_set

    budget = {
        key: value
        for key, value in (
            ("tokens", args.budget_tokens),
            ("minutes", args.budget_minutes),
            ("usd", args.budget_usd),
        )
        if value is not None
    }
    report = run_eval_set(
        Path(args.cases),
        _build_executor(args),
        root=_runs_root(args),
        baseline=Path(args.baseline) if args.baseline else None,
        update_baseline=Path(args.update_baseline) if args.update_baseline else None,
        stamp=args.stamp,
        budget=budget or None,
        only=set(args.cases_only) if args.cases_only else None,
    )
    if args.json:
        _print_json(report)
    else:
        print(format_eval_report(report))
    return 0 if report["ok"] else 1


def cmd_compare(args: argparse.Namespace) -> int:
    from .compare import compare_runs, format_compare

    report = compare_runs(args.left, args.right, root=_runs_root(args))
    if args.json:
        _print_json(report)
    else:
        print(format_compare(report))
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


def cmd_budget(args: argparse.Namespace) -> int:
    budget = load_budget()
    pipeline = load_pipeline()
    payload: dict[str, object] = {
        "default_max_attempts": budget["default_max_attempts"],
        "max_veto_retries": budget["max_veto_retries"],
        "on_attempts_exhausted": budget.get("on_attempts_exhausted", "escalate"),
        "on_budget_exhausted": budget.get("on_budget_exhausted"),
        "on_model_unavailable": budget.get("on_model_unavailable"),
        "enforced_by_engine": ["attempts", "veto_retries"],
        "runtime_responsibility": sorted(
            name for name, enabled in (budget.get("enforce") or {}).items() if enabled
        ),
    }
    if args.run_id:
        state = load_state(safe_run_dir(args.run_id, _runs_root(args)))
        payload["mode"] = state["mode"]
        payload["mode_budget"] = pipeline["modes"][state["mode"]].get("budget")
        payload["attempts_used"] = {
            "gates": {
                gate_id: record["attempts"]
                for gate_id, record in state["gate_status"].items()
                if record.get("attempts")
            },
            "tasks": {
                task_id: task["attempts"]
                for task_id, task in state["tasks"].items()
                if task.get("attempts")
            },
        }
    else:
        payload["mode_budgets"] = {
            mode: cfg.get("budget") for mode, cfg in pipeline["modes"].items()
        }
    _print_json(payload)
    return 0


def add_executor_arguments(parser: argparse.ArgumentParser) -> None:
    """Executor options shared by `factory run` and `factory eval`."""
    parser.add_argument("--executor", default="fake", choices=["fake", "opencode", "hermes"])
    parser.add_argument("--executor-model", help="model override handed to the executor CLI")
    parser.add_argument(
        "--executor-tools",
        default="auto",
        help="Hermes toolsets for a step (`auto`: file, plus terminal on s4_build; `all` keeps the full set)",
    )
    parser.add_argument(
        "--executor-skills",
        default=None,
        help="value for Hermes --skills (default: leave the host setting untouched)",
    )
    parser.add_argument("--timeout", type=float, default=1800.0, help="per-step timeout in seconds")
    sandbox = parser.add_mutually_exclusive_group()
    sandbox.add_argument(
        "--sandbox",
        dest="sandbox",
        action="store_true",
        default=None,
        help="wrap the executor in bubblewrap (default for hermes/opencode)",
    )
    sandbox.add_argument(
        "--no-sandbox",
        dest="sandbox",
        action="store_false",
        help="run the executor without the trust boundary (debugging only)",
    )
    parser.add_argument("--sandbox-rw", action="append", help="extra path the sandboxed executor may write (repeatable)")
    parser.add_argument("--yolo", action="store_true", help="pass --yolo to hermes")
    parser.add_argument("--reasoning", default="medium", help="reasoning effort passed to hermes")
    parser.add_argument("--opencode-mode", default="run", choices=["run", "prompt"])
    parser.add_argument(
        "--fake-scenario",
        default="ok",
        choices=["ok", "break-artifact", "write-state", "escape", "slow"],
    )


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
    init.add_argument("--idea", help="one-paragraph product idea handed to the intake stage")
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

    run = sub.add_parser("run", help="execute work orders with a runtime executor")
    run.add_argument("--run-id", required=True)
    run.add_argument("--mode", default="nano", choices=["nano", "standard", "enterprise", "brownfield"])
    add_executor_arguments(run)
    run.add_argument("--max-steps", type=int, default=60)
    run.add_argument(
        "--parallel-tasks",
        type=int,
        default=0,
        help="run up to N independent s4_build tasks in separate git worktrees (0 = sequential)",
    )
    run.add_argument("--runs-root")
    run.add_argument("--no-init", action="store_true", help="refuse to create a missing run")
    run.add_argument("--idea", help="product idea written to runs/<id>/idea.md when the run is created")
    run.add_argument("--json", action="store_true")
    run.add_argument("--budget-tokens", type=int, help="override the mode token budget for this run")
    run.add_argument("--budget-minutes", type=float, help="override the mode minute budget")
    run.add_argument("--budget-usd", type=float, help="override the mode usd budget")
    run.set_defaults(func=cmd_run)

    evaluate = sub.add_parser("eval", help="run the external eval cases against a baseline")
    evaluate.add_argument("--cases", default="eval/cases", help="directory with one subdirectory per case")
    evaluate.add_argument(
        "--case",
        action="append",
        dest="cases_only",
        metavar="ID",
        help="run only this case id (repeatable); the full set stays the default",
    )
    evaluate.add_argument("--baseline", help="baseline JSON (default: <cases>/../baseline.json)")
    evaluate.add_argument(
        "--update-baseline",
        help="rewrite this baseline from the current results (refuses a failing case)",
    )
    evaluate.add_argument("--stamp", help="run-id suffix (default: UTC timestamp)")
    evaluate.add_argument("--budget-tokens", type=int, help="override every case token budget")
    evaluate.add_argument("--budget-minutes", type=float, help="override every case minute budget")
    evaluate.add_argument("--budget-usd", type=float, help="override every case usd budget")
    evaluate.add_argument("--runs-root")
    evaluate.add_argument("--json", action="store_true")
    add_executor_arguments(evaluate)
    evaluate.set_defaults(func=cmd_eval)

    compare = sub.add_parser("compare", help="delta between two runs")
    compare.add_argument("--left", required=True, help="left run id")
    compare.add_argument("--right", required=True, help="right run id")
    compare.add_argument("--runs-root")
    compare.add_argument("--json", action="store_true")
    compare.set_defaults(func=cmd_compare)

    submit = sub.add_parser("submit", help="submit artifacts for a stage")
    submit.add_argument("--run-id", required=True)
    submit.add_argument("--stage", required=True)
    submit.add_argument("--role", required=True)
    submit.add_argument("--artifact", action="append")
    submit.add_argument("--task-id")
    submit.add_argument("--runs-root")
    submit.set_defaults(func=cmd_submit)

    task = sub.add_parser("task", help="task commands")
    task.add_argument("task_command", choices=["import", "list", "claim", "submit", "accept", "reject", "reset"])
    task.add_argument("--run-id", required=True)
    task.add_argument("--task-id")
    task.add_argument("--role")
    task.add_argument("--artifact", action="append")
    task.add_argument("--reason", default="verification failed")
    task.add_argument("--approver", help="human:<id> for reset")
    task.add_argument("--runs-root")
    task.set_defaults(func=cmd_task)

    gate = sub.add_parser("gate", help="approve, reject or accept risk on a gate")
    gate.add_argument("gate_command", choices=["approve", "reject", "accept-risk"])
    gate.add_argument("--run-id", required=True)
    gate.add_argument("--gate", required=True)
    gate.add_argument("--approver", required=True, help="human:<id> or the role that owns the gate")
    gate.add_argument("--evidence", action="append")
    gate.add_argument("--risk", help="risk description for accept-risk")
    gate.add_argument("--mitigation", help="mitigation for accept-risk")
    gate.add_argument("--expires", help="risk acceptance expiry date")
    gate.add_argument("--approval-token", help="HMAC token required when FACTORY_APPROVAL_KEY is set")
    gate.add_argument("--runs-root")
    gate.set_defaults(func=cmd_gate)

    stage = sub.add_parser("stage", help="stage recovery commands")
    stage.add_argument("stage_command", choices=["reset"])
    stage.add_argument("--run-id", required=True)
    stage.add_argument("--stage", required=True)
    stage.add_argument("--approver", required=True, help="human:<id>")
    stage.add_argument("--reason", required=True)
    stage.add_argument("--runs-root")
    stage.set_defaults(func=cmd_stage)

    veto = sub.add_parser("veto", help="blocking veto by a role")
    veto.add_argument("--run-id", required=True)
    veto.add_argument("--stage", required=True)
    veto.add_argument("--role", required=True)
    veto.add_argument("--code", required=True, help="veto code, e.g. VETO-SEC-01")
    veto.add_argument("--evidence", action="append", required=True)
    veto.add_argument("--runs-root")
    veto.set_defaults(func=cmd_veto)

    verify = sub.add_parser("verify", help="deterministic run verification")
    verify.add_argument("--run-id", required=True)
    verify.add_argument("--stage")
    verify.add_argument("--require-gate", action="store_true")
    verify.add_argument("--key-file", help="HMAC key for the signed checkpoint")
    verify.add_argument("--store", help="checkpoint store directory")
    verify.add_argument("--runs-root")
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=cmd_verify)

    sign = sub.add_parser("sign", help="sign the journal head into a checkpoint store")
    sign.add_argument("--run-id", required=True)
    sign.add_argument("--key-file", help="HMAC key file (or FACTORY_JOURNAL_KEY)")
    sign.add_argument("--store", help="checkpoint store directory (outside runs/)")
    sign.add_argument("--runs-root")
    sign.add_argument("--json", action="store_true")
    sign.set_defaults(func=cmd_sign)

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
    quarantine.add_argument("--run-id", help="write a SKILL_INSTALL event to this run")
    quarantine.add_argument("--runs-root")
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

    budget = sub.add_parser("budget", help="show attempt limits and mode budgets")
    budget.add_argument("--run-id")
    budget.add_argument("--runs-root")
    budget.set_defaults(func=cmd_budget)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to a legacy code page (cp1252); the CLI prints
    # Russian text and JSON with non-ASCII, so pin UTF-8 on the streams.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):  # pragma: no cover - exotic stream
                pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FactoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    except OSError as exc:
        # A sandboxed caller hits EROFS/EPERM on state.json or blackbox.lock;
        # that is the boundary working, not a crash worth a traceback.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
