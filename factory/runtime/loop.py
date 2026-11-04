"""The stage loop: work order → executor → declared artifacts → verification.

The loop never invents progress.  It submits only declared artifacts that
exist, closes only gates whose approver is a role, and stops on every human
gate.  Anything else (tampering, a crashed executor, an exhausted budget, a
deadlocked task graph) is a stop with exit code 3, not a retry forever.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..artifacts import resolve_recorded_path, sha256_dir, validate_stage
from ..config import load_artifacts, load_budget, load_models, load_pipeline, project_root
from ..engine import (
    VERIFIER,
    _model_for,
    _task_deliverable_errors,
    gate_approve,
    gate_reject,
    next_action,
    submit_work,
    task_accept,
    task_claim,
    task_reject,
    verification_policy,
    verification_report,
)
from ..errors import FactoryError, RunError
from ..journal import sha256_file
from ..paths import run_dir as safe_run_dir
from ..state import find_stage, load_state
from ..verify import verify_run
from .base import Executor, RunContext, RuntimeFailure, safe_write_text
from .evidence import EvidenceStore
from .guard import Fingerprint
from .prompt import build_prompt
from .submit import work_order_submit_plan
from .usage import UsageLedger

HUMAN_STOP = 2
COMPLETE = 0
STOP = 3

# Жёсткий лимит контекста роли в символах: max_tokens модели × 4 символа на
# токен (русский текст) × MAX_CHARS_RATIO. Контекст больше лимита не подаётся
# исполнителю вообще — стадия останавливается с context_exceeded.
MAX_CHARS_RATIO = 0.75


def _context_limit_chars(role: str | None) -> int:
    """Лимит окна модели роли в символах по factory/models.yaml.

    Tier берётся из фронтматтера карточки роли (default_model_tier),
    tier-2-balanced — если карточки или её tier нет.
    """
    tier = "tier-2-balanced"
    if role:
        try:
            tier = str(_model_for(role)["tier"])
        except Exception:
            tier = "tier-2-balanced"
    max_tokens = int(load_models()["tiers"][tier]["max_tokens"])
    return int(max_tokens * 4 * MAX_CHARS_RATIO)


def _is_verification_order(order: dict[str, Any]) -> bool:
    """A verifier step carries the report and nothing else, and says so in data."""
    specs = order.get("artifacts") or []
    return bool(specs) and all(spec.get("report") == "verification" for spec in specs)


def _report_verdict(
    run_dir: Path, order: dict[str, Any], registry: dict[str, Any]
) -> tuple[str | None, str]:
    """`(verdict, reason)` of the report this order is about, or `(None, "")`."""
    state = load_state(run_dir)
    if verification_policy(state) != "role":
        return None, ""
    try:
        _path, errors, verdict, reason = verification_report(
            run_dir,
            registry,
            stage_id=str(order["stage"]),
            task_id=str(order["task_id"]) if order.get("task_id") else None,
        )
    except FactoryError:
        return None, ""
    if errors:
        return None, "; ".join(errors[:3])
    return verdict, reason


@dataclass(slots=True)
class LoopResult:
    run_id: str
    status: str
    code: int
    steps: int
    stage: str | None
    message: str
    order: dict[str, Any] | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    evidence_path: str | None = None

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def needs_human(self) -> bool:
        return self.code == HUMAN_STOP


def _artifact_digest(order: dict[str, Any], run_dir: Path, task: dict[str, Any] | None) -> str:
    """Digest of the declared artifacts' current content, to detect a stuck stage."""
    digest = hashlib.sha256()
    roots = [
        str(spec.get("path")) for spec in order.get("artifacts") or [] if spec.get("root")
    ]
    for entry in sorted(work_order_submit_plan(order, run_dir, task=task), key=lambda e: e["path"]):
        path = resolve_recorded_path(run_dir, entry["path"], root_paths=roots)
        try:
            value = sha256_dir(path) if path.is_dir() else sha256_file(path)
        except OSError:
            value = "unreadable"
        digest.update(f"{entry['path']}:{value}\n".encode("utf-8"))
    return digest.hexdigest()


def _collect_writable_paths(
    order: dict[str, Any], run_dir: Path, task: dict[str, Any] | None
) -> dict[Path, bool]:
    """Paths the executor may write, derived from the order, never the run root.

    A file artifact is exposed through its parent directory (so an editor can
    write atomically); a directory artifact and a task directory are exposed
    directly.  Root artifacts are exposed one file at a time: the project root
    as a whole is never mounted writable, otherwise `s15_quarantine` could
    rewrite `factory/` itself.
    """
    run = run_dir.resolve()
    project = project_root().resolve()
    entries: dict[Path, bool] = {}

    def add(candidate: Path, is_dir: bool, *, root: bool = False) -> None:
        resolved = candidate.resolve()
        if not root:
            if resolved != run and run not in resolved.parents:
                raise RuntimeFailure(f"writable path escapes the run: {resolved}")
            if resolved == run:
                raise RuntimeFailure("refusing to expose the run directory writable")
        else:
            if project not in resolved.parents:
                raise RuntimeFailure(f"root artifact escapes the project: {resolved}")
        entries[resolved] = entries.get(resolved, False) or is_dir

    for spec in order.get("artifacts") or []:
        raw = str(spec.get("resolved_path") or spec.get("path") or "")
        if not raw or "{" in raw:
            continue
        is_root = bool(spec.get("root"))
        candidate = Path(raw) if Path(raw).is_absolute() else (project if is_root else run) / raw
        if spec.get("directory"):
            add(candidate, True, root=is_root)
        elif candidate.parent == (project if is_root else run):
            add(candidate, False, root=is_root)
        else:
            add(candidate.parent, True, root=is_root)

    if task is not None and not _is_verification_order(order):
        # A verifier reads the task's deliverable and allowed files; it must
        # not get a writable mount for any of them.  Its only write scope is
        # the report spec already added above.
        for entry in task.get("allowed_files") or []:
            raw = str(entry)
            candidate = Path(raw) if Path(raw).is_absolute() else run / raw
            if raw.endswith("/"):
                add(candidate, True)
            elif candidate.parent == run:
                add(candidate, False)
            else:
                add(candidate.parent, True)

    # The Hermes adapter writes its usage file here; everything else under
    # `runtime/` (ledger, evidence) stays read-only for the executor.
    add(run / "runtime" / "agent", True)
    return entries


def _prepare_writable_paths(entries: dict[Path, bool]) -> tuple[list[str], list[Path]]:
    created: list[Path] = []
    for path in sorted(entries):
        is_dir = entries[path]
        if path.is_symlink():
            raise RuntimeFailure(f"refusing to expose a symlink to the executor: {path}")
        if is_dir:
            if path.exists() and not path.is_dir():
                raise RuntimeFailure(f"expected a directory for the executor: {path}")
            path.mkdir(parents=True, exist_ok=True)
            continue
        if path.exists():
            if not path.is_file():
                raise RuntimeFailure(f"expected a file for the executor: {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        created.append(path)
    return [str(path) for path in sorted(entries)], created


def _cleanup_placeholders(paths: list[Path]) -> None:
    """Remove placeholder files the executor never wrote.

    A pre-created empty artifact would otherwise look "populated" to
    `work_order_submit_plan` and be submitted as if the executor had produced
    it.
    """
    for path in paths:
        try:
            if path.is_file() and not path.is_symlink() and path.stat().st_size == 0:
                path.unlink()
        except OSError:
            continue


def _submit_order(
    run_id: str,
    order: dict[str, Any],
    run_dir: Path,
    root: str | Path | None,
    state: dict[str, Any],
    *,
    role: str | None = None,
) -> list[str]:
    """Submit the declared artifacts this step's role is responsible for.

    A stage order lists every artifact of the stage.  Submitting the whole
    plan after each step re-submits artifacts that other roles produced
    earlier, attributes them to a role that did not run, and inflates the
    journal.  The acting role filters the plan.
    """
    task = state["tasks"].get(str(order.get("task_id"))) if order.get("task_id") else None
    plan = work_order_submit_plan(order, run_dir, task=task)
    if role:
        plan = [entry for entry in plan if entry["owner"] == role]
    if not plan:
        return []
    task_id = order.get("task_id")
    if task_id:
        task = state["tasks"][str(task_id)]
        owner = str(task["owner"])
        if task["status"] == "pending":
            task_claim(run_id, str(task_id), owner, root=root)
        submit_work(
            run_id,
            stage_id=str(order["stage"]),
            role=owner,
            artifacts=[entry["path"] for entry in plan],
            task_id=str(task_id),
            root=root,
            why=f"Runtime submitted declared artifacts for {task_id}",
        )
        return [entry["path"] for entry in plan]
    grouped: dict[str, list[str]] = {}
    for entry in plan:
        grouped.setdefault(entry["owner"], []).append(entry["path"])
    for owner, paths in grouped.items():
        submit_work(
            run_id,
            stage_id=str(order["stage"]),
            role=owner,
            artifacts=paths,
            root=root,
            why=f"Runtime submitted declared artifacts for {order['stage']}",
        )
    return [entry["path"] for entry in plan]


def _complete_verification(
    run_id: str,
    order: dict[str, Any],
    run_dir: Path,
    root: str | Path | None,
    registry: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    """Act on the verifier's report; return a note when the step did not land.

    PASS closes the task or advances the stage to its gate.  FAIL is not a
    crash and not an approval: the task or the stage goes back to rework with
    the verifier's reason in the journal.  An invalid report returns the
    validation errors as well, so the loop can leave an evidence step: a retry
    is otherwise invisible in the audit.
    """
    stage_id = str(order["stage"])
    task_id = str(order["task_id"]) if order.get("task_id") else None
    path, errors, verdict, reason = verification_report(
        run_dir, registry, stage_id=stage_id, task_id=task_id
    )
    if errors:
        try:
            report_rel = path.relative_to(run_dir.resolve()).as_posix()
        except ValueError:
            report_rel = path.as_posix()
        return (
            "verification report is not valid: " + "; ".join(errors[:3]),
            {"errors": errors, "report": report_rel},
        )
    if task_id:
        if verdict == "FAIL":
            task_reject(
                run_id, task_id, reason or "the verifier's report says FAIL", root=root
            )
            return "", None
        try:
            task_accept(run_id, task_id, root=root)
        except FactoryError as exc:
            return str(exc), None
        return "", None
    state = load_state(run_dir)
    submitted = _submit_order(run_id, order, run_dir, root, state, role=VERIFIER)
    if not submitted:
        return "the verification report exists but was not submitted", None
    if verdict == "FAIL":
        gate_reject(
            run_id,
            gate_id=str(order["gate"]["id"]),
            approver=VERIFIER,
            evidence=[f"verifier report FAIL: {reason or 'no reason given'}"],
            root=root,
        )
    return "", None


def _ready_build_tasks(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Pending build tasks whose dependencies are already accepted.

    A task in `review` or `in_progress` means the sequential path owns the
    next step; parallel work must not jump ahead of it.
    """
    tasks = [task for task in state["tasks"].values() if task.get("stage") == "s4_build"]
    if any(task["status"] in {"review", "in_progress", "escalated", "blocked"} for task in tasks):
        return []
    return [
        task
        for task in tasks
        if task["status"] == "pending"
        and all(
            state["tasks"].get(dep, {}).get("status") == "accepted"
            for dep in task.get("deps", [])
        )
    ]


def _record_parallel_usage(
    ledger: UsageLedger, outcomes: list[Any], *, executor_name: str
) -> None:
    for outcome in outcomes:
        usage = dict(outcome.usage or {})
        ledger.append(
            {
                "step": ledger.next_step(),
                "parallel": True,
                "task_id": outcome.task_id,
                "stage": "s4_build",
                "role": outcome.role,
                "executor": executor_name,
                "exit_code": 0 if outcome.ok else 1,
                "seconds": outcome.seconds,
                "tokens_total": usage.get("tokens_total"),
                "tokens_in": usage.get("tokens_in"),
                "tokens_out": usage.get("tokens_out"),
                "cache_read": usage.get("cache_read"),
                "api_calls": usage.get("api_calls"),
                "model": usage.get("model"),
                "usd": usage.get("usd"),
                "prompt_chars": outcome.prompt_chars,
                "summary": (outcome.error or "worktree task merged")[:500],
                "transcript": outcome.transcript,
            }
        )


def run_loop(
    run_id: str,
    executor: Executor,
    *,
    max_steps: int = 60,
    root: str | Path | None = None,
    budget: dict[str, Any] | None = None,
    enforce: dict[str, Any] | None = None,
    parallel_tasks: int = 0,
    progress: Callable[[int, dict[str, Any]], None] | None = None,
) -> LoopResult:
    run_dir = safe_run_dir(run_id, root)
    if not run_dir.is_dir():
        raise RunError(f"run {run_id!r} does not exist; run `factory init` first")
    pipeline = load_pipeline()
    state = load_state(run_dir)
    registry = load_artifacts(str(state["mode"]))
    mode = str(state["mode"])
    if budget is None:
        budget = pipeline["modes"][mode].get("budget")
    if enforce is None:
        enforce = load_budget().get("enforce")
    ledger = UsageLedger(run_dir / "runtime" / "usage.jsonl")
    evidence_store = EvidenceStore(run_dir)
    evidence: list[str] = []
    steps = 0
    last_order: dict[str, Any] | None = None
    last_digest: str | None = None
    no_progress_strikes = 0

    def finish(status: str, code: int, message: str, stage: str | None = None) -> LoopResult:
        totals = ledger.totals().as_dict()
        return LoopResult(
            run_id=run_id,
            status=status,
            code=code,
            steps=steps,
            stage=stage,
            message=message,
            order=last_order,
            usage=totals,
        )

    for step in range(1, max_steps + 1):
        steps = step
        try:
            order = next_action(run_id, root=root)
        except FactoryError as exc:
            return finish("error", STOP, str(exc))
        last_order = order
        if progress is not None:
            progress(step, order)
        action = str(order["action"])
        stage_id = order.get("stage")

        if parallel_tasks > 1 and action == "produce" and stage_id == "s4_build":
            current = load_state(run_dir)
            ready = _ready_build_tasks(current)
            if len(ready) >= 2:
                from .worktree import run_parallel_tasks

                stage = find_stage(pipeline, "s4_build")
                try:
                    batch = run_parallel_tasks(
                        run_id,
                        run_dir,
                        stage,
                        ready[:parallel_tasks],
                        executor,
                        root=root,
                        max_workers=parallel_tasks,
                    )
                except FactoryError as exc:
                    path = evidence_store.write_step(
                        step, order, {"status": "parallel_failed", "error": str(exc)}
                    )
                    stop = finish("parallel_failed", STOP, str(exc), stage_id)
                    stop.evidence_path = str(path)
                    return stop
                _record_parallel_usage(
                    ledger,
                    batch.outcomes,
                    executor_name=getattr(executor, "name", type(executor).__name__),
                )
                evidence_store.write_step(
                    step,
                    order,
                    {
                        "status": "parallel_batch",
                        "tasks": batch.tasks,
                        "merged": sorted(batch.merged),
                        "outcomes": [
                            {
                                "task_id": item.task_id,
                                "ok": item.ok,
                                "error": item.error,
                                "changed": item.changed,
                                "worktree": item.worktree,
                            }
                            for item in batch.outcomes
                        ],
                    },
                )
                violation = ledger.budget_violation(budget, enforce)
                if violation:
                    path = evidence_store.write_step(
                        step, order, {"status": "budget", "violation": violation}
                    )
                    stop = finish("budget", STOP, violation, stage_id)
                    stop.evidence_path = str(path)
                    return stop
                if not batch.ok:
                    errors = [item.error for item in batch.outcomes if item.error]
                    stop = finish(
                        "parallel_failed",
                        STOP,
                        "; ".join(errors) or "no parallel task produced artifacts",
                        stage_id,
                    )
                    return stop
                last_digest = None
                evidence = []
                continue

        if action == "complete":
            return finish("complete", COMPLETE, "all gates approved", None)
        if action == "blocked":
            return finish("blocked", STOP, str(order.get("instructions")))
        if action == "wait":
            path = evidence_store.write_step(
                step, order, {"status": "wait", "message": order.get("instructions")}
            )
            result = finish("deadlock", STOP, str(order.get("instructions")), stage_id)
            result.evidence_path = str(path)
            return result

        if action == "gate":
            approver = str(order["gate"]["approver"])
            if approver == "human" or approver.startswith("human:"):
                path = evidence_store.write_human_stop(
                    order,
                    {
                        "status": "needs_human",
                        "next_command": order.get("next_command"),
                        "evidence": evidence_store.gate_evidence(order),
                        "pending_artifacts": order.get("pending_artifacts"),
                    },
                )
                result = finish(
                    "human_gate",
                    HUMAN_STOP,
                    str(order.get("next_command")),
                    stage_id,
                )
                result.evidence_path = str(path)
                return result

            gate_errors = verify_run(run_dir, str(stage_id), require_gate=False)
            if gate_errors:
                gate_reject(
                    run_id,
                    gate_id=str(order["gate"]["id"]),
                    approver=approver,
                    evidence=gate_errors[:5],
                    root=root,
                )
                evidence = gate_errors[:5]
                continue
            verdict, reason = _report_verdict(run_dir, order, registry)
            if verdict == "FAIL":
                # The verifier said no.  The gate is not closed; the stage goes
                # back to rework with the verifier's own words as evidence.
                gate_reject(
                    run_id,
                    gate_id=str(order["gate"]["id"]),
                    approver=approver,
                    evidence=[f"verifier report FAIL: {reason or 'no reason given'}"],
                    root=root,
                )
                evidence = [f"verifier report FAIL: {reason or 'no reason given'}"]
                continue
            gate_evidence = evidence_store.gate_evidence(order)
            try:
                gate_approve(
                    run_id,
                    gate_id=str(order["gate"]["id"]),
                    approver=approver,
                    evidence=gate_evidence,
                    root=root,
                )
            except FactoryError as exc:
                # A verifier that cannot approve must reject, so the stage goes
                # back to the producer with the engine's own words as evidence.
                try:
                    gate_reject(
                        run_id,
                        gate_id=str(order["gate"]["id"]),
                        approver=approver,
                        evidence=[str(exc)],
                        root=root,
                    )
                except FactoryError as reject_exc:
                    path = evidence_store.write_step(
                        step,
                        order,
                        {
                            "status": "gate_failed",
                            "approver": approver,
                            "error": str(exc),
                            "reject_error": str(reject_exc),
                        },
                    )
                    result = finish("gate_failed", STOP, str(exc), stage_id)
                    result.evidence_path = str(path)
                    return result
                evidence = [str(exc)]
                continue
            evidence = []
            continue

        if action == "verify" and order.get("task_id") and verification_policy(load_state(run_dir)) != "role":
            # Historical run without the verifier policy: keep the old
            # behaviour so an interrupted pre-1.0.2 run can still be finished.
            state = load_state(run_dir)
            task = state["tasks"][str(order["task_id"])]
            errors = _task_deliverable_errors(run_dir, task, registry)
            if errors:
                task_reject(run_id, str(order["task_id"]), "; ".join(errors[:3]), root=root)
                evidence = errors[:2]
            else:
                task_accept(run_id, str(order["task_id"]), root=root)
                evidence = []
            continue

        if action not in {"produce", "verify"}:
            return finish("unsupported", STOP, f"work order action {action!r} is not executable")

        if (
            action == "produce"
            and not order.get("task_id")
            and not order.get("pending_artifacts")
            and not evidence
        ):
            # Nothing is missing *by shape*, but the stage has rules of its own
            # (acceptance rows, REQ parity, the plan and task graphs).  If one
            # of them fails, a producer has work to do and the step must run;
            # submitting the same files again would loop forever and mint an
            # ARTIFACT_SUBMIT per iteration.
            current = load_state(run_dir)
            stage_errors = validate_stage(
                run_dir,
                find_stage(pipeline, str(stage_id)),
                registry,
                current["tasks"],
                verification_policy=None,
            )
            if stage_errors:
                evidence = stage_errors[:3]
            else:
                submitted = _submit_order(
                    run_id, order, run_dir, root, current, role=order.get("next_role")
                )
                if not submitted:
                    evidence = [
                        "стадия не имеет незакрытых артефактов, но и сдавать нечего"
                    ]
                continue

        before = Fingerprint.capture(run_dir)
        state = load_state(run_dir)
        task = state["tasks"].get(str(order.get("task_id"))) if order.get("task_id") else None
        writable_entries = _collect_writable_paths(order, run_dir, task)
        writable, placeholders = _prepare_writable_paths(writable_entries)
        prompt = build_prompt(
            order, run_dir=run_dir, role=order.get("next_role"), evidence=evidence
        )
        prompt_chars = len(prompt)
        context_limit = _context_limit_chars(order.get("next_role"))
        if prompt_chars > context_limit:
            # Роль никогда не получает обрезанное вслепую сообщение: если
            # свои артефакты + RAG-фрагменты не влезают в окно модели,
            # задача останавливается с ошибкой и ждёт дробирования.
            path = evidence_store.write_step(
                step,
                order,
                {
                    "status": "context_exceeded",
                    "prompt_chars": prompt_chars,
                    "limit_chars": context_limit,
                    "model_tier": _model_for(order.get("next_role") or "")["tier"],
                },
            )
            stop = finish(
                "context_exceeded",
                STOP,
                (
                    f"контекст слишком велик для окна модели "
                    f"({prompt_chars} символов > {context_limit}); "
                    "разбей задачу на подзадачи, а не подавай с обрезанным промптом"
                ),
                stage_id,
            )
            stop.evidence_path = str(path)
            return stop
        context = RunContext(
            run_id=run_id,
            run_dir=run_dir,
            step=step,
            order=order,
            mode=mode,
            prompt=prompt,
            allowed_paths=[str(item.get("path")) for item in order.get("pending_artifacts") or []],
            evidence=list(evidence),
            runtime_dir=run_dir / "runtime",
            sequence=int(state["event_count"]),
            extra_read_write=tuple(writable),
        )
        safe_write_text(context.path_in_runtime("prompt.md"), prompt)
        started = time.monotonic()
        try:
            result = executor.execute(order, context)
        except Exception as exc:  # noqa: BLE001 - an adapter crash is a stop, not a traceback
            _cleanup_placeholders(placeholders)
            path = evidence_store.write_step(
                step, order, {"status": "executor_crash", "error": repr(exc), "prompt": prompt}
            )
            stop = finish("executor_failed", STOP, f"{type(exc).__name__}: {exc}", stage_id)
            stop.evidence_path = str(path)
            return stop
        _cleanup_placeholders(placeholders)
        elapsed = time.monotonic() - started

        after = Fingerprint.capture(run_dir)
        drift = before.drift(after)
        usage: dict[str, Any] = dict(result.usage or {})
        ledger_step = ledger.next_step()
        ledger.append(
            {
                "step": ledger_step,
                "loop_step": step,
                "stage": stage_id,
                "task_id": order.get("task_id"),
                "role": order.get("next_role"),
                "executor": getattr(executor, "name", type(executor).__name__),
                "exit_code": result.exit_code,
                "seconds": round(elapsed, 3),
                "tokens_total": usage.get("tokens_total"),
                "tokens_in": usage.get("tokens_in"),
                "tokens_out": usage.get("tokens_out"),
                "cache_read": usage.get("cache_read"),
                "api_calls": usage.get("api_calls"),
                "model": usage.get("model"),
                "usd": usage.get("usd"),
                "prompt_chars": len(prompt),
                "stdout_chars": len(result.stdout or ""),
                "stderr_chars": len(result.stderr or ""),
                "summary": (result.summary or "")[:500],
                "transcript": result.transcript_path,
            }
        )
        if drift:
            path = evidence_store.write_step(
                step,
                order,
                {
                    "status": "tampered",
                    "drift": drift,
                    "stdout": (result.stdout or "")[-4000:],
                    "stderr": (result.stderr or "")[-4000:],
                },
            )
            stop = finish("tampered", STOP, "; ".join(drift), stage_id)
            stop.evidence_path = str(path)
            return stop
        if not result.ok:
            path = evidence_store.write_step(
                step,
                order,
                {
                    "status": "executor_failed",
                    "exit_code": result.exit_code,
                    "stdout": (result.stdout or "")[-4000:],
                    "stderr": (result.stderr or "")[-4000:],
                },
            )
            message = (result.stderr or result.stdout or "executor returned a non-zero exit code").strip()
            stop = finish("executor_failed", STOP, message[-800:], stage_id)
            stop.evidence_path = str(path)
            return stop

        violation = ledger.budget_violation(budget, enforce)
        if violation:
            path = evidence_store.write_step(
                step, order, {"status": "budget", "violation": violation}
            )
            stop = finish("budget", STOP, violation, stage_id)
            stop.evidence_path = str(path)
            return stop

        state = load_state(run_dir)
        task = state["tasks"].get(str(order.get("task_id"))) if order.get("task_id") else None
        submit_note = ""
        if _is_verification_order(order):
            submit_note, invalid_report = _complete_verification(
                run_id, order, run_dir, root, registry
            )
            if invalid_report:
                # A retry after a rejected report must be visible in the audit,
                # not only in the executor transcript.
                evidence_store.write_step(
                    step, order, {"status": "report_invalid", **invalid_report}
                )
        else:
            submitted = _submit_order(
                run_id, order, run_dir, root, state, role=order.get("next_role")
            )
            if not submitted:
                submit_note = (
                    "исполнитель завершился успешно, но ни один объявленный артефакт "
                    "не появился на диске"
                )
        digest = _artifact_digest(order, run_dir, task)
        stage_now = load_state(run_dir)["stage_status"].get(str(order.get("stage")))
        if digest == last_digest and digest:
            no_progress_strikes += 1
        else:
            no_progress_strikes = 0
        last_digest = digest
        if no_progress_strikes >= 2:
            path = evidence_store.write_step(
                step,
                order,
                {
                    "status": "no_progress",
                    "digest": digest,
                    "stage_status": stage_now,
                    "message": "declared artifacts did not change across two consecutive steps",
                },
            )
            stop = finish(
                "no_progress",
                STOP,
                "declared artifacts did not change across two consecutive steps; "
                "the stage cannot converge without a human",
                stage_id,
            )
            stop.evidence_path = str(path)
            return stop
        if submit_note:
            evidence = [submit_note]
            continue
        evidence = []

    return finish(
        "max_steps",
        STOP,
        f"stopped after {max_steps} steps without reaching a gate or completion",
        last_order.get("stage") if last_order else None,
    )
