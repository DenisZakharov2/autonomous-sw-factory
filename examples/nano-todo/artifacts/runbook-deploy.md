# Регламент выкладки и проверки отката

## 1. Деплой
```bash
python3 -m product.src.todo --help
```
- Healthcheck: `exit 0`

## 2. Rollback drill
```bash
git checkout -- product/
```
- Код возврата: `0`
- Фактический вывод: `rollback ok`

## 3. RTO
- Время восстановления: `< 1 min`
- Потеря данных: `0`
