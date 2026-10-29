#!/usr/bin/env python3
"""Build the committed `examples/nano-todo` reference run.

This is a deterministic reference run: the artifacts are written by this
script, not by an LLM. It proves the engine, schemas, gates, task flow and
journal; it does not claim to prove model autonomy.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from factory.engine import (
    gate_approve,
    init_run,
    submit_work,
    task_accept,
    task_claim,
    task_submit,
)
from factory.metrics import write_metrics
from factory.verify import verify_run

EXAMPLE = ROOT / "examples" / "nano-todo"
RUNS = ROOT / "examples"
RUN_ID = "nano-todo"


def write(relative: str, text: str) -> Path:
    path = EXAMPLE / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def doc(title: str, sections: list[tuple[str, str]]) -> str:
    lines = [f"# {title}", ""]
    for heading, body in sections:
        lines += [heading, body.strip(), ""]
    return "\n".join(lines)


def stage_artifacts() -> None:
    write(
        "artifacts/interview-brief.md",
        doc("Установочный бриф проекта", [
            ("## 1. Цель (JTBD)", "Разработчик хочет вести список задач в терминале без внешних зависимостей и без веб-интерфейса."),
            ("## 2. Out-of-Scope", "- Веб-интерфейс\n- Синхронизация между устройствами\n- Пользовательские аккаунты"),
            ("## 3. Требования", "| Код | Описание | Приоритет |\n| :--- | :--- | :--- |\n| `REQ-001` | Добавить задачу | `Must-Have` |\n| `REQ-002` | Показать список задач | `Must-Have` |\n| `REQ-003` | Отметить задачу выполненной | `Must-Have` |"),
            ("## 4. Профиль", "- `customer_profile`: `DEVELOPER`\n- Язык документации: русский\n- Ограничение: только стандартная библиотека Python"),
        ]),
    )
    write(
        "artifacts/GLOSSARY.md",
        doc("Словарь терминов продукта", [
            ("## Термины", "| Термин | Определение | Идентификатор в коде |\n| :--- | :--- | :--- |\n| `Задача` | Элемент списка с текстом и статусом | `Task` |\n| `Список` | Набор задач в JSON-файле | `TodoStore` |"),
        ]),
    )
    write(
        "artifacts/vision.md",
        doc("Продуктовое видение", [
            ("## 1. Ценность", "Минимальный CLI- todo для одного разработчика: одна команда, один JSON-файл, ноль зависимостей."),
            ("## 2. Out-of-Scope", "- Веб-интерфейс\n- Синхронизация\n- Напоминания"),
            ("## 3. Метрики", "- `tasks_added`: 1 за запуск\n- `time_to_first_task`: < 10 секунд"),
        ]),
    )
    write(
        "artifacts/backlog.md",
        doc("Приоритизированный бэклог", [
            ("## 1. Must-Have", "| ID | Ценность | REQ | Приоритет |\n| :--- | :--- | :--- | :--- |\n| `B-001` | Добавление | `REQ-001` | `Must-Have` |\n| `B-002` | Просмотр | `REQ-002` | `Must-Have` |\n| `B-003` | Завершение | `REQ-003` | `Must-Have` |"),
            ("## 2. Out-of-Scope", "| ID | Причина |\n| :--- | :--- |\n| `B-900` | Веб-интерфейс не подтверждён JTBD |"),
        ]),
    )
    write(
        "artifacts/competitors.md",
        doc("Обзор альтернатив", [
            ("## 1. Альтернативы", "| Решение | Что делает | Слабое место |\n| :--- | :--- | :--- |\n| `todo.txt` | Простой список | Нет статусов |\n| `taskwarrior` | Полный трекер | Сложен для одной задачи |"),
            ("## 2. Вывод", "Наш вариант выигрывает минимализмом: три команды и один файл."),
        ]),
    )
    write(
        "artifacts/metrics.md",
        doc("Метрики продукта", [
            ("## 1. Целевые метрики", "| Метрика | Формула | Цель | Горизонт |\n| :--- | :--- | :--- | :--- |\n| `time_to_first_task` | время до первой задачи | < 10 с | MVP |"),
            ("## 2. Способ измерения", "- Источник: замер вручную\n- Частота: каждый релиз\n- Владелец: product owner"),
        ]),
    )
    write(
        "artifacts/spec.md",
        doc("Инженерная спецификация", [
            ("## 1. Требования", "| ID | Требование | Приоритет | Источник |\n| :--- | :--- | :--- | :--- |\n| `REQ-001` | `todo add <text>` добавляет задачу | `Must-Have` | интервью |\n| `REQ-002` | `todo list` печатает задачи | `Must-Have` | интервью |\n| `REQ-003` | `todo done <id>` меняет статус | `Must-Have` | интервью |"),
            ("## 2. Приёмка", "| ID | Given | When | Then |\n| :--- | :--- | :--- | :--- |\n| `REQ-001` | пустой файл | `add \"купить хлеб\"` | задача с id 1 |\n| `REQ-002` | одна задача | `list` | строка с задачей |\n| `REQ-003` | задача id 1 | `done 1` | статус done |"),
        ]),
    )
    write(
        "artifacts/spec-human.md",
        doc("Понятное ТЗ для человека", [
            ("## 1. Что делает система", "`REQ-001` — добавляет задачу. `REQ-002` — показывает список. `REQ-003` — отмечает задачу выполненной."),
            ("## 2. Как проверяем", "| ID | Что проверяем | Как проверяем |\n| :--- | :--- | :--- |\n| `REQ-001` | добавление | запустить `add` и `list` |\n| `REQ-002` | список | запустить `list` |\n| `REQ-003` | завершение | запустить `done 1` и `list` |"),
        ]),
    )
    write(
        "artifacts/traceability.json",
        json.dumps({
            "schema_version": "1.0.0",
            "requirements": {
                "REQ-001": {"spec": "artifacts/spec.md#REQ-001", "tasks": ["TASK-001"], "tests": ["product/tests/test_todo.py::test_add"], "acceptance": "A-1"},
                "REQ-002": {"spec": "artifacts/spec.md#REQ-002", "tasks": ["TASK-001"], "tests": ["product/tests/test_todo.py::test_list"], "acceptance": "A-2"},
                "REQ-003": {"spec": "artifacts/spec.md#REQ-003", "tasks": ["TASK-002"], "tests": ["product/tests/test_todo.py::test_done"], "acceptance": "A-3"},
            },
        }, indent=2),
    )
    write(
        "artifacts/domain-model.md",
        doc("Доменная модель", [
            ("## 1. Сущности", "| Сущность | Атрибуты | Идентификатор |\n| :--- | :--- | :--- |\n| `Task` | id, text, done | `id` |"),
            ("## 2. Состояния", "| Сущность | Состояние | Переход | Условие |\n| :--- | :--- | :--- | :--- |\n| `Task` | `open` | `done` | команда done |"),
            ("## 3. Инварианты", "- id уникален и растёт\n- текст не пустой"),
        ]),
    )
    write(
        "artifacts/access-matrix.md",
        doc("Матрица доступа", [
            ("## 1. Роли", "- `owner`: единственный пользователь CLI\n- `process`: процесс с доступом к файлу"),
            ("## 2. Матрица", "| Роль | Сущность | Операция | Разрешено |\n| :--- | :--- | :--- | :--- |\n| `owner` | `Task` | add/list/done | да |\n| `process` | `Task` | read/write | да |"),
        ]),
    )
    write(
        "artifacts/security-review-skills.md",
        doc("Аудит внешних скиллов", [
            ("## 1. Сканирование", "- Сканер: `skillspector 2.12.0`\n- Команда: `skillspector scan --format json --fail-on-incomplete --min-coverage 95`\n- Путь: `skills/`\n- Покрытие: `100%`"),
            ("## 2. Вердикты", "| Скилл | Recommendation | Severity | is_complete | Решение |\n| :--- | :--- | :--- | :--- | :--- |\n| `none` | `SAFE` | `LOW` | `true` | `not installed` |"),
            ("## 3. Лок", "- Файл: `skills-lock.json`\n- Записей: `0`\n- Все записи содержат `commit` и `sha256`: `true`"),
        ]),
    )
    write(
        "skills-lock.json",
        json.dumps({"schema_version": "1.0.0", "skills": []}, indent=2),
    )
    write(
        "artifacts/architecture.md",
        doc("Архитектура", [
            ("## 1. Компоненты", "| Компонент | Ответственность | Технология |\n| :--- | :--- | :--- |\n| `TodoStore` | чтение/запись JSON | stdlib |\n| `CLI` | разбор команд | argparse |"),
            ("## 2. REQ mapping", "| REQ | Компонент | Тест |\n| :--- | :--- | :--- |\n| `REQ-001` | `TodoStore` | `test_add` |\n| `REQ-002` | `CLI` | `test_list` |\n| `REQ-003` | `TodoStore` | `test_done` |"),
            ("## 3. Решения", "- `ADR-001`: JSON-файл вместо БД\n- Отвергнуто: SQLite (лишняя зависимость)"),
        ]),
    )
    write(
        "artifacts/api-contract.md",
        doc("Замороженный API-контракт [FROZEN]", [
            ("## 1. Эндпоинты", "| Метод | Путь | Вход | Выход | Ошибки |\n| :--- | :--- | :--- | :--- | :--- |\n| CLI | `add` | text | id | пустой текст |\n| CLI | `list` | - | строки | нет файла |\n| CLI | `done` | id | статус | нет id |"),
            ("## 2. Модели", "| Модель | Поля | Ограничения |\n| :--- | :--- | :--- |\n| `Task` | id:int, text:str, done:bool | text != '' |"),
            ("## 3. Ошибки", "| Код | HTTP | Когда | Тело |\n| :--- | :--- | :--- | :--- |\n| `E_EMPTY` | - | пустой текст | сообщение |"),
        ]),
    )
    write(
        "artifacts/data-model.md",
        doc("Модель данных", [
            ("## 1. Таблицы", "| Таблица | Поля | Ключи |\n| :--- | :--- | :--- |\n| `tasks.json` | id, text, done | id |"),
            ("## 2. Индексы", "| Таблица | Индекс | Поля | Причина |\n| :--- | :--- | :--- | :--- |\n| `tasks.json` | нет | - | файл мал |"),
            ("## 3. Миграции", "| Миграция | Up | Down | Обратима |\n| :--- | :--- | :--- | :--- |\n| `none` | - | - | да |"),
        ]),
    )
    write(
        "artifacts/threat-model.md",
        doc("Модель угроз", [
            ("## 1. Активы", "- Файл задач\n- Локальные данные пользователя"),
            ("## 2. Векторы", "| Вектор | Сценарий | Вероятность | Ущерб |\n| :--- | :--- | :--- | :--- |\n| path traversal | путь к файлу | низкая | средний |"),
            ("## 3. Контрмеры", "| Вектор | Контрмера | Проверка |\n| :--- | :--- | :--- |\n| path traversal | фиксированный путь | тест |"),
        ]),
    )
    write(
        "artifacts/adr/ADR-001.md",
        doc("ADR-001: JSON-файл вместо БД", [
            ("## Контекст", "Нужен минимальный todo CLI без зависимостей. Связанные `REQ`: `REQ-001`, `REQ-002`, `REQ-003`."),
            ("## Решение", "Хранить задачи в одном JSON-файле рядом с приложением. Обоснование по принципу Ponytail: меньше кода и нет сервера."),
            ("## Последствия", "- Положительные: ноль зависимостей\n- Отрицательные: нет конкурентного доступа\n- Отвергнуто: SQLite"),
        ]),
    )
    write(
        "artifacts/ux-flows.md",
        doc("Пользовательские потоки", [
            ("## 1. Экраны", "| Экран | Назначение | REQ |\n| :--- | :--- | :--- |\n| `CLI` | все команды | `REQ-001` |"),
            ("## 2. Потоки", "`add`: `todo add text -> list`\n`done`: `todo list -> todo done 1 -> todo list`"),
            ("## 3. Состояния", "| Экран | Empty | Loading | Populated | Error | Partial/Offline |\n| :--- | :--- | :--- | :--- | :--- | :--- |\n| `CLI` | пустой список | - | список | сообщение | - |"),
        ]),
    )
    write(
        "artifacts/screens/cli.md",
        doc("Спецификация экрана CLI", [
            ("## Назначение", "Терминальный интерфейс для трёх команд."),
            ("## Состояния", "- Empty: `No tasks`\n- Loading: не применимо\n- Populated: строки задач\n- Error: сообщение об ошибке\n- Partial/Offline: не применимо"),
            ("## Доступность", "- Клавиатура: да\n- Контраст: терминал\n- Фокус: не применимо"),
        ]),
    )
    write(
        "artifacts/ai-design.md",
        doc("ИИ-дизайн", [
            ("## 1. Промпты", "| Шаблон | Назначение | Вход | Выход |\n| :--- | :--- | :--- | :--- |\n| `none` | ИИ не используется | - | - |"),
            ("## 2. Валидация", "- Схема ответа: не применимо\n- Таймаут: не применимо\n- Ретраи: не применимо"),
            ("## 3. Fallback", "`not applicable` — продукт детерминированный."),
            ("## 4. Eval", "- Датасет: не применимо\n- Кейсов: 0\n- Adversarial injection: 0\n- Порог: -"),
        ]),
    )
    write(
        "artifacts/plan.md",
        doc("План декомпозиции задач", [
            ("## 1. Задачи", "| `task_id` | Роль | `REQ` | `depends_on` | `Allowed Files` | Команда проверки |\n| :--- | :--- | :--- | :--- | :--- | :--- |\n| `TASK-001` | `12-fullstack-developer` | `REQ-001`, `REQ-002` | `-` | `product/src/todo.py`, `product/tests/test_todo.py` | `python3 -m unittest discover -s product/tests` |\n| `TASK-002` | `12-fullstack-developer` | `REQ-003` | `TASK-001` | `product/src/todo.py`, `product/tests/test_todo.py` | `python3 -m unittest discover -s product/tests` |"),
            ("## 2. Покрытие REQ", "| REQ | Задачи |\n| :--- | :--- |\n| `REQ-001` | `TASK-001` |\n| `REQ-002` | `TASK-001` |\n| `REQ-003` | `TASK-002` |\n\nВсе `REQ` покрыты: `true`."),
        ]),
    )
    write(
        "artifacts/technical-debt.md",
        doc("Технический долг", [
            ("## 1. Долг", "| ID | Описание | Причина | Влияние |\n| :--- | :--- | :--- | :--- |\n| `TD-001` | нет блокировки файла | минимализм | низкое |"),
            ("## 2. План", "| ID | Когда закрываем | Владелец |\n| :--- | :--- | :--- |\n| `TD-001` | при многопользовательском режиме | 12 |"),
        ]),
    )
    write(
        "artifacts/test-report.md",
        doc("Отчёт тестирования", [
            ("## 1. Прогоны", "| Прогон | Команда | Код | Длительность |\n| :--- | :--- | :--- | :--- |\n| 1 | `python3 -m unittest discover -s product/tests` | `0` | `<1s` |\n| 2 | то же | `0` | `<1s` |\n| 3 | то же | `0` | `<1s` |"),
            ("## 2. Покрытие REQ", "| REQ | Тест | Результат |\n| :--- | :--- | :--- |\n| `REQ-001` | `test_add` | `PASS` |\n| `REQ-002` | `test_list` | `PASS` |\n| `REQ-003` | `test_done` | `PASS` |"),
            ("## 3. Flaky", "- Три прогона подряд: `PASS`\n- Нестабильные тесты: `0`\n- Пропущенные тесты: `0`"),
        ]),
    )
    write(
        "artifacts/acceptance.md",
        doc("Матрица приёмки", [
            ("## 1. Матрица приёмки", "| REQ | Критерий | Доказательство | Статус |\n| :--- | :--- | :--- | :--- |\n| `REQ-001` | add | `test_add` | `PASS` |\n| `REQ-002` | list | `test_list` | `PASS` |\n| `REQ-003` | done | `test_done` | `PASS` |"),
            ("## 2. Дефекты", "| ID | Severity | Описание | Статус |\n| :--- | :--- | :--- | :--- |\n| `BUG-001` | `Low` | нет цветного вывода | `Open` |"),
        ]),
    )
    write(
        "artifacts/security-review-app.md",
        doc("Аудит безопасности приложения", [
            ("## 1. Секреты", "- Сканирование: `grep` по репозиторию\n- Найдено: `0`"),
            ("## 2. Инъекции", "- SQL/NoSQL: не применимо\n- Prompt injection: не применимо\n- Path traversal: путь фиксирован"),
            ("## 3. Доступ", "- Матрица `access-matrix.md` соблюдена: `true`\n- Проверки авторизации: не применимо"),
            ("## 4. Вердикт", "`PASS` — Critical/High: `0`; доказательства: `grep`, unit tests."),
        ]),
    )
    write(
        "artifacts/db-report.md",
        doc("Отчёт DBA", [
            ("## 1. Миграции", "| Миграция | up -> down -> up | Потеря данных |\n| :--- | :--- | :--- |\n| `none` | `PASS` | `0` |"),
            ("## 2. EXPLAIN", "```\nnot applicable: JSON file, no database\n```"),
            ("## 3. Вердикт", "`PASS` — обратимость: `true`; индексы: не применимо."),
        ]),
    )
    write(
        "artifacts/data-pipeline.md",
        doc("Поток данных", [
            ("## 1. Поток", "`tasks.json -> TodoStore -> stdout`"),
            ("## 2. Идемпотентность", "- Ключ идемпотентности: `id`\n- Двойной прогон: `PASS`\n- Дубли: `0`"),
            ("## 3. DLQ", "- Куда: не применимо\n- Причина: нет\n- Повтор: -"),
        ]),
    )
    write(
        "artifacts/runbook-deploy.md",
        doc("Регламент выкладки и проверки отката", [
            ("## 1. Деплой", "```bash\npython3 -m product.src.todo --help\n```\n- Healthcheck: `exit 0`"),
            ("## 2. Rollback drill", "```bash\ngit checkout -- product/\n```\n- Код возврата: `0`\n- Фактический вывод: `rollback ok`"),
            ("## 3. RTO", "- Время восстановления: `< 1 min`\n- Потеря данных: `0`"),
        ]),
    )
    write(
        "artifacts/runbook-ops.md",
        doc("Эксплуатационный регламент", [
            ("## 1. Healthcheck", "- Проба: `python3 -m product.src.todo --help`\n- Период: при релизе\n- Алерт: exit != 0"),
            ("## 2. Аварии", "| Сценарий | Диагностика | Восстановление |\n| :--- | :--- | :--- |\n| файл повреждён | `python3 -m json.tool tasks.json` | восстановить из бэкапа |"),
            ("## 3. Бэкапы", "- Расписание: перед релизом\n- Хранение: 7 дней\n- Проверка восстановления: `restore-drill`"),
        ]),
    )
    write(
        "artifacts/release-notes.md",
        doc("Заметки о релизе", [
            ("## 1. Изменения", "- Добавлен CLI todo\n- Добавлены три команды"),
            ("## 2. REQ", "- Реализованные требования: `REQ-001`, `REQ-002`, `REQ-003`\n- Известные ограничения: один пользователь"),
        ]),
    )
    write(
        "product/src/todo.py",
        '''"""Minimal todo CLI: stdlib only."""
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
''',
    )
    write(
        "product/tests/test_todo.py",
        '''import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from todo import add, load, mark_done


class TodoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "tasks.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_add(self):
        task = add("buy bread", self.path)
        self.assertEqual(task["id"], 1)
        self.assertFalse(task["done"])

    def test_list(self):
        add("one", self.path)
        self.assertEqual(len(load(self.path)), 1)

    def test_done(self):
        add("one", self.path)
        self.assertTrue(mark_done(1, self.path)["done"])


if __name__ == "__main__":
    unittest.main()
''',
    )
    write(
        "product/README.md",
        doc("Todo CLI", [
            ("## 1. Что это", "Минимальный список задач в терминале. Только стандартная библиотека Python."),
            ("## 2. Запуск", "```bash\npython3 product/src/todo.py add \"купить хлеб\"\npython3 product/src/todo.py list\n```\nТребования: Python 3.10+."),
            ("## 3. Проверка", "```bash\npython3 -m unittest discover -s product/tests\n```\nОжидаемый результат: `OK`."),
        ]),
    )
    write(
        "product/docs/user-guide.md",
        doc("Руководство пользователя", [
            ("## 1. Начало", "Откройте терминал в папке продукта."),
            ("## 2. Основные действия", "1. `add \"текст\"` — добавить задачу.\n2. `list` — показать задачи.\n3. `done 1` — отметить выполненной."),
            ("## 3. Ошибки", "| Симптом | Причина | Что делать |\n| :--- | :--- | :--- |\n| `no task 1` | неверный id | `list` и повторить |"),
        ]),
    )
    write(
        "artifacts/lessons-learned.md",
        doc("Уроки прогона", [
            ("## 1. Что сработало", "- Один JSON-файл и stdlib\n- Три команды покрывают JTBD"),
            ("## 2. Что улучшить", "- Добавить блокировку файла при параллельном запуске\n- Добавить `--json` вывод\n\nИзменения проходят через `artifacts/patches/` и человеческое ревью."),
        ]),
    )
    (EXAMPLE / "artifacts" / "patches").mkdir(parents=True, exist_ok=True)
    (EXAMPLE / "artifacts" / "patches" / "README.md").write_text(
        "# Patches\n\nS9 improvement patches are proposed here for human review.\n", encoding="utf-8"
    )


def submit_and_gate(stage: str, role: str, gate: str, approver: str, evidence: str) -> None:
    submit_work(RUN_ID, stage_id=stage, role=role, artifacts=[], root=RUNS)
    gate_approve(RUN_ID, gate_id=gate, approver=approver, evidence=[evidence], root=RUNS)


def main() -> int:
    os.environ["FACTORY_ROOT"] = str(EXAMPLE)
    os.environ["FACTORY_RUNS_ROOT"] = str(RUNS)
    if EXAMPLE.exists():
        shutil.rmtree(EXAMPLE)
    init_run(RUN_ID, "nano", root=RUNS)
    stage_artifacts()

    submit_and_gate("s00_intake", "01-smart-interviewer", "gate_s00", "human:example", "interview accepted")
    submit_and_gate("s0_vision", "01-smart-interviewer", "gate_s0", "human:example", "vision accepted")
    submit_and_gate("s1_spec", "01-smart-interviewer", "gate_s1", "human:example", "spec-human approved")
    submit_and_gate("s15_quarantine", "00-orchestrator", "gate_s15", "02-independent-verifier", "no external skills")
    submit_and_gate("s2_architecture", "12-fullstack-developer", "gate_s2", "02-independent-verifier", "architecture frozen")
    submit_and_gate("s3_plan", "01-smart-interviewer", "gate_s3", "02-independent-verifier", "plan imported")

    task_claim(RUN_ID, "TASK-001", "12-fullstack-developer", root=RUNS)
    write(
        "tasks/TASK-001/deliverable.md",
        doc("Отчёт о выполнении задачи", [
            ("## 1. Что сделано", "- Task ID: `TASK-001`\n- Роль: `12-fullstack-developer`\n- Изменённые файлы: `product/src/todo.py`, `product/tests/test_todo.py`"),
            ("## 2. REQ", "- Покрытые `REQ`: `REQ-001`, `REQ-002`\n- Не покрытые: нет"),
            ("## 3. Проверка", "```bash\npython3 -m unittest discover -s product/tests\n```\n- Код возврата: `0`\n- Тесты: `3 passed`"),
            ("## 4. Diff", "```diff\n+ add(), load(), save()\n+ test_add, test_list\n```\n\nСтатус: `[ТРЕБУЕТ ПРОВЕРКИ]` — `PASS` ставит только `02-independent-verifier`."),
        ]),
    )
    task_submit(RUN_ID, "TASK-001", "12-fullstack-developer", ["product/src/todo.py", "product/tests/test_todo.py"], root=RUNS)
    task_accept(RUN_ID, "TASK-001", root=RUNS)
    task_claim(RUN_ID, "TASK-002", "12-fullstack-developer", root=RUNS)
    write(
        "tasks/TASK-002/deliverable.md",
        doc("Отчёт о выполнении задачи", [
            ("## 1. Что сделано", "- Task ID: `TASK-002`\n- Роль: `12-fullstack-developer`\n- Изменённые файлы: `product/src/todo.py`, `product/tests/test_todo.py`"),
            ("## 2. REQ", "- Покрытые `REQ`: `REQ-003`\n- Не покрытые: нет"),
            ("## 3. Проверка", "```bash\npython3 -m unittest discover -s product/tests\n```\n- Код возврата: `0`\n- Тесты: `3 passed`"),
            ("## 4. Diff", "```diff\n+ mark_done()\n+ test_done\n```\n\nСтатус: `[ТРЕБУЕТ ПРОВЕРКИ]` — `PASS` ставит только `02-independent-verifier`."),
        ]),
    )
    task_submit(RUN_ID, "TASK-002", "12-fullstack-developer", ["product/src/todo.py", "product/tests/test_todo.py"], root=RUNS)
    task_accept(RUN_ID, "TASK-002", root=RUNS)
    submit_and_gate("s4_build", "12-fullstack-developer", "gate_s4", "02-independent-verifier", "all tasks accepted")

    submit_and_gate("s5_acceptance", "12-fullstack-developer", "gate_s5", "02-independent-verifier", "100% PASS")
    submit_and_gate("s6_security", "12-fullstack-developer", "gate_s6", "02-independent-verifier", "no Critical/High")
    submit_and_gate("s7_release", "12-fullstack-developer", "gate_s7", "human:example", "rollback drill passed")
    submit_and_gate("s8_docs", "12-fullstack-developer", "gate_s8", "02-independent-verifier", "commands verified")

    write_metrics(EXAMPLE)
    submit_and_gate("s9_learn", "00-orchestrator", "gate_s9", "human:example", "patch queued for human review")

    errors = verify_run(EXAMPLE, "s9_learn", require_gate=True)
    if errors:
        print("EXAMPLE VERIFY FAILED")
        for error in errors:
            print("-", error)
        return 1
    print(f"EXAMPLE OK: {EXAMPLE} events={len((EXAMPLE / 'blackbox.jsonl').read_text().splitlines())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
