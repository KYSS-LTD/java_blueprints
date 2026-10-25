# Баг-баш на корпусе

| Пример | Методов | Блок-схем | Находок | Исправлений | Сборка | Экспорт javac | Оптимизация | Время |
|---|---|---|---|---|---|---|---|---|
| analyzer-pairs | 9 | 9 | 4 | 1 | ✓ | ✓ | — | 1.8 с |
| broken | 6 | 6 | 3 | 3 | — | — | — | — с |
| demo-orders | 88 | 88 | 37 | 23 | ✓ | ✓ | passed | 49.7 с |
| monoliths/expression-calculator | 87 | 87 | 48 | 14 | ✓ | ✓ | passed | 37.1 с |
| monoliths/graph-paths | 67 | 67 | 39 | 17 | ✓ | ✓ | passed | 33.0 с |
| monoliths/inventory | 82 | 82 | 28 | 17 | ✓ | ✓ | passed | 40.1 с |
| monoliths/library | 93 | 93 | 31 | 16 | ✓ | ✓ | passed | 34.0 с |
| monoliths/text-analyzer | 63 | 63 | 23 | 11 | ✓ | ✓ | passed | 25.1 с |
| out-of-scope/reflection-and-net | 2 | 2 | 0 | 0 | ✓ | ✓ | — | 1.4 с |
| out-of-scope/threads | 2 | 2 | 3 | 1 | ✓ | ✓ | — | 1.4 с |
| out-of-scope/time-and-files | 1 | 1 | 0 | 0 | ✓ | ✓ | — | 1.4 с |
| scope/01-arrays | 1 | 1 | 4 | 2 | ✓ | ✓ | — | 1.3 с |
| scope/01-classes | 6 | 6 | 0 | 0 | ✓ | ✓ | — | 1.4 с |
| scope/01-control | 5 | 5 | 0 | 0 | ✓ | ✓ | — | 1.3 с |
| scope/01-ctors | 8 | 8 | 1 | 0 | ✓ | ✓ | — | 1.3 с |
| scope/01-expr | 1 | 1 | 3 | 1 | ✓ | ✓ | — | 1.3 с |
| scope/01-fields | 3 | 3 | 1 | 0 | ✓ | ✓ | — | 1.4 с |
| scope/01-inheritance | 11 | 11 | 2 | 0 | ✓ | ✓ | — | 1.4 с |
| scope/01-interfaces | 11 | 11 | 1 | 0 | ✓ | ✓ | — | 1.6 с |
| scope/01-jumps | 3 | 3 | 2 | 1 | ✓ | ✓ | — | 1.3 с |
| scope/01-lang | 5 | 5 | 3 | 2 | ✓ | ✓ | — | 1.5 с |
| scope/01-loops | 1 | 1 | 4 | 2 | ✓ | ✓ | — | 1.3 с |
| scope/01-methods | 9 | 9 | 2 | 2 | ✓ | ✓ | passed | 12.9 с |
| scope/01-nested | 5 | 5 | 3 | 0 | ✓ | ✓ | — | 1.3 с |
| scope/01-try | 6 | 6 | 0 | 0 | ✓ | ✓ | — | 1.4 с |
| scope/01-types | 1 | 1 | 4 | 2 | ✓ | ✓ | — | 1.3 с |
| scope/02-charsets | 1 | 1 | 0 | 0 | ✓ | ✓ | — | 1.1 с |
| scope/02-format | 1 | 1 | 2 | 1 | ✓ | ✓ | — | 1.1 с |
| scope/02-output | 1 | 1 | 2 | 1 | ✓ | ✓ | — | 1.1 с |
| scope/02-reader | 1 | 1 | 1 | 0 | ✓ | ✓ | — | 1.1 с |
| scope/02-scanner | 1 | 1 | 1 | 0 | ✓ | ✓ | — | 1.1 с |
| scope/03-comparator | 8 | 8 | 0 | 0 | ✓ | ✓ | — | 1.5 с |
| scope/03-factories | 3 | 3 | 7 | 2 | ✓ | ✓ | — | 1.4 с |
| scope/03-iter | 3 | 3 | 2 | 0 | ✓ | ✓ | — | 1.3 с |
| scope/03-list | 1 | 1 | 5 | 3 | ✓ | ✓ | — | 1.4 с |
| scope/03-map | 1 | 1 | 2 | 0 | ✓ | ✓ | — | 1.5 с |
| scope/03-optional | 3 | 3 | 1 | 0 | ✓ | ✓ | — | 1.5 с |
| scope/03-set | 1 | 1 | 2 | 0 | ✓ | ✓ | — | 1.4 с |
| scope/03-utils | 1 | 1 | 5 | 2 | ✓ | ✓ | — | 1.4 с |
| scope/04-checked | 3 | 3 | 2 | 0 | ✓ | ✓ | — | 1.3 с |
| scope/04-custom | 10 | 10 | 2 | 0 | ✓ | ✓ | — | 1.4 с |
| scope/04-jdk | 2 | 2 | 5 | 2 | ✓ | ✓ | — | 1.4 с |
| scope/04-throws | 4 | 4 | 1 | 0 | ✓ | ✓ | — | 1.4 с |
| scope/04-uncaught | 3 | 3 | 2 | 1 | ✓ | ✓ | — | 1.2 с |
| scope/b1-enum | 7 | 7 | 3 | 0 | ✓ | ✓ | — | 1.5 с |
| scope/b1-record | 6 | 6 | 7 | 4 | ✓ | ✓ | — | 1.5 с |
| scope/b2-generics | 13 | 13 | 1 | 0 | ✓ | ✓ | — | 1.5 с |
| scope/b3-lambda | 4 | 4 | 4 | 2 | ✓ | ✓ | — | 1.6 с |
| scope/b4-stream | 1 | 1 | 5 | 3 | ✓ | ✓ | passed | 17.2 с |

## Итог

Дефектов не найдено: 49 примеров прошли весь путь.
