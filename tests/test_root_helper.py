"""Root-helper: фиксированный список команд, без shell."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from factory.runtime.root_helper import (
    HelperError,
    OPERATOR_NAME_RE,
    POLKIT_RULE_TEMPLATE,
    prepare,
    render_helper_script,
    system_python,
)


def test_polkit_rule_does_not_grant_transient_units():
    assert "manage-units" not in POLKIT_RULE_TEMPLATE
    assert "run-" not in POLKIT_RULE_TEMPLATE
    assert "org.freedesktop.policykit.exec" in POLKIT_RULE_TEMPLATE
    assert "/usr/local/libexec/factory-root-helper" in POLKIT_RULE_TEMPLATE


def test_helper_rejects_unknown_commands_and_root():
    with pytest.raises(HelperError, match="unknown"):
        prepare(["bash", "-c", "id"])
    with pytest.raises(HelperError, match="username"):
        prepare(["run-as", "root", "--env-file", "/run/factory-executor/demo.env",
                 "--pid-file", "/run/factory-executor/demo.pid", "--", "/bin/true"])


def test_helper_rejects_paths_outside_the_env_root():
    with pytest.raises(HelperError, match="outside"):
        prepare(["install-env", "factory-executor", "/tmp/evil.env", "/tmp/src"])
    with pytest.raises(HelperError, match="outside"):
        prepare(["remove-env", "/run/factory-executor/../shadow"])


def test_run_as_keeps_the_command_as_argv():
    command, args = prepare([
        "run-as", "factory-executor",
        "--env-file", "/run/factory-executor/demo.env",
        "--pid-file", "/run/factory-executor/demo.pid",
        "--", "bwrap", "--", "hermes",
    ])
    assert command == "run-as"
    assert args[:3] == [
        "factory-executor",
        "/run/factory-executor/demo.env",
        "/run/factory-executor/demo.pid",
    ]
    assert args[3:] == ["bwrap", "--", "hermes"]


def test_operator_name_cannot_break_the_polkit_rule():
    """Имя оператора попадает в JS-строку правила: кавычка дала бы root."""
    with pytest.raises(HelperError, match="operator"):
        prepare(["install-policy", 'x"; polkit.addRule(function(){return 1;}); //'])
    assert not OPERATOR_NAME_RE.fullmatch('a"b')
    assert OPERATOR_NAME_RE.fullmatch("anony")
    assert OPERATOR_NAME_RE.fullmatch("first.last")


def test_pid_file_name_passes_the_helper_name_check():
    """Имя pid-файла обязано пройти собственную проверку helper'а."""
    from factory.runtime.isolation import pid_file_path
    from factory.runtime.root_helper import PID_NAME_RE

    for seq in (5, 0, "s00_intake", "TASK-API-01"):
        name = pid_file_path("run-01", seq).name
        assert PID_NAME_RE.fullmatch(name), name


def test_child_env_names_the_executor_not_the_operator():
    from factory.runtime.root_helper import _child_env

    parsed = {
        "PATH": "/home/operator/bin",
        "USER": "operator",
        "LOGNAME": "operator",
        "SHELL": "/bin/bash",
        "HOME": "/home/operator",
        "LD_PRELOAD": "/tmp/evil.so",
        "FACTORY_ROOT": "/home/operator/factory",
        "ANTHROPIC_API_KEY": "secret",
    }
    env = _child_env(parsed, "factory-executor")
    assert env["PATH"] == "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    assert env["USER"] == "factory-executor"
    assert env["LOGNAME"] == "factory-executor"
    assert env["SHELL"] == "/usr/sbin/nologin"
    assert env["HOME"] == "/home/operator"
    assert "LD_PRELOAD" not in env
    assert "FACTORY_ROOT" not in env
    assert env["ANTHROPIC_API_KEY"] == "secret"


def test_remove_pid_is_a_known_command():
    command, args = prepare(["remove-pid", "/run/factory-executor/run-01-5.pid"])
    assert command == "remove-pid"
    assert args == ["/run/factory-executor/run-01-5.pid"]
    with pytest.raises(HelperError, match="outside"):
        prepare(["remove-pid", "/tmp/run-01-5.pid"])


def test_helper_refuses_malformed_arguments():
    """Каждая подкоманда принимает только свой список аргументов."""
    with pytest.raises(HelperError, match="missing helper command"):
        prepare([])
    with pytest.raises(HelperError, match="run-as"):
        prepare(["run-as", "factory-executor", "--env-file", "/run/factory-executor/a.env"])
    with pytest.raises(HelperError, match="missing a command"):
        prepare(["run-as", "factory-executor", "--env-file", "/run/factory-executor/a.env",
                 "--pid-file", "/run/factory-executor/a.pid", "--", ""])
    with pytest.raises(HelperError, match="run-as"):
        prepare(["run-as", "factory-executor", "--pid-file", "/run/factory-executor/a.pid",
                 "--env-file", "/run/factory-executor/a.env", "--", "/bin/true"])
    with pytest.raises(HelperError, match="absolute path"):
        prepare(["install-env", "factory-executor", "/run/factory-executor/a.env", "relative/path"])
    with pytest.raises(HelperError, match="username"):
        prepare(["ensure-user", "root"])
    with pytest.raises(HelperError, match="ensure-user"):
        prepare(["ensure-user"])
    with pytest.raises(HelperError, match="ping"):
        prepare(["ping", "extra"])
    with pytest.raises(HelperError, match="retire-legacy-polkit"):
        prepare(["retire-legacy-polkit", "extra"])


def test_helper_source_does_not_import_the_factory_package():
    """Root не должен исполнять код, который оператор может переписать."""
    source = Path("factory/runtime/root_helper.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(?:import|from)\s+factory\b", source, re.M)


def test_installed_helper_uses_a_root_owned_interpreter():
    try:
        interpreter = system_python()
    except HelperError as exc:
        pytest.skip(f"no root-owned system python on this host: {exc}")
    path = Path(interpreter)
    assert path.is_absolute()
    assert path.stat().st_uid == 0
    assert not path.stat().st_mode & 0o022
    assert str(path.parent).startswith("/usr")
    assert str(path) != sys.executable or not sys.executable.startswith(str(Path.home()))


def test_rendered_helper_is_self_contained():
    try:
        script = render_helper_script()
    except HelperError as exc:
        pytest.skip(f"no root-owned system python on this host: {exc}")
    assert script.startswith("#!/usr/")
    assert "import factory" not in script
    assert "from factory" not in script
    assert script.rstrip().endswith("raise SystemExit(main())")


def test_rendered_helper_runs_without_the_factory_package(tmp_path):
    """Скрипт, установленный в /usr/local/libexec, не должен зависеть от пакета."""
    try:
        script = render_helper_script()
    except HelperError as exc:
        pytest.skip(f"no root-owned system python on this host: {exc}")
    target = tmp_path / "factory-root-helper"
    target.write_text(script, encoding="utf-8")
    target.chmod(0o755)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "PYTHONPATH": "",
    }
    completed = subprocess.run(
        [str(target), "ping"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
    )
    assert "Traceback" not in completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr
    if os.geteuid() == 0:
        assert completed.returncode == 0
    else:
        assert completed.returncode == 1
        assert "must run as root" in completed.stderr
