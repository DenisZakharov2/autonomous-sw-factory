# Пользовательские потоки

## 1. Экраны
| Экран | Назначение | REQ |
| :--- | :--- | :--- |
| `CLI` | все команды | `REQ-001` |

## 2. Потоки
`add`: `todo add text -> list`
`done`: `todo list -> todo done 1 -> todo list`

## 3. Состояния
| Экран | Empty | Loading | Populated | Error | Partial/Offline |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `CLI` | пустой список | - | список | сообщение | - |
