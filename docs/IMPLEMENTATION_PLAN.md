# Проверяемый срез TaskClosureKit v2

## Область

Staged migration заменяет product semantics, сохраняя строгие primitives v1. Один claim `configured-acceptance-satisfied`, одна исполняемая capability `git-index-whitespace-v1`, local HMAC journal, отдельное authority/review/closure confirmation и versioned JSON CLI. Новые зависимости, сетевые integrations, installer и release не нужны.

Исходный v1 context сохранён в [CLI v1](CLI_V1.md). [Архитектурная карта](ARCHITECTURE_V2.md) задаёт Keep/Extract/Replace/Deprecate/Delete later. [Product boundaries](PRODUCT_BOUNDARIES.md) ограничивают scope.

## Vertical slices и условия

| Срез | Проверяемый результат |
| --- | --- |
| Основной flow | Контракт принят → human authority подтверждена → baseline создан → trusted preset реально запущен → evidence привязано к exact snapshot → review → evaluate даёт CLAIMABLE → отдельный authorized close сохраняет bounded Claim/Closure |
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
11. Legacy CLI сохраняет отдельную схему, v1 journals не принимаются v2.

## Проверки

Реальные CLI integration tests выполняются на одноразовых нейтральных Git fixtures с исходным commit и loose objects. Они запускают trusted preset, проверяют negative branches, fresh process handoff и persistence. Domain tests проверяют composition/trust/claim invariants; architecture tests — границы imports. Legacy regression suite проверяет сохранение path/snapshot/runner/integrity behavior. Тесты не читают реальные secrets и не запускают чужие project scripts.

Общий локальный runner без создания bytecode:

```sh
python3 -m unittest discover -s tests -v
```

При запуске в агентской среде применяется обязательный локальный RTK wrapper и `PYTHONDONTWRITEBYTECODE=1`. Фактические результаты, platform-specific skips и независимый review относятся к конкретному проверенному состоянию и указываются в отчёте. План не утверждает, что они уже выполнены.

## Definition of Done

Новая модель реально используется CLI; реальный end-to-end flow и все negative invariants проходят; legacy/security regression не выявляет ухудшения; обязательный review выполнен для точного состояния. JSON envelope различает operational status и claim decision, machine consumer не парсит prose. Документы отражают actual CLI и trust limits; version bump допустим лишь после полного working slice и migration path.

Это local readiness. Commit, push, CI, publication и deployment имеют собственные подтверждения и не следуют из тестов. В первом срезе не реализуются SDK/hook, general executor, policy DSL, memory, UI/server/database, network CI adapters, public attestations или installer.
