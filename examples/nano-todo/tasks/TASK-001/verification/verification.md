# Отчёт независимого верификатора

## 1. Проверено
- Предмет проверки: `task TASK-001`
- Артефакты (только чтение): `tasks/TASK-001/deliverable.md`, `product/src/todo.py`, `product/tests/test_todo.py`

## 2. Проверки
### Проверка 1: детерминированная проверка

- Команда: `factory verify --run-id nano-todo --stage s4_build`
- Код возврата: `0`
- Вывод:

```text
deterministic checks, state and hash chain: OK
```

### Проверка 2: границы записи

- Команда: `test -r tasks/TASK-001/deliverable.md`
- Код возврата: `0`
- Вывод:

```text
artifacts readable; none writable by the verifier
```

### Проверка 3: подмена или недостача

- Команда: `factory verify --run-id nano-todo --stage s4_build`
- Код возврата: `0`
- Вывод:

```text
no missing or tampered artifact reported
```

## 3. Вердикт
- Вердикт: PASS
- Причина: `все три проверки вернули 0, расхождений нет`
