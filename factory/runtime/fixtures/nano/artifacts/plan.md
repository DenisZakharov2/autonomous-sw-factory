# План декомпозиции задач

## 1. Задачи
| `task_id` | Роль | `REQ` | `depends_on` | `Allowed Files` | Команда проверки |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `TASK-001` | `12-fullstack-developer` | `REQ-001`, `REQ-002` | `-` | `product/src/todo.py`, `product/tests/test_todo.py` | `python3 -m unittest discover -s product/tests` |
| `TASK-002` | `12-fullstack-developer` | `REQ-003` | `TASK-001` | `product/src/todo.py`, `product/tests/test_todo.py` | `python3 -m unittest discover -s product/tests` |

## 2. Покрытие REQ
| REQ | Задачи |
| :--- | :--- |
| `REQ-001` | `TASK-001` |
| `REQ-002` | `TASK-001` |
| `REQ-003` | `TASK-002` |

Все `REQ` покрыты: `true`.
