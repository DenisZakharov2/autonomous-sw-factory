"""Запуск исполнителя под отдельным Unix-пользователем (граница после bwrap).

`bwrap` изолирует файловую систему, но агент остаётся под тем же uid, что и
фабрика: ему доступны секреты пользователя с правами 0644 в читаемых каталогах
и всё, что видит этот uid.  Следующая граница — отдельный аккаунт.  На машине
без пароля sudo root доступен через транзиентные юниты `systemd-run --system`
(polkit-правило `run-*`/`factory-*` для оператора), поэтому исполнитель
запускается как `systemd-run -p User=<executor> -- bwrap ...`.

Два независимых слоя защиты:

1. **DAC (uid).**  Процесс исполнителя работает под чужим uid: файлы
   пользователя с правами 0600/0700 (`~/.ssh`, `~/.gnupg`, приватные конфиги)
   для него закрыты ядром.  Доступ открывается точечно через POSIX ACL:
   traverse (`--x`) на цепочке каталогов и `rwX` + default-ACL на дерево
   прогона и на `~/.hermes/{cache,logs,installs}` — единственные каталоги
   домашней директории, которые CLI реально нужны на запись.
2. **bwrap (прочитанное пространство).**  В режиме отдельного пользователя
   профиль песочницы уже не монтирует `/` read-only, а собирает пространство
   из явных привязок: системные каталоги ro, каталог прогона rw, control
   plane ro поверх, `runtime/agent` rw.  `/home` внутри пространства не
   существует, кроме пути к каталогу прогона и явных `--sandbox-rw`.

Окружение исполнителя передаётся не через командную строку (она видна в `ps`
и в D-Bus-свойствах юнита), а через `EnvironmentFile` в `/run/factory-executor/
<run_id>.env` — root:executor 0640, ставится одноразовым root-вызовом и
удаляется в конце прогона.
"""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterator

from .base import RuntimeFailure

EXECUTOR_USER_DEFAULT = "factory-executor"
ENV_ROOT = Path("/run/factory-executor")
POLKIT_RULE_PATH = Path("/etc/polkit-1/rules.d/50-factory-transient-units.rules")
POLKIT_RULE = """// autonomous-sw-factory: запуск исполнителя под отдельным uid через
// транзиентные systemd-юниты. Разрешены только юниты run-* (транзиентные)
// и factory-* (юниты фабрики) для пользователя оператора.
polkit.addRule(function(action, subject) {
    if (subject.user !== "%s") {
        return;
    }
    if (action.id === "org.freedesktop.systemd1.manage-units") {
        var unit = action.lookup("unit") || "";
        if (unit.indexOf("run-") === 0 || unit.indexOf("factory-") === 0) {
            return polkit.Result.YES;
        }
    }
});
"""

_HOME_GRANTS = ("cache", "logs", "installs")
_UNIT_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
USER_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


class ExecutorUserError(RuntimeFailure):
    """Отдельный пользователь недоступен или настройка не завершена."""


def _run(argv: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        input=input_text,
        check=False,
    )


def root_call(argv: list[str]) -> None:
    """Root-действие через транзиентный юнит (polkit-правило, без sudo)."""
    completed = _run(
        ["systemd-run", "--system", "--pipe", "--wait", "--collect", "--quiet", *argv]
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ExecutorUserError(
            f"root-вызов через systemd-run не удался ({' '.join(argv)}): {detail or 'код ' + str(completed.returncode)}. "
            "Проверьте polkit-правило: `factory setup-executor`"
        )


def executor_user_uid(user: str) -> int | None:
    try:
        import pwd  # отложенный импорт: модуля pwd нет на Windows
    except ImportError:
        return None
    try:
        return pwd.getpwnam(user).pw_uid
    except KeyError:
        return None


def _validate_unix_user(user: str) -> str:
    """Validate Unix username: lowercase, starts with letter/underscore, max 32 chars."""
    if not USER_NAME_RE.fullmatch(user):
        raise ExecutorUserError(f"invalid unix username: {user!r}")
    return user


def systemd_run_available() -> bool:
    return shutil.which("systemd-run") is not None


def polkit_allows_transient_units() -> bool:
    """Дешёвая проба: транзиентный юнит без root-прав запускается без пароля."""
    completed = _run(["systemd-run", "--system", "--pipe", "--wait", "--collect", "--quiet", "/bin/true"])
    return completed.returncode == 0


def unit_name(run_id: str, seq: int | str) -> str:
    stem = _UNIT_SAFE_RE.sub("-", f"{run_id}-{seq}").strip("-") or "step"
    return f"factory-exec-{stem}.service"


def env_file_path(run_id: str) -> Path:
    return ENV_ROOT / f"{_UNIT_SAFE_RE.sub('-', run_id)}.env"


def _setfacl(args: list[str]) -> None:
    completed = _run(["setfacl", *args])
    if completed.returncode != 0:
        raise ExecutorUserError(f"setfacl {' '.join(args)}: {(completed.stderr or '').strip()}")


def _grant_traverse(user: str, path: Path) -> None:
    """`--x` на каталоге, если мир его не видит: исполнитель проходит, но не читает."""
    if path.stat().st_mode & 0o001:
        return
    _setfacl(["-m", f"u:{user}:--x", "-m", "m::--x", str(path)])


def ensure_traverse(user: str, path: Path) -> None:
    """Открыть исполнителю проход по цепочке предков пути (не выше $HOME)."""
    home = Path.home()
    current = path if path.is_dir() else path.parent
    chain: list[Path] = []
    for ancestor in (current, *current.parents):
        chain.append(ancestor)
        if ancestor == home:
            break
    for directory in reversed(chain):
        _grant_traverse(user, directory)


def grant_run_tree(user: str, run_dir: Path) -> None:
    """Рекурсивный rwX-грант на дерево прогона + default-ACL для новых файлов.

    Файлы, уже принадлежащие исполнителю (повторный прогон), пропускаются:
    доступ у него к ним есть по владению.  Control plane (`state.json`,
    журнал) этот грант покрывает тоже — тонкая граница остаётся за ro-биндами
    `bwrap` внутри песочницы (см. docs/runtime.md §6).
    """
    ensure_traverse(user, run_dir)
    completed = _run(
        ["setfacl", "-R", "-m", f"u:{user}:rwX", "-m", "m::rwX", str(run_dir)]
    )
    if completed.returncode != 0:
        lines = [
            line
            for line in (completed.stderr or "").splitlines()
            if "Operation not permitted" not in line
        ]
        if lines:
            raise ExecutorUserError(
                f"не удалось выдать грант на {run_dir}: {'; '.join(lines[:3])}"
            )
    _setfacl(["-d", "-m", f"u:{user}:rwX", "-d", "-m", "m::rwX", str(run_dir)])


def _env_escape(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]*", value):
        return value
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _render_env_file(env: dict[str, str]) -> str:
    return "".join(f"{key}={_env_escape(value)}\n" for key, value in sorted(env.items()))


def install_env_file(user: str, run_id: str, env: dict[str, str], workdir: Path) -> Path:
    """Установить EnvironmentFile для юнитов прогона: root:<executor> 0640.

    Права — 0640 группы исполнителя: посторонние не читают окружение, а сам
    юнит его получает.  Файл живёт в tmpfs `/run` и дополнительно удаляется
    по завершении прогона (`executor_user_scope`).
    """
    _validate_unix_user(user)
    ensure_env_root(user)
    target = env_file_path(run_id)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, prefix="factory-env-") as tmp:
        tmp.write(_render_env_file(env))
        staging = Path(tmp.name)
    try:
        staging.chmod(0o600)
        root_call([
            "/usr/bin/install",
            "-m", "0640",
            "-o", "root",
            "-g", user,
            str(staging), str(target)
        ])
    finally:
        staging.unlink(missing_ok=True)
    return target


def remove_env_file(run_id: str) -> None:
    target = env_file_path(run_id)
    try:
        root_call(["/usr/bin/rm", "-f", str(target)])
    except ExecutorUserError:
        pass  # tmpfs очистится при перезагрузке; не маскируем исходную ошибку прогона


def ensure_env_root(user: str) -> None:
    """`/run/factory-executor` — tmpfs, поэтому каталог пересоздаётся при необходимости."""
    _validate_unix_user(user)
    if ENV_ROOT.is_dir():
        return
    root_call([
        "/usr/bin/install", "-d", "-m", "0750", "-o", "root", "-g", user, str(ENV_ROOT)
    ])


def ensure_ready(user: str) -> None:
    """Проверить (и где возможно починить) инфраструктуру отдельного пользователя."""
    _validate_unix_user(user)
    if executor_user_uid(user) is None:
        raise ExecutorUserError(
            f"пользователь {user!r} не существует; создайте его: `factory setup-executor`"
        )
    if not systemd_run_available():
        raise ExecutorUserError("systemd-run не найден: нужен systemd для транзиентных юнитов")
    if shutil.which("setfacl") is None:
        raise ExecutorUserError("setfacl не найден: нужен пакет acl для грантов исполнителю")
    if not polkit_allows_transient_units():
        raise ExecutorUserError(
            "polkit не разрешает транзиентные юниты без пароля; "
            "установите правило: `factory setup-executor`"
        )
    ensure_env_root(user)


def executor_command(
    command: list[str],
    *,
    user: str,
    run_id: str,
    seq: int | str,
) -> list[str]:
    """Обернуть команду в транзиентный юнит под отдельным пользователем."""
    return [
        "systemd-run",
        "--system",
        "--pipe",
        "--wait",
        "--collect",
        "--quiet",
        f"--unit={unit_name(run_id, seq)}",
        "-p",
        f"User={user}",
        "-p",
        f"EnvironmentFile={env_file_path(run_id)}",
        "--",
        *command,
    ]


def stop_unit(run_id: str, seq: int | str) -> None:
    """Остановить юнит шага после таймаута клиента (best-effort)."""
    _run(["systemctl", "stop", unit_name(run_id, seq)])


@contextlib.contextmanager
def executor_user_scope(
    user: str, run_id: str, run_dir: Path, env: dict[str, str]
) -> Iterator[None]:
    """Грант на дерево прогона + EnvironmentFile на время одного прогона."""
    ensure_ready(user)
    grant_run_tree(user, run_dir)
    install_env_file(user, run_id, env, run_dir)
    try:
        yield
    finally:
        remove_env_file(run_id)


def _home_dir_grants(user: str) -> list[str]:
    """Точечные гранты на `~/.hermes/{cache,logs,installs}` — единственные
    каталоги агента, которые CLI пишет (см. docs/runtime.md §6)."""
    home = Path.home()
    actions: list[str] = []
    hermes = home / ".hermes"
    for directory in (home, hermes):
        before = directory.stat().st_mode & 0o001
        _grant_traverse(user, directory)
        if not before:
            actions.append(f"traverse: {directory}")
    for name in _HOME_GRANTS:
        target = hermes / name
        if not target.is_dir():
            continue
        _setfacl(["-R", "-m", f"u:{user}:rwX", "-m", "m::rwX", str(target)])
        _setfacl(["-d", "-m", f"u:{user}:rwX", "-d", "-m", "m::rwX", str(target)])
        actions.append(f"rwX: {target}")
    return actions


def setup_executor_user(user: str, operator: str) -> dict[str, Any]:
    """Идемпотентная настройка: polkit-правило, пользователь, /run, гранты."""
    _validate_unix_user(user)
    _validate_unix_user(operator)
    actions: list[str] = []
    if POLKIT_RULE_PATH.is_file():
        actions.append(f"polkit-правило уже установлено: {POLKIT_RULE_PATH}")
    else:
        staging = Path(os.environ.get("TMPDIR", "/tmp")) / POLKIT_RULE_PATH.name
        staging.write_text(POLKIT_RULE % operator, encoding="utf-8")
        staging.chmod(0o644)
        try:
            root_call(
                ["/usr/bin/install", "-m", "0644", "-o", "root", "-g", "root", str(staging), str(POLKIT_RULE_PATH)]
            )
        except ExecutorUserError as exc:
            staging.unlink(missing_ok=True)
            raise ExecutorUserError(
                "не удалось установить polkit-правило без пароля. Установите вручную:\n"
                f"  sudo install -m 0644 {staging} {POLKIT_RULE_PATH}\n"
                "или через docker: docker run --rm -v /etc/polkit-1/rules.d:/rules "
                f"-v {staging}:/tmp/rule ubuntu:24.04 sh -c 'install -m 0644 /tmp/rule /rules/'\n"
                f"Исходная ошибка: {exc}"
            ) from exc
        staging.unlink(missing_ok=True)
        actions.append(f"polkit-правило установлено: {POLKIT_RULE_PATH}")
    if executor_user_uid(user) is None:
        root_call(
            [
                "/usr/sbin/useradd",
                "--system",
                "--no-create-home",
                "--shell",
                "/usr/sbin/nologin",
                "--comment",
                "autonomous-sw-factory executor",
                user,
            ]
        )
        actions.append(f"создан системный пользователь {user} (nologin, без домашнего каталога)")
    else:
        actions.append(f"пользователь {user} уже существует (uid {executor_user_uid(user)})")
    ensure_env_root(user)
    actions.append(f"каталог окружения: {ENV_ROOT} (root:{user} 0750)")
    actions.extend(_home_dir_grants(user))
    return {"user": user, "uid": executor_user_uid(user), "actions": actions}
