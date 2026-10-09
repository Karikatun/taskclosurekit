# Проверяемый срез TaskClosureKit v2.1

## Область

Текущий срез использует строгие private primitives. Один claim `configured-acceptance-satisfied`, builtin `git-index-whitespace-v1` и минимальные project tests/typecheck/build capabilities, local HMAC journal, отдельное authority/review/closure confirmation и versioned JSON CLI. Новые зависимости, сетевые integrations, installer и release не нужны.

[Границы безопасности](SAFETY_BOUNDARIES.md) описывают поддержанные IO/path/Git/resource limits. [Архитектура](ARCHITECTURE_V2.md) задаёт текущие модули. [Product boundaries](PRODUCT_BOUNDARIES.md) ограничивают scope.

## Vertical slices и условия

| Срез | Проверяемый результат |
| --- | --- |
| Основной flow | Контракт принят → operator authority подтверждена → baseline создан → trusted preset реально запущен → evidence привязано к exact snapshot → review → evaluate даёт CLAIMABLE → отдельный authorized close сохраняет bounded Claim/Closure |
| Stale input | Разрешённое изменение после PASS сохраняет исторический результат, но делает evidence STALE_INPUT и блокирует claim; повторный check/review восстанавливает current applicability |
| Scope violation | Изменены разрешённый `src/foo.py` и неразрешённый `README.md`: даже PASS не устраняет `write_scope_violation` |
| Unknown independence | Review присутствует, policy требует independence, trusted proof отсутствует: UNKNOWN и NOT_CLAIMABLE |
| Handoff | Новый процесс читает current/stale evidence, missing requirements и next action из persisted transaction без transcript |
| External evidence seam | Fake adapter принимает measured_ci только после repository/commit/exact snapshot/workflow/check/criterion binding; никаких network calls |

## Обязательные invariants

1. Agent assertion не становится measured/human evidence; downstream presentation не меняет trust.
2. Evidence не расширяет authority, review не меняет acceptance.
3. Historical PASS со stale/unknown freshness не удовлетворяет required criterion.
4. Changed authority или execution environment инвалидирует dependent evaluation.
5. Out-of-scope write блокирует closure.
6. Review относится к exact contract/snapshot/evidence set; изменение любого binding блокирует применимость.
7. Required independence + UNKNOWN блокирует, JSON assertion не устанавливает PROVEN.
8. CLAIMABLE не закрывает task автоматически; close отдельно сохраняет конкретный limited claim.
9. Неизвестное состояние, malformed/tampered/unsupported records fail-closed.
10. Domain не импортирует CLI/IO; контракт не определяет executable/shell.
11. Предыдущие task schemas и lifecycle не принимаются; HMAC storage envelope не смешивается с task contract.

## Проверки

Реальные CLI integration tests выполняются на одноразовых нейтральных Git fixtures с исходным commit и loose objects. Они запускают trusted preset, проверяют negative branches, fresh process handoff и persistence. Domain tests проверяют composition/trust/claim invariants; architecture tests — границы imports. Safety regression suite проверяет path/snapshot/runner/integrity behavior текущего application и CLI. Тесты не читают реальные secrets и не запускают чужие project scripts.

Общий локальный runner без создания bytecode:

```sh
python3 -m unittest discover -s tests -v
```

При запуске в агентской среде применяется обязательный локальный RTK wrapper и `PYTHONDONTWRITEBYTECODE=1`. Фактические результаты, platform-specific skips и независимый review относятся к конкретному проверенному состоянию и указываются в отчёте. План не утверждает, что они уже выполнены.

## Definition of Done

Новая модель реально используется CLI; реальный end-to-end flow и все negative invariants проходят; security regression не выявляет ухудшения; обязательный review выполнен для точного состояния. JSON envelope различает operational status и claim decision, machine consumer не парсит prose. Документы отражают actual CLI и trust limits; версия и публикация требуют самостоятельного решения и фактических проверок.

Это local readiness. Commit, push, CI, publication и deployment имеют собственные подтверждения и не следуют из тестов. В первом срезе не реализуются SDK/hook, general executor, policy DSL, memory, UI/server/database, network CI adapters, public attestations или installer.

## Real engineering checks

Slice A проверяет bug fix через tests + typecheck + exact review → CLAIMABLE → отдельный close; новое source изменение делает tests stale. Slice B требует tests AND build: missing build блокирует, оба PASS лишь потенциально разрешают claim, build config изменение инвалидирует evidence, rerun инвалидирует старый review. Slice C меняет файл за пределами write authority: все PASS не перекрывают `write_scope_violation`. Эти disposable synthetic fixtures выполняют реальные процессы на нейтральном C коде: Clang собирает и запускает regression, `-Werror -fsyntax-only` проверяет типы и object build пишет разрешённую `.build` область; они не являются настоящим запуском checks [web-app-template](WEB_APP_TEMPLATE_REFERENCE.md).

Дополнительные обязательные invariants: changed config/dispatcher не self-authorizes после CREATE; registry definition drift означает STALE_AUTHORITY; runtime drift — STALE_ENVIRONMENT; explicit input drift — STALE_INPUT; PASS одного preset не удовлетворяет другого; новый FAIL важнее старого PASS; required UNKNOWN independence блокирует и при реальном check flow; renderer не повышает trust.

Обновлённый [CLI JSON interface](CLI.md#machine-envelope) для внешнего workflow, agent host, orchestrator или automation должен сохранять structured `decision/state/reasons/next_action`, различать operational failure, blocked decision и stale/unknown. [Совместимость](COMPATIBILITY.md) документирует compatibility. Фактически выполненные tests, reviewer evidence и commit отражаются отдельным финальным отчётом: этот документ не превращает план в PASS.

### Conservative output invalidation

Snapshot включает разрешённые generated outputs; они не исключаются автоматически. Если build впервые создаёт `.build/foo.o` после tests, предыдущий tests PASS может стать STALE_INPUT даже при неизменном source. Тогда tests нужно повторить на состоянии с build output; byte-identical повторный output не меняет snapshot. При дальнейших writes возможно снова потребуется recheck. Это намеренная conservative applicability, не вычисление точного dependency graph. Claim разрешён лишь когда все required receipts и review относятся к текущим bindings.
