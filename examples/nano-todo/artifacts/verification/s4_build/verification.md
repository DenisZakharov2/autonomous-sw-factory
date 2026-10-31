# Отчёт независимого верификатора

## 1. Проверено
- Предмет проверки: `stage s4_build`
- Артефакты (только чтение): `product/src`, `product/tests`

## 2. Проверки
### Проверка 1: детерминированная проверка

- Команда: `factory verify --run-id nano-todo --stage s4_build`
- Код возврата: `0`
- Вывод:

```text
deterministic checks, state and hash chain: OK
```

### Проверка 2: границы записи

- Команда: `test -r product/src`
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
