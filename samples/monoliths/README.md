# Дополнительные монолиты

Пять монолитов по 800–1100 строк на разные темы (S1.7): проверка того, что разбор, анализ и декомпозиция обобщаются за пределы `demo-orders`. Устроены так же: один «god-class» `Main` с ролями parse/analyze/optimize/print, вспомогательные типы в том же файле, ввод команд через `Scanner` (кириллица), вывод `printf`, заведомые дефекты и «оптимизируемые» участки, 9–10 сценариев с `expected_stdout`. Код написан по описанию организаторов (ИИ как инструмент), каждый файл проверен: `javac`, повторяемый вывод, вывод просмотрен вручную.

| Монолит | Тема | Строк | Что показывает |
|---|---|---|---|
| `text-analyzer/` | анализ текста: слова, частоты, анаграммы, палиндромы, перенос строк, шифр Цезаря | 810 | `contains` по `ArrayList` (`uniqueWords`), вложенный поиск повторов (`dedupeLines`), конкатенация в `replaceWord`, `==` для строк (`isConjunction`), пустой `catch` в `readLines`, клоны `countVowels`/`countConsonants`/`countDigits` |
| `inventory/` | склад: приход, отгрузка, резервы, цены, аудит, пополнение | 909 | вложенное слияние (`mergeSimilar`), `contains` (`categories`), конкатенация (`buildCsv`), `==` (`audit`), пустой `catch` в `readBatch`, клоны `checkSku`/`checkName`/`checkCategory` |
| `expression-calculator/` | калькулятор: токенизатор, рекурсивный разбор, ОПЗ, упрощение дерева, переменные | 1107 | клоны `parseComparison`/`parseAdditive`/`parseMultiplicative`, `contains` в обходе дерева, вложенный подсчёт повторов (`countRepeats`), конкатенация (`rpnString`), `==` (`isConstantName`), пустой `catch` в `evaluateBatch` |
| `graph-paths/` | сеть дорог: обходы, Дейкстра, Флойд, компоненты, циклы | 906 | массивы и матрицы, вложенное слияние рёбер (`mergeParallelEdges`), `contains` в `components`, конкатенация (`routeString`), `==` (`printNodes`), пустой `catch` в `readBatch`, клоны `countIsolated`/`countLeaves`/`countHubs` |
| `library/` | библиотека: каталог (книги и журналы), читатели, выдачи, продления, штрафы, отчёты | 960 | иерархия классов (`abstract Item` → `Book`, `Magazine`), `enum` с полем, вложенный `static class Stats`, текстовый блок, `Stream`; поиск по номеру в списках `findItemById`/`findReaderById`, вложенное слияние (`mergeDuplicateItems`), `contains` (`uniqueAuthors`), конкатенация (`buildCatalogCsv`), `==` для строк (`sameTitle`), пустой `catch` в `addBook`, клоны `checkTitle`/`checkAuthor`/`checkReaderName` |

Состав сценариев в каждом: `empty-input`, `bad-format`, `uncaught-exception` (команда `boom`, код выхода 1), `boundary-values`, `large-input` (250–400 строк ввода), несколько тематических.

Проверка: `python scripts/verify_corpus.py monoliths` (JDK) и `backend/tests/unit/test_corpus_layout.py` (без JDK). Запасные темы из плана (матрицы, банк) остаются на случай ещё одного монолита.
