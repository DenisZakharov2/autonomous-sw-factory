"""Фиксированный root-helper для границы исполнителя.

`pkexec` может запустить только эту программу и больше ничего. Она не
принимает shell-команду и не поднимает транзиентные systemd-юниты. Каждая
подкоманда либо создаёт непривилегированный аккаунт исполнителя, либо пишет
один env-файл в `/run/factory-executor`, либо сбрасывает uid и делает `exec`.

Процесс, который вызывает `pkexec`, — оператор. После установки polkit-правила
это происходит без пароля, поэтому подкоманда, способная скопировать
произвольный файл в произвольный путь или выполнить что-то от root, была бы
той же дырой, что и прежнее правило на `systemd-run --system`. Пути и имена
проверяются здесь.

Модуль **самодостаточен**: он импортирует только стандартную библиотеку.
Установленная копия — это текст этого файла, а не импорт пакета `factory`:
иначе root исполнил бы код, который оператор может переписать
(`factory/runtime/isolation.py::render_helper_script`).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ENV_ROOT = Path("/run/factory-executor")
POLKIT_RULE_PATH = Path("/etc/polkit-1/rules.d/49-factory-root-helper.rules")
LEGACY_POLKIT_RULE = Path("/etc/polkit-1/rules.d/50-factory-transient-units.rules")
HELPER_PATH = Path("/usr/local/libexec/factory-root-helper")
MAX_ENV_BYTES = 1024 * 1024
USER_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
OPERATOR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]{0,63}$")
ENV_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}\.env$")
PID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}\.pid$")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DROPPED_ENV = {"LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "BASH_ENV", "ENV", "SHELLOPTS"}
IDENTITY_ENV = {"PATH", "USER", "LOGNAME", "SHELL"}
SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Интерпретаторы, которым можно запускать установленный helper. Ни один из
# них не лежит в каталоге пользователя: root не должен исполнять то, что
# оператор может переписать.
PYTHON_CANDIDATES = (
    "/usr/bin/python3",
    "/usr/bin/python3.13",
    "/usr/bin/python3.12",
    "/usr/bin/python3.11",
    "/usr/bin/python3.10",
)

POLKIT_RULE_TEMPLATE = """// autonomous-sw-factory: root без пароля на произвольные команды не выдаётся.
// Разрешён только фиксированный helper, который сам проверяет аргументы.
polkit.addRule(function(action, subject) {
    if (subject.user !== "%s") {
        return;
    }
    if (action.id !== "org.freedesktop.policykit.exec") {
        return;
    }
    var program = action.lookup("program") || "";
    if (program === "/usr/local/libexec/factory-root-helper") {
        return polkit.Result.YES;
    }
});
"""


class HelperError(Exception):
    """Helper отказал в команде. Код возврата — 1."""


def _validate_user(user: str) -> str:
    if not USER_NAME_RE.fullmatch(user) or user == "root":
        raise HelperError(f"refusing username {user!r}")
    return user


def validate_operator(operator: str) -> str:
    """Имя оператора подставляется в polkit-правило как строка JS.

    Набор символов строгий: кавычка или обратный слэш в имени сломали бы
    правило и дали бы оператору произвольный JavaScript от root.
    """
    if not OPERATOR_NAME_RE.fullmatch(operator):
        raise HelperError(f"refusing operator name {operator!r}")
    return operator


def _env_path(value: str, pattern: re.Pattern[str]) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.parent != ENV_ROOT:
        raise HelperError(f"refusing path outside {ENV_ROOT}: {value}")
    if not pattern.fullmatch(path.name) or ".." in path.name:
        raise HelperError(f"refusing env path name {path.name!r}")
    return path


def _root_owned(path: Path) -> bool:
    """Файл принадлежит root и не доступен на запись группе или остальным."""
    try:
        info = path.stat()
    except OSError:
        return False
    return info.st_uid == 0 and not info.st_mode & 0o022


def _root_owned_chain(path: Path) -> bool:
    """Каждый каталог на пути к файлу принадлежит root и не пишется посторонним."""
    current = path
    while True:
        if not _root_owned(current):
            return False
        if current.parent == current:
            return True
        current = current.parent


def system_python() -> str:
    """Путь к системному интерпретатору, принадлежащему root.

    `sys.executable` оператора использовать нельзя: root исполнил бы
    интерпретатор из каталога пользователя, и правка этого файла дала бы
    root без пароля.
    """
    for candidate in PYTHON_CANDIDATES:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if not _root_owned(resolved) or not _root_owned_chain(resolved.parent):
            continue
        return str(resolved)
    raise HelperError(
        "no root-owned system python found "
        f"(tried: {', '.join(PYTHON_CANDIDATES)}); "
        "the helper must not run under the operator's interpreter"
    )


def render_helper_script() -> str:
    """Текст устанавливаемой программы `/usr/local/libexec/factory-root-helper`.

    Это копия этого модуля с shebang на системном интерпретаторе: установленная
    программа не импортирует пакет `factory` и не зависит от окружения
    оператора.
    """
    source = Path(__file__).resolve()
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise HelperError(f"cannot read the helper source {source}: {exc}") from exc
    if re.search(r"^\s*(?:import|from)\s+factory\b", text, re.M):
        raise HelperError("the helper must not import the factory package")
    return f"#!{system_python()}\n{text}"


def prepare(argv: list[str]) -> tuple[str, list[str]]:
    """Проверить argv и вернуть `(команда, аргументы)`. Систему не трогает.

    Аргументы `run-as`: `(user, env_file, pid_file, command...)`.
    """
    if not argv:
        raise HelperError("missing helper command")
    command, rest = argv[0], argv[1:]
    if command == "ping":
        if rest:
            raise HelperError("ping takes no arguments")
        return command, []
    if command == "install-policy":
        if len(rest) != 1:
            raise HelperError("install-policy OPERATOR")
        return command, [validate_operator(rest[0])]
    if command == "retire-legacy-polkit":
        if rest:
            raise HelperError("retire-legacy-polkit takes no arguments")
        return command, []
    if command == "ensure-user":
        if len(rest) != 1:
            raise HelperError("ensure-user USER")
        return command, [_validate_user(rest[0])]
    if command == "ensure-env-root":
        if len(rest) != 1:
            raise HelperError("ensure-env-root USER")
        return command, [_validate_user(rest[0])]
    if command == "install-env":
        if len(rest) != 3:
            raise HelperError("install-env USER DEST SRC")
        user, dest, src = rest
        _validate_user(user)
        _env_path(dest, ENV_NAME_RE)
        source = Path(src)
        if not source.is_absolute():
            raise HelperError("env source must be an absolute path")
        return command, [user, dest, src]
    if command == "remove-env":
        if len(rest) != 1:
            raise HelperError("remove-env DEST")
        _env_path(rest[0], ENV_NAME_RE)
        return command, rest
    if command == "remove-pid":
        if len(rest) != 1:
            raise HelperError("remove-pid PIDFILE")
        _env_path(rest[0], PID_NAME_RE)
        return command, rest
    if command == "stop":
        if len(rest) != 2:
            raise HelperError("stop USER PIDFILE")
        _validate_user(rest[0])
        _env_path(rest[1], PID_NAME_RE)
        return command, rest
    if command == "run-as":
        if "--" not in rest:
            raise HelperError("run-as USER --env-file PATH --pid-file PATH -- CMD")
        flags, cmd = rest[: rest.index("--")], rest[rest.index("--") + 1 :]
        if len(flags) != 5 or flags[1] != "--env-file" or flags[3] != "--pid-file":
            raise HelperError("run-as USER --env-file PATH --pid-file PATH -- CMD")
        if not cmd or not cmd[0]:
            raise HelperError("run-as is missing a command")
        user = _validate_user(flags[0])
        _env_path(flags[2], ENV_NAME_RE)
        _env_path(flags[4], PID_NAME_RE)
        return command, [user, flags[2], flags[4], *cmd]
    raise HelperError(f"unknown helper command {command!r}")


def _caller_uid() -> int | None:
    for key in ("PKEXEC_UID", "SUDO_UID"):
        raw = os.environ.get(key, "")
        if raw.isdigit():
            return int(raw)
    return None


def _open_nofollow(path: Path, flags: int, mode: int = 0o644) -> int:
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    elif path.is_symlink():
        raise HelperError(f"refusing to follow a symlink: {path}")
    return os.open(path, flags, mode)


def _user_ids(user: str) -> tuple[int, int]:
    import pwd

    try:
        record = pwd.getpwnam(user)
    except KeyError as exc:
        raise HelperError(f"user {user!r} does not exist") from exc
    if record.pw_uid == 0:
        raise HelperError(f"refusing to use uid 0 ({user})")
    return record.pw_uid, record.pw_gid


def _parse_env_file(path: Path) -> dict[str, str]:
    fd = _open_nofollow(path, os.O_RDONLY)
    try:
        raw = os.read(fd, MAX_ENV_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > MAX_ENV_BYTES:
        raise HelperError(f"env file is larger than {MAX_ENV_BYTES} bytes")
    env: dict[str, str] = {}
    for line in raw.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not ENV_KEY_RE.fullmatch(key):
            raise HelperError(f"refusing env line {key!r}")
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = (
                value[1:-1]
                .replace("\\n", "\n")
                .replace("\\r", "\r")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )
        env[key] = value
    return env


def _child_env(parsed: dict[str, str], user: str) -> dict[str, str]:
    """Окружение исполнителя.

    `PATH` — системный. `USER`/`LOGNAME`/`SHELL` — исполнителя: оператор не
    должен выдавать себя за исполнителя в логах и в проверках прав. `HOME`
    остаётся домашним каталогом оператора — там лежит `~/.hermes`, куда CLI
    агента пишет (гранты на этот каталог выдаёт `factory setup-executor`).
    """
    env = {"PATH": SAFE_PATH, "LANG": parsed.get("LANG", "C.UTF-8")}
    for key, value in parsed.items():
        if key in DROPPED_ENV or key in IDENTITY_ENV:
            continue
        if key.startswith(("FACTORY_", "LD_")):
            continue
        env[key] = value
    env["USER"] = user
    env["LOGNAME"] = user
    env["SHELL"] = "/usr/sbin/nologin"
    return env


def _ensure_user(user: str) -> None:
    import pwd

    try:
        record = pwd.getpwnam(user)
    except KeyError:
        record = None
    if record is not None:
        if record.pw_uid == 0:
            raise HelperError(f"refusing to adopt uid 0 as {user}")
        return
    import subprocess

    completed = subprocess.run(
        [
            "/usr/sbin/useradd",
            "--system",
            "--no-create-home",
            "--shell",
            "/usr/sbin/nologin",
            "--comment",
            "autonomous-sw-factory executor",
            user,
        ],
        check=False,
    )
    if completed.returncode != 0:
        raise HelperError(f"useradd {user} failed with status {completed.returncode}")


def _ensure_env_root(user: str) -> None:
    uid, gid = _user_ids(user)
    del uid
    if ENV_ROOT.is_symlink():
        raise HelperError(f"refusing a symlinked {ENV_ROOT}")
    ENV_ROOT.mkdir(parents=True, exist_ok=True)
    os.chown(ENV_ROOT, 0, gid)
    os.chmod(ENV_ROOT, 0o750)


def _install_env(user: str, dest: str, src: str) -> None:
    _uid, gid = _user_ids(user)
    target = _env_path(dest, ENV_NAME_RE)
    source = Path(src)
    if source.is_symlink() or not source.is_file():
        raise HelperError(f"env source is not a regular file: {source}")
    caller = _caller_uid()
    if caller is not None and source.stat().st_uid != caller:
        raise HelperError("env source is not owned by the user who invoked pkexec")
    _ensure_env_root(user)
    data = source.read_bytes()
    if len(data) > MAX_ENV_BYTES:
        raise HelperError(f"env file is larger than {MAX_ENV_BYTES} bytes")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = _open_nofollow(target, flags, 0o640)
    try:
        os.fchown(fd, 0, gid)
        os.fchmod(fd, 0o640)
        os.write(fd, data)
    finally:
        os.close(fd)


def _remove_env(dest: str) -> None:
    target = _env_path(dest, ENV_NAME_RE)
    if target.is_symlink():
        target.unlink()
        return
    if target.exists():
        target.unlink()


def _remove_pid(pid_file: str) -> None:
    target = _env_path(pid_file, PID_NAME_RE)
    if target.is_symlink():
        target.unlink()
        return
    if target.exists():
        target.unlink()


def _install_policy(operator: str) -> None:
    """Поставить polkit-правило: без пароля запускается только этот helper."""
    text = POLKIT_RULE_TEMPLATE % operator
    directory = POLKIT_RULE_PATH.parent
    if directory.is_symlink():
        raise HelperError(f"refusing a symlinked {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    staging = POLKIT_RULE_PATH.with_name(f".{POLKIT_RULE_PATH.name}.tmp")
    fd = _open_nofollow(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, text.encode("utf-8"))
        os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o644)
    finally:
        os.close(fd)
    os.replace(staging, POLKIT_RULE_PATH)


def _retire_legacy() -> None:
    path = LEGACY_POLKIT_RULE
    if path.is_symlink():
        path.unlink()
        return
    if not path.exists():
        return
    fd = _open_nofollow(path, os.O_RDONLY)
    try:
        text = os.read(fd, 65536).decode("utf-8", errors="replace")
    finally:
        os.close(fd)
    if "org.freedesktop.systemd1.manage-units" not in text and 'indexOf("run-")' not in text:
        raise HelperError("refusing to remove a polkit rule that is not the factory transient-unit rule")
    path.unlink()


def _stop(user: str, pid_file: str) -> None:
    uid, _gid = _user_ids(user)
    path = _env_path(pid_file, PID_NAME_RE)
    if not path.is_file() or path.is_symlink():
        return
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except ValueError as exc:
        raise HelperError(f"pid file is not an integer: {path}") from exc
    if pid <= 1:
        raise HelperError(f"refusing to signal pid {pid}")
    proc = Path(f"/proc/{pid}")
    if not proc.exists():
        path.unlink(missing_ok=True)
        return
    if proc.stat().st_uid != uid:
        raise HelperError(f"pid {pid} is not owned by {user}")
    os.kill(pid, 9)
    path.unlink(missing_ok=True)


def _run_as(user: str, env_file: str, pid_file: str, command: list[str]) -> None:
    uid, gid = _user_ids(user)
    env_path = _env_path(env_file, ENV_NAME_RE)
    pid_path = _env_path(pid_file, PID_NAME_RE)
    parsed = _parse_env_file(env_path)
    fd = _open_nofollow(pid_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
    finally:
        os.close(fd)
    os.chown(pid_path, 0, gid)
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    if os.geteuid() == 0:
        raise HelperError("privilege drop failed")
    try:
        os.execvpe(command[0], command, _child_env(parsed, user))
    except OSError as exc:
        print(f"factory-root-helper: cannot exec {command[0]}: {exc}", file=sys.stderr)
        os._exit(127)


def execute(command: str, args: list[str]) -> None:
    if command == "ping":
        return
    if command == "install-policy":
        _install_policy(args[0])
        return
    if command == "retire-legacy-polkit":
        _retire_legacy()
        return
    if command == "ensure-user":
        _ensure_user(args[0])
        return
    if command == "ensure-env-root":
        _ensure_env_root(args[0])
        return
    if command == "install-env":
        _install_env(args[0], args[1], args[2])
        return
    if command == "remove-env":
        _remove_env(args[0])
        return
    if command == "remove-pid":
        _remove_pid(args[0])
        return
    if command == "stop":
        _stop(args[0], args[1])
        return
    if command == "run-as":
        _run_as(args[0], args[1], args[2], args[3:])
        return
    raise HelperError(f"unknown helper command {command!r}")


def main(argv: list[str] | None = None) -> int:
    try:
        command, args = prepare(list(sys.argv[1:] if argv is None else argv))
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            raise HelperError("factory-root-helper must run as root (pkexec)")
        execute(command, args)
    except HelperError as exc:
        print(f"factory-root-helper: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
