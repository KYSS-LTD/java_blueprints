# Комплект сдачи

Сводка по чек-листу [docs/12 § 3](../docs/12-demo-and-submission.md).

| Пункт | Состояние |
|---|---|
| `README.md` | есть |
| `SOLUTION.md` | есть |
| `docker-compose.yml` | есть |
| `docs/README.md` | есть |
| `docs/13-acceptance.md` | есть |
| `samples/demo-orders/Main.java` | есть |

| Проверка | Результат |
|---|---|
| pytest (backend) | ✓ |
| vitest (frontend) | ✓ |
| verify_samples | ✓ |
| bug_bash | ✓ |
| perf_budget | ✓ |
| fix_differential | ✓ |
| extract_differential | ✓ |
| jdk_sweep | ✓ |

- pytest: 1447 из 1449 (`reports/pytest-report.xml`)
- vitest: 288 из 288 (`reports/vitest-report.xml`)
- playwright: 20 из 20 (`reports/playwright-report.xml`; запуск: `cd frontend && pnpm e2e` при поднятом приложении)
- корпус через API: `reports/verify_samples.md`, `reports/verify_samples.json`
- баг-баш: `reports/bug_bash.md`
- бюджеты производительности: `reports/perf_budget.md`
- прочность на чужом коде (исходники JDK): `reports/jdk_sweep.md`
- дифференциальная проверка исправлений и выделения метода (I8): `reports/fix_differential.md`, `reports/extract_differential.md`

Два теста backend требуют JDK 21+ и на более раннем JDK падают по замыслу (`test_jdk.py`, `/health`).
