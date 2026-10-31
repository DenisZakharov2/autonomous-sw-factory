"""Assemble the prompt an executor receives for one work order.

The prompt is deliberately boring: role card, stage, the exact artifacts that
are still missing, the templates, what is already accepted, the paths that may
be written, and the rules.  No journal dump: a model that receives the whole
history starts arguing with it instead of working.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import validate_stage
from ..config import PACKAGE_ROOT, load_pipeline
from ..pipeline import effective_roles
from ..engine import _plan_rows  # one parser, one truth about plan.md
from ..state import find_stage, load_state

ROLE_CARD_LIMIT = 4500
TEMPLATE_LIMIT = 2200
ACCEPTED_LIMIT = 12
SUMMARY_LIMIT = 160
CONSTRAINTS_LIMIT = 4000
CONSTRAINTS_FILE = "constraints.md"


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n… (обрезано)"


def _demote_headings(text: str) -> str:
    """Keep the section title the only H1: case files bring their own headings."""
    out: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        out.append(f"#{line}" if not fenced and line.startswith("#") else line)
    return "\n".join(out)


def _first_line(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                return stripped[:SUMMARY_LIMIT]
    except OSError:
        return ""
    return ""


def _role_card(role: str) -> tuple[str, str]:
    path = PACKAGE_ROOT / "roles" / f"{role}.md"
    if not path.is_file():
        return role, ""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        text = parts[2] if len(parts) == 3 else text
    return role, _clip(text, ROLE_CARD_LIMIT)


def _task_row(run_dir: Path, task_id: str) -> list[str]:
    for row in _plan_rows(run_dir):
        if row[0] == task_id:
            return row
    return []


def _accepted_context(run_dir: Path, order: dict[str, Any], registry: dict[str, Any]) -> list[str]:
    state = load_state(run_dir)
    pipeline = load_pipeline()
    lines: list[str] = []
    for stage in pipeline["stages"]:
        if state["stage_status"].get(stage["id"]) != "accepted":
            continue
        if stage["id"] == order.get("stage"):
            continue
        for artifact_id in stage["artifacts"]:
            spec = registry["artifacts"][artifact_id]
            path = run_dir / str(spec["path"])
            if not path.is_file():
                continue
            summary = _first_line(path)
            lines.append(f"- `{spec['path']}` — {summary or 'без заголовка'}")
            if len(lines) >= ACCEPTED_LIMIT:
                return lines
    return lines


def build_prompt(
    order: dict[str, Any],
    *,
    run_dir: Path,
    role: str | None = None,
    evidence: list[str] | None = None,
    max_chars: int = 26000,
    workspace: Path | None = None,
) -> str:
    from ..config import load_artifacts, load_pipeline as _pipeline

    workspace = Path(workspace) if workspace is not None else run_dir

    pipeline = _pipeline()
    state = load_state(run_dir)
    registry = load_artifacts(str(state["mode"]))
    role = str(role or order.get("next_role") or (order.get("roles") or [""])[0] or "")
    stage_id = order.get("stage")
    stage = find_stage(pipeline, stage_id) if stage_id else None

    lines: list[str] = []
    title, card = _role_card(role)
    lines.append(f"# Роль: `{title}`")
    if card:
        lines.append(card)
    else:
        lines.append("Карточка роли не найдена; работай по описанию задания ниже.")
    lines.append("")

    if stage is not None:
        lines.append(f"# Стадия `{stage['id']}`: {stage['title']}")
        lines.append(f"Режим: `{state['mode']}`. Статус стадии: `{state['stage_status'].get(stage['id'])}`.")
        lines.append("Критерии выхода:")
        lines.extend(f"- {item}" for item in stage["exit"])
        lines.append("")
        if stage["id"] == "s3_plan":
            lines.append("# Правила плана")
            lines.append(
                "- Каждый `REQ` из `spec.md` покрыт хотя бы одной задачей. Задачи, которые меняют "
                "один и тот же файл, обязаны быть связаны `depends_on` — иначе импорт плана "
                "отклоняется движком."
            )
            lines.append(
                "- `artifacts/traceability.json` принят на `s1_spec` и после гейта заморожен: не "
                "переписывай его. Распределение `REQ` по задачам в плане должно следовать матрице "
                "(её поле `tasks`), а не наоборот; колонку `REQ` плана и раздел «Покрытие REQ» "
                "построй по ней. Ссылки матрицы обязаны указывать на существующие `TASK-00N` — "
                "это проверяется на гейте `s3_plan`."
            )
            lines.append("")

    idea_path = run_dir / "idea.md"
    if idea_path.is_file():
        lines.append("# Идея продукта (вход от человека)")
        lines.append(_clip(idea_path.read_text(encoding="utf-8"), 2000))
        lines.append("")
        lines.append(
            "Это единственный источник истины о продукте: не подменяй его своей памятью, "
            "привычками или другими проектами на этой машине."
        )
        lines.append("")

    # Eval runs carry the case frame in `constraints.md` (copied by the eval
    # harness).  It is absent for a normal run, so no empty heading appears.
    constraints_path = run_dir / CONSTRAINTS_FILE
    if constraints_path.is_file():
        try:
            constraints = constraints_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            constraints = ""
        if constraints:
            lines.append("# Ограничения кейса")
            lines.append(_clip(_demote_headings(constraints), CONSTRAINTS_LIMIT))
            lines.append("")
            lines.append(
                "Это рамки кейса, а не режима: число `REQ`, состав задач и роли "
                "стадии проверяются, поэтому следуй им буквально."
            )
            lines.append("")

    accepted = _accepted_context(run_dir, order, registry)
    if accepted:
        lines.append("# Что уже принято (кратко)")
        lines.extend(accepted)
        lines.append("")

    lines.append("# Задание")
    instructions = str(order.get("instructions") or "")
    if "submit" in instructions.lower():
        # The engine writes instructions for a CLI user ("then submit them").
        # An executor that follows them runs `factory submit` itself and makes
        # the runtime's declared-only submission guarantee meaningless.
        instructions = (
            "Создай или обнови объявленные артефакты. Сдачу, проверку и гейты "
            "выполняет рантайм: тебе не нужно ничего сдавать."
        )
    lines.append(instructions)
    if order.get("task_id"):
        task = state["tasks"].get(str(order["task_id"]), {})
        row = _task_row(run_dir, str(order["task_id"]))
        lines.append("")
        lines.append(f"## Задача `{task.get('task_id')}` (владелец `{task.get('owner')}`)")
        lines.append(f"- REQ: {', '.join(task.get('reqs') or []) or '—'}")
        lines.append(
            "- Разрешённые файлы: "
            + (", ".join(f"`{item}`" for item in task.get("allowed_files") or []) or "—")
        )
        lines.append(f"- Отчёт: `tasks/{task.get('task_id')}/deliverable.md` (шаблон `templates/deliverable.tmpl`)")
        if len(row) >= 6 and row[5]:
            lines.append(f"- Команда проверки: `{row[5]}`")
        lines.append("- Не меняй файлы других задач и не выходи за разрешённые файлы.")
    lines.append("")

    stage_errors: list[str] = []
    if stage is not None:
        stage_errors = validate_stage(run_dir, stage, registry, state["tasks"])
    if stage_errors:
        lines.append("# Детерминированная проверка стадии не проходит")
        lines.extend(f"- {error}" for error in stage_errors[:8])
        lines.append("")
        if stage is not None and stage["id"] == "s1_spec":
            lines.append(
                "Каждый `REQ`, перечисленный в `spec.md`, обязан дословно присутствовать "
                "в `spec-human.md` и быть ключом в `traceability.json`."
            )
            lines.append("")
        if stage is not None and stage["id"] == "s3_plan":
            build_roles = effective_roles(
                pipeline, state["mode"], find_stage(pipeline, "s4_build")
            )
            lines.append(
                "Колонка роли в таблице `plan.md` должна содержать только роли, "
                f"активные на `s4_build` в режиме `{state['mode']}`: "
                + ", ".join(f"`{role}`" for role in build_roles)
                + ". Роль, которая писала план, не обязана исполнять задачи. "
                "Пути в колонке `Allowed Files` — относительно каталога прогона, "
                "без префикса `runs/<run_id>/`."
            )
            lines.append("")
        if stage is not None and stage["id"] == "s15_quarantine":
            lines.append(
                "Область сканирования — только каталог `skills/` внутри корня проекта. "
                "Не сканируй `~/.hermes`, `~/.claude`, `~/.codex` и другие библиотеки "
                "хост-агента: фабрика отвечает только за скиллы, которые ставит сама. "
                "Если в `skills/` нет скиллов, лок — `{\"schema_version\": \"1.0.0\", "
                "\"skills\": []}`. Пути в локе указываются относительно корня проекта и "
                "обязаны существовать. Отсутствие каталога `skills/` — штатный случай: "
                "достаточно одного поиска, чтобы это установить. Не перебирай дерево "
                "прогона, не читай `/proc`, `mountinfo` и не диагностируй песочницу — "
                "это работа рантайма; о неудачной записи достаточно одной строки в отчёте."
            )
            lines.append("")

    pending = list(order.get("pending_artifacts") or [])
    own = [item for item in pending if str(item.get("owner") or "") in {"", role}]
    foreign = [item for item in pending if item not in own]
    if own:
        lines.append("# Чего не хватает у тебя")
        specs = {str(spec.get("id")): spec for spec in order.get("artifacts") or []}
        for item in own:
            spec = specs.get(str(item.get("id")), {})
            lines.append(f"## `{item.get('path')}` (id `{item.get('id')}`, владелец `{item.get('owner')}`)")
            errors = item.get("errors") or []
            if errors:
                lines.append("Текущие ошибки валидации:")
                lines.extend(f"- {error}" for error in errors)
            required = spec.get("required_sections") or []
            if required:
                lines.append("Обязательные секции (заголовки должны совпадать дословно):")
                lines.extend(f"- `{section}`" for section in required)
            template = spec.get("template")
            if template:
                template_path = PACKAGE_ROOT / str(template)
                if template_path.is_file():
                    lines.append(f"Шаблон `factory/{template}`:")
                    lines.append("```markdown")
                    lines.append(_clip(template_path.read_text(encoding="utf-8"), TEMPLATE_LIMIT))
                    lines.append("```")
            lines.append("")
    else:
        lines.append("# Чего не хватает у тебя")
        if foreign:
            lines.append("Твои артефакты на месте; стадия ждёт другие роли:")
        elif stage_errors:
            lines.append(
                "Артефакты на месте, но стадия не проходит проверку: исправь ошибки выше, "
                "перезаписав соответствующие файлы."
            )
        else:
            lines.append("Все объявленные артефакты стадии на месте; проверь их валидность и заверши стадию.")
        lines.append("")
    if foreign:
        lines.append("# Ждут другие роли (не пиши их файлы)")
        lines.extend(
            f"- `{item.get('path')}` — `{item.get('owner')}`" for item in foreign
        )
        lines.append("")

    if evidence:
        lines.append("# Замечания предыдущей проверки")
        lines.extend(f"- {item}" for item in evidence)
        lines.append("")

    lines.append("# Разрешённые пути")
    lines.append(f"Работай только внутри `{workspace}`. Разрешено писать:")
    pending_paths = [str(item.get("path")) for item in own]
    if not pending_paths and (stage_errors or evidence):
        # A gate can reject a stage whose artifacts already exist (for example
        # a plan whose tasks share a file without a dependency).  The rework
        # step then has nothing pending, and an empty "allowed" list told the
        # agent it could rewrite nothing.
        pending_paths = [
            str(spec.get("path"))
            for spec in order.get("artifacts") or []
            if spec.get("path") and not spec.get("per_task")
        ]
    if order.get("task_id"):
        task = state["tasks"].get(str(order["task_id"]), {})
        pending_paths.extend(str(item) for item in task.get("allowed_files") or [])
        pending_paths.append(f"tasks/{order['task_id']}/deliverable.md")
    if stage is not None and stage["id"] == "s_in":
        # The drill writes a git bundle, a worktree archive and a restore
        # clone.  They are not registry artifacts, but they are evidence the
        # case checks and must be inside the run.
        pending_paths.append("backups/")
    for path in dict.fromkeys(pending_paths):
        lines.append(f"- `{path}`")
    lines.append("")

    lines.append("# Правила")
    lines.append("- Запрещено менять `state.json`, `blackbox.jsonl`, `blackbox.lock`, `.git` и код движка `factory/`.")
    lines.append(
        "- Не читай исходники движка (`factory/**`, `factory/artifacts.yaml`, `factory/artifacts.py`): "
        "шаблоны уже вложены в этот промпт, а правила проверки изложены ниже. Чтение движка не "
        "улучшает артефакт, но оплачивается на каждом следующем вызове."
    )
    lines.append("- Запрещено писать вне каталога прогона и создавать символические ссылки.")
    lines.append("- Не оставляй незаполненные `{{...}}` и пустые секции: валидатор движка их отклонит.")
    lines.append(
        "- Как проверяются Markdown-артефакты (это весь набор правил, другого не требуется): "
        "каждый обязательный заголовок — отдельная строка, совпадающая дословно, вне ```-блоков; "
        "под заголовком минимум одно слово; строки вида `{{имя}}` запрещены. "
        "Валидацию выполняет рантайм после сдачи — перечитывать свои файлы для самопроверки не нужно."
    )
    lines.append(
        "- Работай минимальным числом шагов: прочитай только то, чего нет в этом промпте, "
        "запиши файлы и заверши ход. Каждый лишний инструментальный вызов удорожает шаг."
    )
    lines.append("- Пиши артефакты на русском языке, в стиле шаблонов.")
    lines.append("- После `s5_acceptance` тесты продукта заморожены: не меняй `product/tests/`.")
    lines.append("- Не запускай сетевые установки и не подключай новые зависимости без разрешения.")
    lines.append(
        "- Не запускай команды `factory ...` (submit, verify, gate, task): это делает рантайм. "
        "Твоя работа заканчивается на файлах в разрешённых путях."
    )
    if stage is not None and stage["id"] == "s_in":
        lines.append(
            "- На `s_in` разрешено и требуется выполнять `git` и `tar` внутри каталога прогона: "
            "бэкап-бандл, архив worktree и манифест кладутся в `backups/`, восстановление "
            "проверяется во временном каталоге внутри прогона. Читать `.git` нужно, менять — нет."
        )
    lines.append("")
    lines.append("# Формат ответа")
    lines.append("1. Что сделано (2–5 строк).")
    lines.append("2. Созданные или изменённые файлы (точные пути).")
    lines.append("3. Чем проверено — только если в твоём наборе инструментов есть терминал; иначе одной строкой: «проверку выполняет рантайм».")
    prompt = "\n".join(lines)
    if len(prompt) > max_chars:
        prompt = prompt[:max_chars].rstrip() + "\n\n… (промпт обрезан по лимиту рантайма)"
    return prompt
