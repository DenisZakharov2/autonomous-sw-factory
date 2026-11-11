# Пошаговый план исправления autonomous-sw-factory (на основе Audit3.md)

**Подход:** Ponytail — минимальные изменения, сохраняем валидацию/безопасность. Каждое исправление — атомарный diff, за которым следует запуск затронутых тестов.

---

## P0 — Безопасность и упаковка (Critical)

### P0-1: `pyproject.toml` — добавить пропущенные ресурсные каталоги в `package-data`

**Файл:** `pyproject.toml` (стр. 46–47)

**Проблема:** `roles/*`, `memory/*`, `ui/static/*`, `templates/**/*`, `runtime/fixtures/**/*` не попадают в `.whl`. После `pip install` пакет ломается (`factory doctor`, `factory ui`, `factory run --executor fake` падают).

**Исправление:**
```toml
[tool.setuptools.package-data]
factory = [
    "pipeline.yaml", "models.yaml", "budget.yaml", "artifacts.yaml", "FACTORY_GLOSSARY.md",
    "orchestrator/*.md",
    "roles/*.md",
    "schemas/*.json",
    "templates/**/*.tmpl",
    "memory/*.md",
    "ui/static/*",
    "runtime/fixtures/**/*",
]
```

**Проверка:**
```bash
pip install -e .  # dev
pip build --wheel
cd /tmp && pip install /path/to/dist/*.whl
factory doctor
factory ui --help
```

---

### P0-2: `factory/runtime/worktree.py` — защита от симлинк-атак в `merge()`

**Файл:** `factory/runtime/worktree.py` (стр. 321–330)

**Проблема:** `source.read_bytes()` и `source.is_file()` разыменовывают симлинки. Злоумышленник в worktree создаёт `product/src/x.py -> /home/operator/.ssh/id_ed25519`; при merge основной процесс (под оператором) читает секрет и кладёт в `run_dir`, доступный `factory-executor`.

**Исправление** — добавить `_reject_symlink_chain` и вызывать для `source` и `target`:
```python
def _reject_symlink_chain(root: Path, candidate: Path) -> None:
    rel_parts = candidate.relative_to(root).parts
    probe = root
    for part in rel_parts:
        probe = probe / part
        if probe.is_symlink():
            raise RuntimeFailure(f"refusing symlink in worktree merge path: {probe}")

# В merge(), перед чтением/записью:
rel_norm = normalise_allowed(relative)
source = worktree / rel_norm
_reject_symlink_chain(worktree, source)
_reject_symlink_chain(self.run_dir, target)
```

**Проверка:** создать симлинк в worktree, запустить merge — ожидаем `RuntimeFailure`.

---

### P0-3: `factory/runtime/isolation.py` — устранить shell-инъекцию, TOCTOU/symlink атаку на staging, подмену пользователя через `LOGNAME`

**Файл:** `factory/runtime/isolation.py` (стр. 172–228, 314–338) + `factory/cli.py` (стр. 105)

**Проблемы:**
1. `ensure_env_root` вызывает `/bin/sh -c "mkdir -p ... && chown ... && chmod ..."` под root — инъекция через `user`.
2. `install_env_file` создаёт staging в `workdir` (где у `factory-executor` есть ACL `rwX`) — можно подложить симлинк `.env.staging -> /etc/shadow`, который `install` от root скопирует.
3. `cli.py` использует `getpass.getuser()` — подменяется через `LOGNAME`/`USER`.

**Исправления:**

1. `ensure_env_root` — заменить на атомарный `install -d`:
```python
def ensure_env_root(user: str) -> None:
    _validate_unix_user(user)
    if ENV_ROOT.is_dir():
        return
    root_call(["/usr/bin/install", "-d", "-m", "0750", "-o", "root", "-g", user, str(ENV_ROOT)])
```

2. `install_env_file` — staging через `tempfile.NamedTemporaryFile` (O_EXCL|O_CREAT, 0600):
```python
def install_env_file(user: str, run_id: str, env: dict[str, str], workdir: Path) -> Path:
    _validate_unix_user(user)
    ensure_env_root(user)
    target = env_file_path(run_id)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, prefix="factory-env-") as tmp:
        tmp.write(_render_env_file(env))
        staging = Path(tmp.name)
    try:
        staging.chmod(0o600)
        root_call(["/usr/bin/install", "-m", "0640", "-o", "root", "-g", user, str(staging), str(target)])
    finally:
        staging.unlink(missing_ok=True)
    return target
```

3. Добавить валидатор имени пользователя:
```python
USER_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")

def _validate_unix_user(user: str) -> str:
    if not USER_NAME_RE.fullmatch(user):
        raise ExecutorUserError(f"invalid unix username: {user!r}")
    return user
```
Вызвать в `ensure_env_root`, `install_env_file`, `ensure_ready`, `setup_executor_user`.

4. `_env_escape` — экранировать `\n` и `\r`:
```python
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
```

5. `factory/cli.py` (стр. 105) — заменить `getpass.getuser()`:
```python
import pwd
operator = pwd.getpwuid(os.getuid()).pw_name
```

**Проверка:** запустить `factory setup-executor`, проверить polkit-правило, попробовать подсунуть симлинк в staging — ожидаем отказ.

---

### P0-4: `factory/runtime/sandbox.py` — исправить перевёрнутую проверку родительских путей и добавить `CONTROL_PLANE_FILES/DIRS` в `_forbidden`

**Файл:** `factory/runtime/sandbox.py` (стр. 80–93, 170–180)

**Проблема:** стр. 91 проверяет `path in protected.parents` (является ли path родителем `runtime/`), но **не проверяет** `protected in path.parents` (находится ли path внутри `runtime/`). Вложенные пути вроде `runtime/evidence/step-0001.json` проходят как разрешённые для записи. Также не защищены `CONTROL_PLANE_FILES` и `CONTROL_PLANE_DIRS`.

**Исправление:**
```python
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
```

**Проверка:** попытаться смонтировать `runtime/evidence` или `checks` как read-write — ожидаем `RuntimeFailure`.

---

### P0-5: `factory/checkpoint.py` — исправить проверку статуса `stale` и `KeyError: 'index'`

**Файл:** `factory/checkpoint.py` (стр. 215–298)

**Проблемы:**
1. `checkpoint_state`: статус `stale` ставится если `count > signed_count`, **без проверки** что хеш на позиции `signed_count - 1` совпадает с `newest["head_hash"]`. Злоумышленник может переписать журнал целиком и добавить 1 событие — статус `stale`, `verify` вернёт 0 ошибок.
2. Стр. 293: `entry['index']` — ключа нет, `KeyError`.

**Исправления:**

1. В `checkpoint_state` (стр. 233–243):
```python
signed_count = int(newest.get("event_count") or 0)
if newest.get("head_hash") and count > signed_count and signed_count > 0:
    if events[signed_count - 1].get("hash") == newest.get("head_hash"):
        result["status"] = "stale"
    else:
        result["status"] = "diverged"
```

2. В `verify_checkpoint` (стр. 292–293):
```python
for index, entry in enumerate(checkpoints, 1):
    ...
    if not hmac.compare_digest(str(entry.get("hmac")), _signature(secret, body)):
        errors.append(f"checkpoint #{index} signature does not match its own contents")
```

**Проверка:** подделать журнал (переписать историю, добавить событие) — `factory verify` должен вернуть `diverged` с ошибкой.

---

## P1 — Корректность ядра, рантайма и схем (High)

### P1-1: Синхронизировать regex `task_id` во всех схемах

**Файлы:**
- `factory/schemas/event.schema.json` — `^TASK-[A-Z0-9]+$` (строго)
- `factory/schemas/task.schema.json` — `^TASK-[A-Z0-9-]+$`
- `factory/schemas/state.schema.json` — `^TASK-[A-Z0-9-]+$`
- `factory/paths.py` — `^TASK-[A-Z0-9][A-Z0-9-]{0,62}$`

**Проблема:** `TASK-API-01` проходит `paths.py` и `state.schema.json`, но роняет `event.schema.json` при записи в `blackbox.jsonl`.

**Исправление:** привести `event.schema.json` к `^TASK-[A-Z0-9][A-Z0-9-]{0,62}$` (как в `paths.py`).

**Проверка:** запустить тесты схем, создать задачу с дефисом — должна пройти валидацию журнала.

---

### P1-2: `factory/runtime/loop.py` — поправить расчёт токенов для кириллицы и использовать `context_window`

**Файл:** `factory/runtime/loop.py` (стр. 55–75) + `factory/models.yaml`

**Проблемы:**
1. `_effective_prompt_chars` считает не-ASCII как 2 символа (~0.5 токена), но для кириллицы в BPE ~1.7–2.2 символа/токен. Промпт на 98k русских символов весит ~48–55k токенов и пробивает окно 32k без срабатывания защиты.
2. Используется `max_tokens` (лимит генерации) вместо `context_window` (размер входного окна).

**Исправления:**

1. В `models.yaml` добавить `context_window` для каждого тира (если нет — fallback на `max_tokens`).
2. Заменить функции:
```python
def _estimate_prompt_tokens(text: str) -> int:
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return int((ascii_chars / 4.0) + (non_ascii_chars / 1.8))

def _context_limit_tokens(role: str | None) -> int:
    tier = "tier-2-balanced"
    if role:
        try:
            tier = str(_model_for(role)["tier"])
        except Exception:
            pass
    tier_cfg = load_models()["tiers"][tier]
    window = int(tier_cfg.get("context_window") or tier_cfg["max_tokens"])
    return int(window * MAX_CHARS_RATIO)
```
В `run_loop` использовать `_estimate_prompt_tokens(prompt)` и `_context_limit_tokens(...)`.

**Проверка:** прогнать задачу с большим русскоязычным промптом — защита `context_exceeded` должна сработать до вызова модели.

---

### P1-3: `factory/runtime/usage.py` — не обнулять бюджет при частичных `None`

**Файл:** `factory/runtime/usage.py` (стр. 95–108, 152–159)

**Проблема:** один `tokens_total: null` обнуляет `totals.tokens` на весь оставшийся прогон. Условие `if totals.tokens is not None:` в `budget_violation()` отключает контроль бюджета.

**Исправление:** суммировать только известные значения (lower bound):
```python
def totals(self) -> UsageTotals:
    rows = self.records()
    seconds = sum(float(row.get("seconds") or 0.0) for row in rows)
    known_tokens = 0
    has_known_tokens = False
    missing_tokens = False
    known_usd = 0.0
    has_known_usd = False
    tokens_in = 0
    has_known_in = False
    tokens_out = 0
    has_known_out = False
    cache_read = 0
    has_known_cache = False
    for row in rows:
        value = row.get("tokens_total")
        if value is None:
            missing_tokens = True
        else:
            known_tokens += int(value)
            has_known_tokens = True
        cost = row.get("usd")
        if cost is None:
            pass
        else:
            known_usd += float(cost)
            has_known_usd = True
        for name, target, has_flag in (
            ("tokens_in", "in", has_known_in),
            ("tokens_out", "out", has_known_out),
            ("cache_read", "cache", has_known_cache),
        ):
            raw = row.get(name)
            if raw is not None:
                val = int(raw)
                if name == "tokens_in":
                    tokens_in += val
                    has_known_in = True
                elif name == "tokens_out":
                    tokens_out += val
                    has_known_out = True
                else:
                    cache_read += val
                    has_known_cache = True
    tokens = known_tokens if has_known_tokens else None
    usd = known_usd if has_known_usd else None
    tokens_in = tokens_in if has_known_in else None
    tokens_out = tokens_out if has_known_out else None
    cache_read = cache_read if has_known_cache else None
    return UsageTotals(
        steps=len(rows),
        seconds=seconds,
        tokens=tokens,
        usd=usd,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_read=cache_read,
    )
```

**Проверка:** смоделировать частичный `usage.jsonl` с `null` — `factory verify` должен проверять бюджет по накопленным токенам.

---

### P1-4: `factory/quarantine.py` — исправить порядок веток в `decide()` и `verify_lock()`

**Файл:** `factory/quarantine.py` (стр. 67–85, 213–248)

**Проблема:** проверка `if exit_code != 0: return BLOCK_RETRY` стоит **выше** проверки `DO_NOT_INSTALL` / `HIGH` / `CRITICAL`. Сканер при угрозах завершается с ненулевым кодом → вредоносный скилл получает `BLOCK_RETRY` вместо `REJECT_AND_PURGE` и **не удаляется**.

**Исправление `decide()`:**
```python
def decide(exit_code: int, result: ScanResult, min_coverage: float = 95.0) -> str:
    if result.recommendation == "DO_NOT_INSTALL" or result.severity in {"HIGH", "CRITICAL"}:
        return REJECT_AND_PURGE
    if not result.is_complete or result.failed_analyzers or result.coverage_percent < min_coverage:
        return BLOCK_INCOMPLETE
    if exit_code != 0:
        return BLOCK_RETRY
    if result.recommendation == "SAFE" and result.severity == "LOW":
        return AUTO_ALLOW
    return ESCALATE_HUMAN
```

**Исправление `verify_lock()`** (стр. 236–247): разрешить `CAUTION` с `human:` approver и `MEDIUM` severity, проверить `coverage_percent >= 95.0`:
```python
scanner = entry.get("scanner") or {}
rec = scanner.get("recommendation")
human_ok = rec == "CAUTION" and str(entry.get("approved_by", "")).startswith("human:")
safe_ok = (
    rec == "SAFE"
    and scanner.get("severity", "LOW") == "LOW"
    and float(scanner.get("coverage_percent", 100.0)) >= 95.0
)
if not scanner.get("is_complete") or not (safe_ok or human_ok):
    errors.append(f"{entry.get('name')}: not approved by a complete SAFE scan")
```

**Проверка:** смоделировать сканер с `exit_code=1`, `severity=HIGH` — должен вернуть `REJECT_AND_PURGE` и удалить скилл.

---

### P1-5: `factory/retrieval.py` — исправить токенизатор (латинская `A`), индексацию строк, кэш между проектами

**Файл:** `factory/retrieval.py` (стр. 24, 30–32, 134–148, 221–235)

**Проблемы:**
1. Регулярка `r"[A-Za-zA-Яа-яЁё0-9_]+"` — `A` перед `-Я` латинская (U+0041), диапазон захватывает `[`, `\`, `]`, `^`, `` ` ``, `{`, `|`, `}`, `~`.
2. `text.find(head, start_line - 1)` — второй аргумент `str.find` это смещение в символах, а не номер строки. Номера строк ломаются.
3. Единый кэш `~/.cache/factory/index/index.json` без проверки `data["root"]` — при переключении проектов подмешиваются чанки из прошлого репо.

**Исправления:**

1. Токенизатор:
```python
def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zа-яё0-9_]+", text.lower())
```

2. Индексация чанков — использовать `char_cursor`:
```python
char_cursor = 0
for piece in pieces:
    idx = text.find(piece, char_cursor)
    if idx < 0:
        idx = char_cursor
    line_no = text.count("\n", 0, idx) + 1
    end_line = line_no + piece.count("\n")
    char_cursor = max(0, idx + len(piece) - CHUNK_OVERLAP)
```

3. В `retrieve()` добавить проверку рута:
```python
def retrieve(query: str, *, index_dir: Path | None = None, k: int = TOP_K_DEFAULT) -> list[dict[str, Any]]:
    data = _load(index_dir)
    if Path(data.get("root", "")).resolve() != project_root().resolve():
        data = build_index(project_root(), index_dir=index_dir)
    ...
```

**Проверка:** построить индекс в проекте А, переключиться на проект Б — RAG не должен возвращать чанки из А.

---

### P1-6: `factory/pipeline.yaml` + `PIPELINE.md` — обновить бюджет `enterprise`

**Файлы:** `factory/pipeline.yaml`, `PIPELINE.md`

**Проблема:** бюджет `enterprise` = 6M токенов, но реальный прогон `enterprise-mini` потребовал 11.88M. Любой реальный прогон гарантированно упадёт по `budget_exceeded`.

**Исправление:** поднять до `15_000_000` (11.88M × 1.25 запас).

**Проверка:** запустить `factory eval --case enterprise-mini --executor fake` — должен пройти без `budget_exceeded`.

---

## P2 — Дашборд, документация, CI (Medium)

### P2-1: `factory/ui/export.py` — защита от Formula Injection с ведущими пробелами

**Файл:** `factory/ui/export.py` (стр. 99–112)

**Проблема:** `_cell` проверяет `value.startswith(_FORMULA_PREFIXES)`, но не учитывает ведущие пробелы/переводы строк (`" =1+1"`, `"\n=CMD|..."`), которые Excel всё равно интерпретирует как формулу.

**Исправление:**
```python
def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    if text.lstrip(" \t\r\n").startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text
```

---

### P2-2: `factory/ui/data.py` — исправить `_usage_up_to_stage`, `timeseries_revision`, `tail[-limit:]`

**Файл:** `factory/ui/data.py` (стр. 359–386, 799–816, 1033–1039)

**Проблемы:**
1. `_usage_up_to_stage` и `_usage_for_stage` имеют идентичное тело — кумулятивный график показывает расход только текущей стадии.
2. `timeseries_revision` не включает `metrics` в ETag — смена метрик не инвалидирует кэш (возвращается `304 Not Modified` со старыми данными).
3. `run_payload` использует `tail[:limit]` вместо `tail[-limit:]` и жёстко возвращает `"limit": TAIL_LIMIT`.

**Исправления:**

1. `_usage_up_to_stage` — суммировать все стадии с `stage_order <= target_stage_order`:
```python
def _usage_up_to_stage(usage_records: list[dict[str, Any]], stage: str | None, field: str) -> int | float | None:
    if not stage:
        return None
    pipeline = load_pipeline()
    stage_order = {s["id"]: s["order"] for s in pipeline["stages"]}
    target_order = stage_order.get(stage, 0)
    total = 0
    has_value = False
    for record in usage_records:
        rec_stage = record.get("stage")
        if rec_stage and stage_order.get(rec_stage, 0) <= target_order:
            val = _number(record.get(field))
            if val is not None:
                total += val
                has_value = True
    return total if has_value else None
```

2. `timeseries_revision` — добавить `metrics` в хеш:
```python
def timeseries_revision(run_id: str, root: str | Path | None = None, bucket: str = "stage", metrics: list[str] | None = None) -> str | None:
    ...
    metrics_key = ",".join(metrics or TIMESERIES_METRICS)
    parts = [
        f"journal={journal_stamp}",
        f"usage={usage_stamp}",
        f"bucket={bucket}",
        f"metrics={metrics_key}",
    ]
    ...
```

3. `run_payload` (стр. 1033–1039):
```python
shown_tail = tail[-limit:] if limit else tail[:TAIL_LIMIT]
journal = {
    ...
    "shown": len(shown_tail),
    "limit": limit or TAIL_LIMIT,
    ...
}
```

---

### P2-3: Документация — исправить все 14 лингвистических ошибок и расхождения

**Файлы:** `README.md`, `README.en.md`, `CHANGELOG.md`, `AUDIT.md`, `ROSTER.md`, `factory/FACTORY_GLOSSARY.md`, `docs/runtime.md`, `DESIGN.md`, `PIPELINE.md`, `SECURITY.md`, `TECHNICAL.md`

**Список исправлений (из аудита):**

| # | Файл | Ошибка | Исправление |
|---|------|--------|-------------|
| 1 | README.md:60 | `промолчально` | `молча (или: без предупреждения)` |
| 2 | README.md:63, docs/runtime.md:241 | `8192 токенов` | `8192 токена` |
| 3 | docs/runtime.md:243 | `393 216 символа` | `393 216 символов` |
| 4 | README.md:305 | `Тип разрешается` | `Тир (уровень модели) разрешается` |
| 5 | README.md:175, README.en.md:158 | висячий `\|` | удалить |
| 6 | README.en.md:133 | `open_code` | `opencode` |
| 7 | CHANGELOG.md:12 | `в полной прогоно` (2×) | `при полном прогоне` |
| 8 | CHANGELOG.md:49 | `ср6` | `cp6` |
| 9 | AUDIT.md:113 | `текущего рабочей директории` | `текущей рабочей директории` |
| 10 | AUDIT.md:120 | `единственный источником` | `единственным источником` |
| 11 | AUDIT.md:130,146 | `записах` / `сисд` | `записях` / `в пользовательском экземпляре systemd` |
| 12 | ROSTER.md:41 | `роли 01 ... пишет` | `роль 01 ... пишет` |
| 13 | FACTORY_GLOSSARY.md:16 | `Greenfield-бэкапа` | `Brownfield-бэкапа на стадии s_in` |
| 14 | README.md:51 | `не начнет` | `не начнёт` |

**Дополнительно:**
- README.en.md: добавить отсутствующий раздел про контроль контекста (как в README.md строки 54–69).
- GitHub About: заменить `5 reference cases` на `6 reference cases`.
- CI: добавить тестирование `.whl` из `/tmp` с extra `[xlsx]`.

---

### P2-4: CI — тестировать собранный wheel

**Файл:** `.github/workflows/ci.yml` (job `build`)

**Исправление:** после сборки установить wheel в чистую директорию и прогнать smoke-тесты:
```yaml
- name: Test wheel install
  run: |
    cd /tmp
    pip install ${{ github.workspace }}/dist/*.whl[xlsx]
    factory doctor
    factory --help
    factory ui --help
```

---

## Порядок выполнения и верификация

1. **P0-1..P0-5** — безопасность/упаковка. После каждого: `pytest tests/ -x -q` + smoke `factory doctor`.
2. **P1-1..P1-6** — корректность ядра. После каждого: `pytest tests/ -k "relevant" -q`.
3. **P2-1..P2-4** — дашборд/доки/CI. После: `pytest tests/ui -q`, `factory ui` ручной прогон.

**Итоговый чек-лист перед релизом:**
- [ ] `pip install dist/*.whl[xlsx]` в чистом окружении — `factory doctor` зелёный
- [ ] `factory eval --case enterprise-mini --executor fake` — проходит без `budget_exceeded`
- [ ] `factory eval --case nano-todo --case standard-mini --case nano-product-mini --case product-mini --executor fake` — все 5 кейсов в CI
- [ ] `pytest tests/ -q` — 409/409 passed
- [ ] Лингвистические правки применены во всех файлах
- [ ] GitHub About обновлено на `6 reference cases`

---

**Риски, которые пользователь должен знать:**
- Изменение расчёта токенов (P1-2) может сработать `context_exceeded` на существующих прогонах — это правильное поведение, но может потребовать дробить задачи мельче.
- Порядок веток в карантине (P1-4) меняет поведение для пограничных сканеров — прогнать `factory skills install` на тестовых скиллах.
- Новый regex `task_id` в `event.schema.json` (P1-1) строже — если в журнале уже есть `TASK-API-01`, они пройдут (совпадает с `paths.py`), но старые невалидные ID будут отклоняться.