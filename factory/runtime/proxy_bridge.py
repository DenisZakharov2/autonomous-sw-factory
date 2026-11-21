"""TCP-to-Unix bridge that lives inside the executor's network namespace.

`bwrap --unshare-net` gives the executor a loopback with no route to the host,
so a TCP listener on the host is unreachable by design.  This process is the
one exception: it listens on the sandbox loopback and forwards bytes over an
AF_UNIX socket, which `bwrap` can bind into the namespace as a file.  It holds
no credential and parses no HTTP -- the credential proxy on the far side of
the socket does that.

If the bridge dies, the child dies with it: the child is started with
PR_SET_PDEATHSIG, so killing the sandboxed process (or the whole sandbox on a
step timeout) cannot leave the executor talking to a dead listener.
"""
from __future__ import annotations

import argparse
import ctypes
import signal
import socket
import subprocess
import sys
import threading

CHUNK = 65536
BACKLOG = 64


def _death_signal() -> None:
    """Ask the kernel to SIGKILL the child when this process dies."""
    if sys.platform != "linux":
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


def _pump(source: socket.socket, destination: socket.socket) -> None:
    try:
        while True:
            data = source.recv(CHUNK)
            if not data:
                break
            destination.sendall(data)
    except OSError:
        pass
    finally:
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _serve_connection(client: socket.socket, target: str) -> None:
    upstream = socket.socket(socket.AF_UNIX)
    try:
        upstream.connect(target)
    except OSError:
        client.close()
        return
    reverse = threading.Thread(target=_pump, args=(client, upstream), daemon=True)
    reverse.start()
    _pump(upstream, client)
    reverse.join(timeout=1)
    for item in (client, upstream):
        try:
            item.close()
        except OSError:
            pass


def _serve(server: socket.socket, target: str) -> None:
    while True:
        try:
            client, _ = server.accept()
        except OSError:
            return
        threading.Thread(target=_serve_connection, args=(client, target), daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--unix", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("missing command after --")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", args.port))
    server.listen(BACKLOG)

    child = subprocess.Popen(command, preexec_fn=_death_signal)
    threading.Thread(target=_serve, args=(server, args.unix), daemon=True).start()
    try:
        return child.wait()
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
