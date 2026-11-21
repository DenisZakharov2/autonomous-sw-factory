"""Run one command inside a bubblewrap sandbox with a read-only root.

The original plan used `systemd-run --user` with `ProtectHome=read-only` and
`ProtectSystem=strict`.  Those properties are not available to user scopes:
`systemd-run --user --scope -p ProtectHome=read-only` answers
"Unknown assignment".  System scopes would run the agent as root, which is a
worse trade.  `bwrap` gives the same guarantees at user level, so it is the
default backend.

The trust boundary is the point of this module.  The whole filesystem is
mounted read-only, then the run root is mounted read-only *on top of itself*
so the boundary does not depend on how `/` was mounted next time.  A declared
artifact is replaced by its private copy under `runtime/agent/out`, and the
executor writes the familiar path while the bytes stay in staging.  A path the
work order did not declare is `EROFS`, and replacing a control-plane file with
a rename is `EBUSY` because that file is a mount point.

Read-only is not private when the executor runs as the operator.  Secret home
directories are therefore replaced by an empty tmpfs, credential files by
`/dev/null`, and `~/.hermes` keeps only the adapter's runtime directories and
the CLI code on top of that mask.

`runtime/agent` itself stays writable: the CLI writes its usage counter and the
transcript there.  The staging copies live under it, so they are visible to the
host after the process exits and go through `factory.runtime.staging` before a
byte reaches the product.

Network stays available: a model call needs it, and this sandbox is about the
control plane, not exfiltration.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Iterable

from .base import RuntimeFailure

BACKEND = "bwrap"

# Paths inside a run that must never be mounted writable.  `runtime/agent` is
# the one exception under `runtime/`: the Hermes adapter writes its usage file
# there, and its bind is applied after the read-only one, so it wins.
FORBIDDEN_WRITABLE = ("runtime/evidence", "checks")
FORBIDDEN_FILES = ("state.json", "blackbox.jsonl", "blackbox.lock", "idea.md", "runtime/usage.jsonl")
# Re-mounted read-only on top of the writable run root.  `checks/` is here
# because the eval harness copies its product checks into the run: an executor
# that could rewrite them would be grading its own product.
CONTROL_PLANE_FILES = (
    "state.json",
    "blackbox.jsonl",
    "blackbox.lock",
    "idea.md",
    "AUDIT_TRAIL.md",
    "checkpoint.json",
    "runtime/usage.jsonl",
)
CONTROL_PLANE_DIRS = ("runtime/evidence", "checks")
# The directories that hold the verifier's reports.  A producer may write next
# to them, never into them: the report is the witness, and a witness the
# producer wrote is not a witness.
VERIFICATION_DIRS = ("artifacts/verification",)
# What the executor may not keep from the operator's environment.  A
# credential is a credential whatever the provider calls it, so the provider's
# own key (`*_API_KEY`) is no longer an exception: the sandboxed model call is
# meant to go through the credential proxy on 127.0.0.1, and until that exists
# an inherited key is a hole, not a feature.
ENV_DENIED = frozenset(
    {
        "NETRC",
        "SSH_AUTH_SOCK",
        "GPG_AGENT_INFO",
        "KUBECONFIG",
        "GOOGLE_APPLICATION_CREDENTIALS",
    }
)
ENV_DENIED_PREFIXES = (
    "SSH_",
    "GPG_",
    "GNUPG_",
    "AWS_",
    "AZURE_",
    "GOOGLE_",
    "GCP_",
    "DOCKER_",
    "NPM_",
    "PYPI_",
    "STRIPE_",
    "SENTRY_",
    "TWILIO_",
    "CLOUDFLARE_",
)
ENV_DENIED_SUFFIXES = (
    "_TOKEN",
    "_PASSWORD",
    "_SECRET",
    "_CREDENTIALS",
    "_API_KEY",
    "_ACCESS_KEY",
    "_SECRET_KEY",
)
# The operator's secret home paths.  In the default mode the whole
# filesystem is mounted read-only, and read-only is not private: the
# executor runs as the operator and can read everything the operator can.
# Directories are replaced by an empty tmpfs; credential *files* by
# `/dev/null`, which also swallows a write.  `~/.hermes` is the adapter's own
# home, so the three runtime directories it writes are bound back over the
# mask (see `HERMES_HOME_PASSTHROUGH`); `config.yaml`, `auth.json` and `.env`
# stay invisible.
SECRET_HOME_DIRS = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".kube",
    ".docker",
    ".config/gh",
    ".hermes",
    ".claude",
    ".cursor",
    ".codex",
    ".openai",
    ".anthropic",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    ".local/share/keyrings",
    ".config/keyrings",
)
HERMES_HOME_DIR = ".hermes"
HERMES_HOME_PASSTHROUGH = ("cache", "logs", "installs")
# The Hermes CLI is installed into its own home by default: the checkout in
# `hermes-agent/` and the interpreters it manages in `tools/`.  Masking those
# with the rest of `~/.hermes` does not protect a secret — it makes
# `hermes` itself (and every Python the executor runs) unresolvable.  They are
# bound back read-only: code, not operator credentials.
HERMES_HOME_READ_ONLY = ("hermes-agent", "tools")

# The `factory/` package itself; binding its repository root would let an
# executor rewrite the code that judges it.
PACKAGE_DIR = Path(__file__).resolve().parents[1]


def sandbox_available(backend: str = BACKEND) -> bool:
    if backend != "bwrap":
        return False
    return shutil.which("bwrap") is not None


def sandbox_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for a sandboxed executor.

    The executor inherits no control variables and no credentials.  The
    agent needs none of them: it is a model client, and the model call goes
    through the credential proxy (PR-C2), not through a key copied into an
    untrusted process.  Until the proxy exists, a live sandboxed run has no
    provider key at all — that is a deliberate hole in functionality, not a
    hole in the boundary.

    Everything that belongs to the *operator* rather than to the model call is
    dropped: the executor is untrusted code, and a token that leaks into it
    leaks out.  `FACTORY_*` would also make an accidental `factory ...` call
    from inside the sandbox point at the factory root, and `PYTHONPATH` would
    shadow a module.
    """
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key.startswith("FACTORY_") or key == "PYTHONPATH":
            env.pop(key, None)
            continue
        if key in ENV_DENIED or key.startswith(ENV_DENIED_PREFIXES):
            env.pop(key, None)
            continue
        if key.endswith(ENV_DENIED_SUFFIXES):
            env.pop(key, None)
    return env


def home_mask_args(home: Path | None = None) -> list[str]:
    """bwrap options that make the operator's secrets invisible inside.

    A directory is replaced by an empty tmpfs, a credential file by
    `/dev/null` (reads as empty, writes are discarded).  `~/.hermes` carries
    both the operator's keys (`config.yaml`, `auth.json`, `.env`) and the
    adapter's own runtime state, so the mask goes on first and then the three
    directories the adapter writes are bound back from the host.  A symlink in
    one of those three is refused: it would carry its target through the mask.
    """
    home = Path.home() if home is None else home
    args: list[str] = []
    for relative in SECRET_HOME_DIRS:
        candidate = home / relative
        if candidate.is_dir():
            args.extend(["--tmpfs", str(candidate)])
        elif candidate.is_file():
            args.extend(["--ro-bind", "/dev/null", str(candidate)])

    hermes = home / HERMES_HOME_DIR
    if not hermes.is_dir():
        return args
    for name in HERMES_HOME_PASSTHROUGH:
        shared = hermes / name
        if shared.is_symlink():
            raise RuntimeFailure(
                f"refusing to pass a symlinked Hermes adapter directory through: {shared}"
            )
        if not shared.is_dir():
            try:
                shared.mkdir(mode=0o700, parents=True)
            except OSError as exc:
                raise RuntimeFailure(
                    f"cannot create the Hermes adapter directory {shared}: {exc}"
                ) from exc
        args.extend(["--bind", str(shared), str(shared)])
    for name in HERMES_HOME_READ_ONLY:
        install = hermes / name
        if install.is_dir():
            args.extend(["--ro-bind", str(install.resolve()), str(install)])
    return args


def _forbidden(run: Path, path: Path) -> bool:
    resolved_run = run.resolve()
    resolved_path = path.resolve()
    if resolved_path == resolved_run or resolved_path in resolved_run.parents:
        return True
    package = PACKAGE_DIR.resolve()
    if resolved_path == package or resolved_path in package.parents or package in resolved_path.parents:
        return True
    for name in (*FORBIDDEN_FILES, *CONTROL_PLANE_FILES):
        target = (resolved_run / name).resolve()
        if resolved_path == target or target in resolved_path.parents:
            return True
    for relative in (*FORBIDDEN_WRITABLE, *CONTROL_PLANE_DIRS):
        protected = (resolved_run / relative).resolve()
        if resolved_path == protected or protected in resolved_path.parents or resolved_path in protected.parents:
            return True
    return False


def _secret_home_collision(home: Path, path: Path) -> str | None:
    """The masked secret path that a writable mount would expose, if any.

    Mounting a secret itself makes it writable again; mounting its ancestor
    (`$HOME`, `~/.config`) re-exposes it under the mask; mounting its
    descendant walks into it.  The Hermes adapter directories are the one
    exception: they are passed back through on purpose, so a path inside them
    is allowed.
    """
    hermes = (home / HERMES_HOME_DIR).resolve()
    allowed = tuple((hermes / name).resolve() for name in HERMES_HOME_PASSTHROUGH)
    for relative in SECRET_HOME_DIRS:
        secret = (home / relative).resolve()
        inside = path == secret or secret in path.parents
        above = path in secret.parents
        if not (inside or above):
            continue
        if inside and any(path == item or item in path.parents for item in allowed):
            continue
        return str(secret)
    return None


def verification_dirs(run: Path) -> list[Path]:
    """The report directories of this run: the stage reports and the task ones.

    They are protected by mounting them read-only on top of the writable run
    root — but only for an executor that was not given a path inside them.
    The verifier is given exactly that path, and must be able to write.
    """
    found: list[Path] = []
    for relative in VERIFICATION_DIRS:
        candidate = run / relative
        if candidate.is_dir():
            found.append(candidate.resolve())
    tasks = run / "tasks"
    if tasks.is_dir():
        for task in sorted(tasks.iterdir()):
            candidate = task / "verification"
            if candidate.is_dir():
                found.append(candidate.resolve())
    return found


def sandbox_command(
    command: list[str],
    run_dir: Path,
    *,
    read_write: tuple[str, ...] = (),
    staged: Iterable[tuple[str, str]] = (),
    enabled: bool = True,
    backend: str = BACKEND,
    executor_user: str | None = None,
    run_id: str = "run",
    seq: int | str = 0,
    network: bool = True,
    credential_proxy: Any | None = None,
) -> list[str]:
    """Команда в песочнице; при `executor_user` — ещё и под чужим uid.

    В обычном режиме корень `/` монтируется read-only.  В режиме отдельного
    пользователя профиль сужен до явных привязок (системные каталоги ro,
    каталог прогона rw): `/home` внутри пространства не существует, поэтому
    мирочитаемые файлы домашней директории недоступны даже при traverse-гранте
    на пути к прогону (см. `runtime/isolation.py`).

    `network=False` additionally unshares the network namespace.  The executor
    sandbox keeps the network because a model call needs it; the host replay of
    the verifier's checks does not, and a test that tries to reach a provider
    fails there instead of leaking a key to it.

    `credential_proxy` (PR-C2) is a started `factory.credential_proxy.CredentialProxy`.
    Its directory is bound read-only at `CREDENTIAL_PROXY_MOUNT`, its generated
    Hermes config replaces `~/.hermes/config.yaml`, and the command is wrapped
    by the bridge.  The caller must pass `network=False`: the bridge is then the
    only address the executor can reach, and the provider key stays on the host.
    The separate-user mode is refused until the socket ACL and env-file wiring
    are designed; failing closed beats a live run that silently has no key.
    """
    if not enabled:
        if executor_user:
            from . import isolation

            return isolation.executor_command(
                list(command), user=executor_user, run_id=run_id, seq=seq
            )
        return list(command)
    if backend != "bwrap":
        raise RuntimeFailure(
            f"unknown sandbox backend {backend!r}; only 'bwrap' is supported "
            "(systemd user scopes cannot apply ProtectHome/ProtectSystem)"
        )
    if not sandbox_available(backend):
        raise RuntimeFailure("sandbox requested but bubblewrap (bwrap) is not installed")
    if credential_proxy is not None:
        if executor_user:
            raise RuntimeFailure(
                "credential proxy is not wired for --executor-user yet: the Unix "
                "socket and env file need an executable-user ACL design. Run the "
                "live executor without --executor-user."
            )
        if network:
            raise RuntimeFailure(
                "credential proxy requires network=False: otherwise the executor "
                "can bypass the bridge and reach a provider directly"
            )
        if (
            getattr(credential_proxy, "directory", None) is None
            or getattr(credential_proxy, "config_path", None) is None
        ):
            raise RuntimeFailure("credential proxy has not been started")

    run = run_dir.resolve()
    home = Path.home()
    args = [
        "bwrap",
        "--die-with-parent",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--new-session",
        "--cap-drop",
        "ALL",
    ]
    if not network:
        args.append("--unshare-net")
    if executor_user:
        for system_dir in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt"):
            if os.path.isdir(system_dir):
                args.extend(["--ro-bind", system_dir, system_dir])
    else:
        args.extend(["--ro-bind", "/", "/"])
        # Read-only is not private: the executor runs as the operator.  The
        # operator's secret paths are masked, and the mask has to come before
        # `--ro-bind run run` below: with TMPDIR under `~/.hermes/cache`, the
        # run root itself lies inside a pass-through directory, and mounting
        # that directory writable later would unmask `state.json`.
        args.extend(home_mask_args(home))
        runtime_dir = Path(os.environ["XDG_RUNTIME_DIR"]) if os.environ.get(
            "XDG_RUNTIME_DIR"
        ) else None
        if runtime_dir is not None and runtime_dir.is_dir():
            for name in ("bus", "ssh-agent.socket"):
                socket = runtime_dir / name
                if socket.is_socket():
                    args.extend(["--ro-bind", "/dev/null", str(socket)])
            keyring = runtime_dir / "keyring"
            if keyring.is_dir():
                args.extend(["--tmpfs", str(keyring)])
        # Mask system D-Bus and systemd sockets — they are not covered by
        # XDG_RUNTIME_DIR and can leak session information.
        for system_socket in ("/run/dbus/system_bus_socket", "/run/systemd/private", "/run/systemd/journal/socket"):
            sock_path = Path(system_socket)
            if sock_path.is_socket():
                args.extend(["--ro-bind", "/dev/null", str(sock_path)])
    args.extend(
        [
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
        ]
    )
    # The run root is a read-only mount.  The previous scheme made the whole
    # directory writable so an editor could rename a temporary file over its
    # target; that also opened every sibling path (`.git`, `backups/`,
    # `constraints.md`) to the executor.  A declared path is now replaced by a
    # staging copy instead, so a rename stays inside the directory it was
    # given and the run root keeps its mode.
    args.extend(["--ro-bind", str(run), str(run)])
    if credential_proxy is not None:
        from ..credential_proxy import BRIDGE_MOUNT

        # The bridge script and the host proxy's Unix socket travel together;
        # the directory is read-only, so the executor cannot replace either.
        args.extend(
            ["--ro-bind", str(credential_proxy.directory), BRIDGE_MOUNT]
        )
        hermes_dir = home / HERMES_HOME_DIR
        if not hermes_dir.is_dir():
            raise RuntimeFailure(
                f"credential proxy needs the operator Hermes home at {hermes_dir}"
            )
        args.extend(
            [
                "--ro-bind",
                str(credential_proxy.config_path),
                str(hermes_dir / "config.yaml"),
            ]
        )

    staged_map: dict[Path, Path] = {}
    for destination, source in staged:
        dest_raw = Path(destination).expanduser()
        source_raw = Path(source).expanduser()
        if dest_raw.is_symlink() or not dest_raw.is_dir():
            raise RuntimeFailure(f"staged destination is not a real directory: {dest_raw}")
        if source_raw.is_symlink() or not source_raw.is_dir():
            raise RuntimeFailure(f"staged source is not a real directory: {source_raw}")
        dest = dest_raw.resolve()
        source_resolved = source_raw.resolve()
        if dest == run or run not in dest.parents:
            raise RuntimeFailure(f"staged destination escapes the run: {dest}")
        if _forbidden(run, dest):
            raise RuntimeFailure(f"refusing to stage the factory control plane: {dest}")
        if not (source_resolved == run or run in source_resolved.parents):
            raise RuntimeFailure(f"staged source escapes the run: {source_resolved}")
        previous = staged_map.get(dest)
        if previous is not None and previous != source_resolved:
            raise RuntimeFailure(f"two staging sources for one destination: {dest}")
        staged_map[dest] = source_resolved

    staged_dirs: list[Path] = []
    for dest in sorted(staged_map, key=lambda item: len(item.parts)):
        # An outer staging bind already carries its nested directories; a
        # second bind over a child would only add a mount with no new bytes.
        if any(parent == dest or parent in dest.parents for parent in staged_dirs):
            continue
        staged_dirs.append(dest)
        args.extend(["--bind", str(staged_map[dest]), str(dest)])

    for name in CONTROL_PLANE_FILES:
        protected_file = run / name
        if protected_file.is_file():
            args.extend(["--ro-bind", str(protected_file), str(protected_file)])
    for name in CONTROL_PLANE_DIRS:
        protected_dir = run / name
        if protected_dir.is_dir():
            args.extend(["--ro-bind", str(protected_dir), str(protected_dir)])
    args.extend(["--chdir", str(run)])
    seen: set[str] = set()
    bound: list[Path] = []
    bound_dirs: list[Path] = []
    for item in read_write:
        path = Path(item).expanduser()
        if path.is_symlink():
            raise RuntimeFailure(f"refusing to expose a symlink to the executor: {path}")
        if not path.exists():
            raise RuntimeFailure(f"sandbox read-write path does not exist: {path}")
        path = path.resolve()
        if _forbidden(run, path):
            raise RuntimeFailure(f"refusing to mount the factory control plane writable: {path}")
        exposed = _secret_home_collision(home, path)
        if exposed is not None:
            raise RuntimeFailure(
                f"refusing to mount {path} writable: it would expose the masked secret path {exposed}"
            )
        for staged_dest in staged_map:
            if path == staged_dest or path in staged_dest.parents:
                raise RuntimeFailure(
                    f"refusing to mount {path} writable over the staged path {staged_dest}"
                )
        if str(path) in seen:
            continue
        if path.is_file() and any(parent in bound_dirs for parent in path.parents):
            # A file bound on itself becomes a mount point, and an atomic
            # writer that creates `<target>.tmp` and renames it over the target
            # gets EBUSY from rename(2).  The parent directory is already
            # writable, so the file needs no separate bind.
            continue
        seen.add(str(path))
        bound.append(path)
        args.extend(["--bind", str(path), str(path)])
        if path.is_dir():
            bound_dirs.append(path)
    # The verifier's reports are mounted read-only on top of the writable run
    # root: later mounts win.  An executor that was given a path inside one of
    # them is the verifier itself and keeps its write access.
    protected_binds = [*bound, *staged_dirs]
    for protected in verification_dirs(run):
        if any(protected == path or protected in path.parents for path in protected_binds):
            continue
        args.extend(["--ro-bind", str(protected), str(protected)])
    args.append("--")
    if credential_proxy is not None:
        args.extend(credential_proxy.wrapper(command))
    else:
        args.extend(command)
    if executor_user:
        from . import isolation

        return isolation.executor_command(args, user=executor_user, run_id=run_id, seq=seq)
    return args
