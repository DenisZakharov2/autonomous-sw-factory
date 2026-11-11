"""Запуск исполнителя под отдельным Unix-пользователем (граница после bwrap).

`bwrap` изолирует файловую систему, но агент остаётся под тем же uid, что и
фабрика: ему доступны секреты пользователя с правами 0644 в читаемых каталогах
и всё, что видит этот uid.  Следующая граница — отдельный аккаунт.

Root здесь не выдаётся пакетом.  Раньше polkit разрешал оператору любой
транзиентный юнит `run-*` / `factory-*`, а `systemd-run --system` без
`--unit` называется именно `run-*`: это был root без пароля для оператора и
для любого его процесса, включая агента вне песочницы.

Теперь единственная привилегированная программа — `/usr/local/libexec/
factory-root-helper`: создать системного пользователя без шелла, поставить
polkit-правило на себя, записать один env-файл в `/run` и сбросить uid перед
`exec`.  Произвольную команду от root она не принимает: список подкоманд
фиксирован, каждый путь проверяется.

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

Окружение исполнителя передаётся не через командную строку (она видна в
`ps`), а через файл `/run/factory-executor/<run-id>.env` (root:executor
`0640`).  Его пишет только helper, и только если исходный файл принадлежит
вызывающему `pkexec`.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterator

from .base import RuntimeFailure
from .root_helper import (
    ENV_ROOT,
    HelperError,
    LEGACY_POLKIT_RULE,
    POLKIT_RULE_PATH,
    POLKIT_RULE_TEMPLATE,
    prepare,
    render_helper_script,
    validate_operator,
)

EXECUTOR_USER_DEFAULT = "factory-executor"
HELPER_PATH = Path("/usr/local/libexec/factory-root-helper")
# Имя правила живёт в root_helper: установленная копия helper'а ставит ровно то
# же правило, что и этот модуль.
POLKIT_RULE = POLKIT_RULE_TEMPLATE

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


def privileged(args: list[str]) -> None:
    """Запустить root-helper через pkexec. Произвольный argv в root не уходит."""
    try:
        prepare(args)
    except HelperError as exc:
        raise ExecutorUserError(str(exc)) from exc
    if not helper_installed():
        raise ExecutorUserError(
            f"root-helper не установлен: {HELPER_PATH}. "
            "Выполните `factory setup-executor`."
        )
    completed = _run(["pkexec", "--disable-internal-agent", str(HELPER_PATH), *args])
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ExecutorUserError(
            f"root-helper {' '.join(args)} не удался: "
            f"{detail or 'код ' + str(completed.returncode)}"
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
    if not USER_NAME_RE.fullmatch(user) or user == "root":
        raise ExecutorUserError(f"invalid unix username: {user!r}")
    return user


def systemd_run_available() -> bool:
    """Legacy: транзиентные юниты больше не используются (см. `root_helper`)."""
    return shutil.which("systemd-run") is not None


def helper_installed() -> bool:
    return HELPER_PATH.is_file()


def polkit_allows_helper() -> bool:
    """Дешёвая проба: pkexec не должен открывать диалог."""
    if not helper_installed() or shutil.which("pkexec") is None:
        return False
    completed = _run(["pkexec", "--disable-internal-agent", str(HELPER_PATH), "ping"])
    return completed.returncode == 0


def _step_token(seq: int | str) -> str:
    """Токен шага для имени файла: только `[0-9a-f]`.

    Имя стадии (`s00_intake`) в имя файла не годится: helper принимает только
    `[0-9a-z-]`, иначе шаг не запустится.
    """
    if isinstance(seq, int) and not isinstance(seq, bool):
        return str(seq)
    return hashlib.sha256(str(seq).encode("utf-8")).hexdigest()[:12]


def pid_file_path(run_id: str, seq: int | str) -> Path:
    stem = _UNIT_SAFE_RE.sub("-", f"{run_id}-{_step_token(seq)}").strip("-") or "step"
    return ENV_ROOT / f"{stem}.pid"


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
    """Установить env-файл прогона: root:<executor> 0640, только внутри `/run`.

    Права — 0640 группы исполнителя: посторонние не читают окружение, а сам
    процесс его получает от helper'а уже после сброса uid. Файл живёт в tmpfs
    `/run` и удаляется по завершении прогона (`executor_user_scope`).
    """
    del workdir
    _validate_unix_user(user)
    ensure_env_root(user)
    target = env_file_path(run_id)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, prefix="factory-env-") as tmp:
        tmp.write(_render_env_file(env))
        staging = Path(tmp.name)
    try:
        staging.chmod(0o600)
        privileged(["install-env", user, str(target), str(staging.resolve())])
    finally:
        staging.unlink(missing_ok=True)
    return target


def remove_env_file(run_id: str) -> None:
    target = env_file_path(run_id)
    try:
        privileged(["remove-env", str(target)])
    except ExecutorUserError:
        pass  # tmpfs очистится при перезагрузке; не маскируем исходную ошибку прогона


def remove_pid_file(run_id: str, seq: int | str) -> None:
    """Убрать pid-файл шага (best-effort): он иначе остаётся в tmpfs навсегда."""
    try:
        privileged(["remove-pid", str(pid_file_path(run_id, seq))])
    except ExecutorUserError:
        pass


def ensure_env_root(user: str) -> None:
    """`/run/factory-executor` — tmpfs, поэтому каталог пересоздаётся при необходимости."""
    _validate_unix_user(user)
    privileged(["ensure-env-root", user])


def ensure_ready(user: str) -> None:
    """Проверить инфраструктуру отдельного пользователя."""
    _validate_unix_user(user)
    if executor_user_uid(user) is None:
        raise ExecutorUserError(
            f"пользователь {user!r} не существует; создайте его: `factory setup-executor`"
        )
    if shutil.which("setfacl") is None:
        raise ExecutorUserError("setfacl не найден: нужен пакет acl для грантов исполнителю")
    if not helper_installed() or not polkit_allows_helper():
        raise ExecutorUserError(
            "root-helper не отвечает без пароля. Установите его: `factory setup-executor`"
        )
    ensure_env_root(user)


def executor_command(
    command: list[str],
    *,
    user: str,
    run_id: str,
    seq: int | str,
) -> list[str]:
    """Команда исполнителя: pkexec helper сбрасывает uid и делает exec."""
    _validate_unix_user(user)
    return [
        "pkexec",
        "--disable-internal-agent",
        str(HELPER_PATH),
        "run-as",
        user,
        "--env-file",
        str(env_file_path(run_id)),
        "--pid-file",
        str(pid_file_path(run_id, seq)),
        "--",
        *command,
    ]


def stop_unit(run_id: str, seq: int | str, user: str) -> None:
    """Остановить процесс шага после таймаута клиента (best-effort)."""
    _validate_unix_user(user)
    _run(
        [
            "pkexec",
            "--disable-internal-agent",
            str(HELPER_PATH),
            "stop",
            user,
            str(pid_file_path(run_id, seq)),
        ]
    )


@contextlib.contextmanager
def executor_user_scope(
    user: str, run_id: str, run_dir: Path, env: dict[str, str]
) -> Iterator[None]:
    """Грант на дерево прогона + env-файл на время одного прогона."""
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
        if not directory.exists():
            continue
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


def _manual_install(helper: Path) -> str:
    return (
        "root-helper не установлен. Это единственная команда, которой нужен sudo:\n"
        f"  sudo install -m 0755 -o root -g root {helper} {HELPER_PATH}\n"
        "После неё выполните `factory setup-executor` ещё раз: helper сам поставит "
        "polkit-правило и снимет старое правило `50-factory-transient-units.rules`.\n"
        "Пока helper не установлен, старое правило (если оно уже стоит) продолжает "
        "разрешать root без пароля — удалите его сейчас:\n"
        f"  sudo rm -f {LEGACY_POLKIT_RULE}\n"
    )


def setup_executor_user(user: str, operator: str) -> dict[str, Any]:
    """Идемпотентная настройка: helper, polkit на этот helper, пользователь, /run, гранты."""
    _validate_unix_user(user)
    try:
        validate_operator(operator)
    except HelperError as exc:
        raise ExecutorUserError(str(exc)) from exc
    actions: list[str] = []
    staging = Path(tempfile.mkdtemp(prefix="factory-setup-"))
    helper = staging / "factory-root-helper"
    helper.write_text(render_helper_script(), encoding="utf-8")
    helper.chmod(0o755)
    if not helper_installed():
        # Каталог 0700: подменить helper между сборкой и `sudo install` нельзя.
        raise ExecutorUserError(_manual_install(helper))
    try:
        privileged(["install-policy", operator])
        actions.append(f"polkit-правило установлено: {POLKIT_RULE_PATH}")
        privileged(["retire-legacy-polkit"])
        actions.append(f"устаревшее polkit-правило снято, если было: {LEGACY_POLKIT_RULE}")
        privileged(["ensure-user", user])
        actions.append(f"пользователь {user} готов (uid {executor_user_uid(user)})")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    ensure_env_root(user)
    actions.append(f"каталог окружения: {ENV_ROOT} (root:{user} 0750)")
    actions.extend(_home_dir_grants(user))
    return {"user": user, "uid": executor_user_uid(user), "actions": actions}
