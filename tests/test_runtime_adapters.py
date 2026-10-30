import json
import os
import stat
from pathlib import Path

import pytest

from factory.engine import init_run, next_action
from factory.runtime.base import RunContext
from factory.runtime.hermes import HermesExecutor
from factory.runtime.opencode import OpenCodeExecutor
from factory.runtime.prompt import build_prompt
from factory.runtime.sandbox import sandbox_command


def _stub(bin_dir: Path, name: str, body: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture()
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_ROOT", str(tmp_path))
    runs = tmp_path / "runs"
    runs.mkdir()
    init_run("run-01", "nano", root=runs)
    run_dir = runs / "run-01"
    order = next_action("run-01", root=runs)
    prompt = build_prompt(order, run_dir=run_dir)
    return RunContext(
        run_id="run-01",
        run_dir=run_dir,
        step=1,
        order=order,
        mode="nano",
        prompt=prompt,
        runtime_dir=run_dir / "runtime",
    )


def test_prompt_carries_role_stage_and_paths(context):
    assert "01-smart-interviewer" in context.prompt
    assert "s00_intake" in context.prompt
    assert "## 1. Цель (JTBD)" in context.prompt
    assert "state.json" in context.prompt
    assert len(context.prompt) < 26000


def test_opencode_run_command_and_usage(tmp_path, monkeypatch, context):
    bin_dir = tmp_path / "bin"
    args_file = tmp_path / "args.txt"
    _stub(
        bin_dir,
        "opencode",
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" > "$ARGS_FILE"\n'
        'echo \'{"type":"text","part":{"type":"text","text":"done",'
        '"usage":{"input":10,"output":5},"cost":0.02}}\'\n',
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("ARGS_FILE", str(args_file))
    executor = OpenCodeExecutor(timeout=30)
    result = executor.execute(context.order, context)
    assert result.exit_code == 0, result.stderr
    assert result.usage["tokens_total"] == 15
    assert result.usage["usd"] == pytest.approx(0.02)
    argv = args_file.read_text(encoding="utf-8").splitlines()
    assert argv[0] == "run"
    assert "--auto" in argv and "--standalone" in argv
    assert "--format" in argv
    assert Path(result.transcript_path).is_file()


def test_opencode_reports_error_events(tmp_path, monkeypatch, context):
    bin_dir = tmp_path / "bin"
    _stub(
        bin_dir,
        "opencode",
        "#!/bin/sh\n"
        'echo \'{"type":"error","error":{"type":"unknown","message":"no provider"}}\'\n',
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    result = OpenCodeExecutor(timeout=30).execute(context.order, context)
    assert result.exit_code == 1
    assert "no provider" in result.summary


def test_opencode_timeout_is_bounded(tmp_path, monkeypatch, context):
    bin_dir = tmp_path / "bin"
    _stub(bin_dir, "opencode", "#!/bin/sh\nsleep 5\n")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    result = OpenCodeExecutor(timeout=0.3).execute(context.order, context)
    assert result.exit_code == 124
    assert "timed out" in result.stderr


def test_missing_binary_is_explicit(context):
    result = OpenCodeExecutor(binary="opencode-not-installed").execute(context.order, context)
    assert result.exit_code == 127
    assert "not found" in result.stderr


def test_hermes_command_reads_usage_file(tmp_path, monkeypatch, context):
    bin_dir = tmp_path / "bin"
    args_file = tmp_path / "args.txt"
    _stub(
        bin_dir,
        "hermes",
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" > "$ARGS_FILE"\n'
        'prev=""\n'
        'for a in "$@"; do\n'
        '  if [ "$prev" = "--usage-file" ]; then printf "%s" "$USAGE_JSON" > "$a"; fi\n'
        '  prev="$a"\n'
        "done\n"
        'echo "READY"\n',
    )
    usage = json.dumps(
        {
            "failed": False,
            "completed": True,
            "total_tokens": 42,
            "input_tokens": 40,
            "output_tokens": 2,
            "estimated_cost_usd": 0.0,
            "model": "test-model",
        }
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("ARGS_FILE", str(args_file))
    monkeypatch.setenv("USAGE_JSON", usage)
    result = HermesExecutor(timeout=30).execute(context.order, context)
    assert result.exit_code == 0, result.stderr
    assert result.usage["tokens_total"] == 42
    argv = args_file.read_text(encoding="utf-8").splitlines()
    assert argv[0] == "-z"
    assert "--in" in argv
    assert "--usage-file" in argv
    assert "--reasoning" in argv


def test_hermes_failed_turn_is_not_success(tmp_path, monkeypatch, context):
    bin_dir = tmp_path / "bin"
    _stub(
        bin_dir,
        "hermes",
        "#!/bin/sh\n"
        'prev=""\n'
        'for a in "$@"; do\n'
        '  if [ "$prev" = "--usage-file" ]; then printf "%s" "$USAGE_JSON" > "$a"; fi\n'
        '  prev="$a"\n'
        "done\n"
        'echo "Provider said: HTTP 400"\n',
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("USAGE_JSON", json.dumps({"failed": True, "completed": False}))
    result = HermesExecutor(timeout=30).execute(context.order, context)
    assert result.exit_code == 1


def test_sandbox_wraps_with_a_narrow_view(tmp_path):
    cmd = sandbox_command(["true"], tmp_path, enabled=True)
    assert cmd[0] == "bwrap"
    assert "--ro-bind" in cmd
    assert cmd[cmd.index("--ro-bind") + 1 : cmd.index("--ro-bind") + 3] == ["/", "/"]
    assert "--unshare-pid" in cmd
    assert "--new-session" in cmd
    assert cmd[cmd.index("--bind") + 1 : cmd.index("--bind") + 3] == [
        str(tmp_path.resolve()),
        str(tmp_path.resolve()),
    ]
    assert cmd[cmd.index("--chdir") + 1] == str(tmp_path.resolve())
    assert cmd[-2:] == ["--", "true"]
    assert sandbox_command(["true"], tmp_path, enabled=False) == ["true"]
    with pytest.raises(Exception):
        sandbox_command(["true"], tmp_path, backend="systemd")


def test_sandbox_binds_context_read_write_paths(tmp_path, context):
    usage = tmp_path / "usage.json"
    cmd = HermesExecutor(sandbox=True).command(
        "prompt", context.run_dir, usage, extra_read_write=(str(tmp_path),)
    )
    assert cmd[0] == "bwrap"
    assert str(tmp_path.resolve()) in cmd
    binds = [cmd[index + 1] for index, item in enumerate(cmd) if item == "--bind"]
    assert str(context.run_dir.resolve()) in binds
    assert str(tmp_path.resolve()) in binds
