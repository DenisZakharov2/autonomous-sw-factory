"""Minimal todo CLI: stdlib only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("tasks.json")


def load(path: Path = DEFAULT_PATH) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def save(tasks: list[dict], path: Path = DEFAULT_PATH) -> None:
    path.write_text(json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8")


def add(text: str, path: Path = DEFAULT_PATH) -> dict:
    if not text.strip():
        raise ValueError("text must not be empty")
    tasks = load(path)
    task = {"id": len(tasks) + 1, "text": text.strip(), "done": False}
    tasks.append(task)
    save(tasks, path)
    return task


def mark_done(task_id: int, path: Path = DEFAULT_PATH) -> dict:
    tasks = load(path)
    for task in tasks:
        if task["id"] == task_id:
            task["done"] = True
            save(tasks, path)
            return task
    raise KeyError(f"no task {task_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="todo")
    sub = parser.add_subparsers(dest="command", required=True)
    p_add = sub.add_parser("add"); p_add.add_argument("text")
    sub.add_parser("list")
    p_done = sub.add_parser("done"); p_done.add_argument("id", type=int)
    args = parser.parse_args(argv)
    if args.command == "add":
        task = add(args.text); print(f"added {task['id']}")
    elif args.command == "list":
        for task in load():
            print(f"{task['id']} [{'x' if task['done'] else ' '}] {task['text']}")
    else:
        print(f"done {mark_done(args.id)['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
