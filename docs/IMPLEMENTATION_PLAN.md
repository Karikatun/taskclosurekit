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

Slice A проверяет bug fix через tests + typecheck + exact review → CLAIMABLE → отдельный close; новое source изменение делает tests stale. Slice B требует tests AND build: missing build блокирует, оба PASS лишь потенциально разрешают claim, build config изменение инвалидирует evidence, rerun инвалидирует старый review. Slice C меняет файл за пределами write authority: все PASS не перекрывают `write_scope_violation`. Эти disposable synthetic fixtures выполняют реальные процессы на нейтральном C коде: Clang собирает и запускает regression, `-Werror -fsyntax-only` проверяет типы и object build пишет разрешённую `.build` область; они не являются настоящим запуском checks [Нейтральный пример](WEB_APP_TEMPLATE_REFERENCE.md).

Дополнительные обязательные invariants: changed config/dispatcher не self-authorizes после CREATE; registry definition drift означает STALE_AUTHORITY; runtime drift — STALE_ENVIRONMENT; explicit input drift — STALE_INPUT; PASS одного preset не удовлетворяет другого; новый FAIL важнее старого PASS; required UNKNOWN independence блокирует и при реальном check flow; renderer не повышает trust.

Обновлённый [CLI JSON interface](CLI.md#machine-envelope) для внешнего workflow, agent host, orchestrator или automation должен сохранять structured `decision/state/reasons/next_action`, различать operational failure, blocked decision и stale/unknown. [Совместимость](COMPATIBILITY.md) документирует compatibility. Фактически выполненные tests, reviewer evidence и commit отражаются отдельным финальным отчётом: этот документ не превращает план в PASS.

### Conservative output invalidation — strict fallback

Snapshot включает разрешённые generated outputs; они не исключаются автоматически. Если build впервые создаёт `.build/foo.o` после tests, предыдущий tests PASS может стать STALE_INPUT даже при неизменном source. Тогда tests нужно повторить на состоянии с build output; byte-identical повторный output не меняет snapshot. При дальнейших writes возможно снова потребуется recheck. Это намеренная conservative applicability, не вычисление точного dependency graph. Claim разрешён лишь когда все required receipts и review относятся к текущим bindings.

## Локальные проверки интеграционного среза, 2026-10-13

### Исправления после review round 2

Public final-capture regression сначала дала три ложных CLOSED: отдельная замена dependency root inode, отзыв proof и изменение provenance во время последнего capture. Теперь host guard удерживается на весь capture/check/required append; core проверяет current physical/provenance/live state/time после capture и перед append без новых callbacks. Неполная capability, subclass/property live state, expired/invalidation guard блокируются, interruption освобождает core mutex. Positive guarded reuse сохраняет **0 dependency-byte reads** на два unchanged evaluate при явно заданном trusted-host enforcement; production OS provider отсутствует. Mutex и test adapter сами не доказывают защиту внешних writers.

Signed private store regression подтвердила приём unaudited delegated close; replay теперь требует STARTED, CONSUMED и exact `consume_audit: true`, совпадение authorization/transaction/body/sequence. Valid delegated close и отдельная manual operator history сохраняются; pending/refused attempts не consume повторно. Исторические промежуточные тестовые fixtures с неверным filename/mode не засчитываются как semantic RED; валидный store RED и последующий GREEN сохранены отдельно.

Stable full suite на неизменённых final source/tests: **267 tests, 392.217 s, OK (skipped=1)**. Skip: case-insensitive filesystem не позволяет создать отдельный reserved case alias. Текущий пилот: Python **4.027424 s**, **18,446,123** logical hash-read bytes / **2,838** завершённых reads; C **8.603406 s**, **7,869,698,015** bytes / **4,010** reads. Negative gates: 3 Python, 4 C; false closes **0**. Бюджеты 120 s / 512 MiB Python / 8 GiB C соблюдены. Эти counters не являются physical disk IO; большой compiler runtime rehash остаётся ограничением. Owning docs уточняют поддержку packed/linked Git и configured v2 output/capture limits при сохранённом strict fallback. Новый независимый review и локальный commit остаются отдельными gates.

### Исторические проверки перед review round 2

После независимого review round 1 исправлены четыре подтверждённых границы: final callback writer при close, suffix quoted credentials в diagnostics, отсутствие положительного immutable dependency reuse path и несогласованный output/capture file limit. Public RED→GREEN regressions сохранены вне repository. Shared-contract suite перед review round 2: **261 tests, 374.348 s, OK (skipped=1)**; source/tests не менялись во время запуска. Skip тот же: case-insensitive filesystem не позволяет создать отдельный reserved case alias. Последующий review обнаружил недостаточный final immutable-root guard и допускаемый unaudited delegated event; этот результат сохранён как исторический, он не подтверждает исправленные final bytes.

Пилот перед review round 2: Python **4.018877 s**, **18,044,260** logical hash-read bytes / **2,815** завершённых file reads; C **8.582529 s**, **7,869,243,721** bytes / **3,984** reads. Negative gates: 3 Python, 4 C; false closes **0**. Бюджеты 120 s / 512 MiB Python / 8 GiB C сохранены. Это logical hash IO текущего процесса, не physical disk IO.

Отдельная synthetic immutable-root boundary проверяет initial material bytes/provenance, включая hidden/ignored files, typed authenticated complete immutable lease и отсутствие доверия от JSON/HMAC/stat/permissions. Два unchanged evaluate не читают dependency bytes повторно: **0 reads**, тогда как первоначальный 2 MiB rehash repro давал четыре чтения / 8 MiB. Positive path, 9 MiB material input, revoked/stale/incomplete/forged proof, signed integer booleans, escaping link и неполный capture проверены. Это результат при явно заданном trusted-host enforcement; in-process test adapter не устанавливает OS immutability и не является production provider. Default environment без proof остаётся UNKNOWN.

Real 9 MiB generated output проходит lifecycle при согласованных trusted writer/capture bounds; overlimit output получает FAIL, unknown coverage и escaping links блокируются. Close consume callback writer/final host callback writer, expiry после consume и interrupted consume не дают CLOSED: durable STARTED/CONSUMED/REFUSED audit не позволяет silent retry. Поддержанные quoted/escaped/truncated credential values проверены по реально сохранённым journal diagnostics.

### Исторические проверки перед review round 1

Full source suite до последнего узкого attributes fix: 244 tests, 348.498 секунды, `OK (skipped=1)`. Skip: case-insensitive filesystem не позволяет создать отдельный reserved case alias. Команда: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v` через обязательный RTK, с изолированным внешним TMPDIR. После RED→GREEN public attributes regression затронутые integration/stage/pilot, execution-policy и remediation suites обновлены: **41 tests, 58.030 s, OK**. Это сохранённые исторические результаты до следующих четырёх fixes, а не подтверждение final bytes.

| Приёмка | Фактическая граница |
| --- | --- |
| Packs/worktree/hooks | `test_git_compatibility`: real clone packs, linked backlinks, действующие default/external hooks без dispatch; unsupported formats и hook drift/race блокируются |
| Длинный runner/toolchain | `test_execution_policy`: real запуск более 5 секунд, finite policy, explicit PATH/runtime binding, no ambient secrets, redaction/output-loss и cleanup |
| Outputs/build inputs/stages | `test_integration_contract`: generated outputs не stale unrelated checks, build input stales+rерun, precommit→staged требует нового review, equivalent commit сохраняет tests, unrelated refs блокируются |
| Review/authority | Required host identity/context/creator proof; отсутствие provider и identity strings блокируют; expired/revoked/self-issued/replayed capabilities не закрывают task |
| CI | `test_v2_external`: exact clean commit/run/runtime/inputs/freshness binding; JSON success без runtime proof не становится measured |
| Scope/storage/recovery | Hidden ignored external write, mode/delete/link, controls, missing/failed/stale checks, HMAC tamper, strict signed semantic replay, concurrent store lock и interruption/recovery отрицательные случаи |
| Read-only consumer | Повторные evaluate/status/resume сохраняют journal bytes, не исполняют checks и не consume authority |

Один end-to-end Python срез проверен прежде пилота. Затем два disposable fixtures использовали существующие Python и C toolchains; прикладные проекты не менялись, installations и сеть не добавлялись. Исторический пилот после attributes fix, перед review round 1: Python **4.140924 s**, **16,503,542** logical hash-read bytes / **2,621** завершённое file read; C **8.815711 s**, **7,576,892,579** bytes / **3,744** reads. Negative gates в пилоте: Python 3, C 4; false close **0**. Это не статистическое доказательство отсутствия всех ошибок.

Counters охватывают только завершённые bounded regular-file reads текущего процесса. Это не physical disk IO; Git/compiler children и незавершённые чтения не включены. Первый C запуск не прошёл общий 512 MiB budget: 5,536,631,622 bytes за 5.800 s. Финальный бюджет — 120 s на fixture, 512 MiB Python / 8 GiB C, обоснован размером selected installed compiler и конечным числом capture/rerun. Большой повторный runtime-byte rehash остаётся ограничением: для compiler в пилоте immutable proof не предоставляется. Production immutable-root/completeness provider отсутствует; поддержанная host boundary проверена отдельно при явно заданном test trust assumption. Измеренного процента ускорения нет.

Один рабочий день — максимальный timebox пилота; фактически наблюдались указанные секунды, длительная эксплуатация весь рабочий день не проверялась. Host/CI providers в tests синтетические и не подтверждают production identity или remote run. Independent final security/contract review и локальный commit являются отдельными gates и отражаются итоговым отчётом.


## Контракт интеграции

[Контракт интеграции](INTEGRATION.md)
