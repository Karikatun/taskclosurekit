# TaskClosureKit CLI v2.1

## npm launcher

`taskclosurekit` в npm payload — дополнительная точка входа в тот же Python CLI и контракт v2. Локальный tarball запускается без Git clone:

```sh
npx --offline --yes --ignore-scripts --package=/absolute/path/taskclosurekit-2.1.1.tgz -- taskclosurekit --help
npx --offline --yes --ignore-scripts --package=/absolute/path/taskclosurekit-2.1.1.tgz -- taskclosurekit --store /absolute/path/store status --next --json
```

Версия исходников и npm payload — 2.1.1. Наличие конкретной версии в registry проверяется отдельно. Он не содержит dependencies/install hooks, не устанавливает Python и не меняет stores при загрузке. Node >=22, Python >=3.9, macOS/Linux с POSIX resource APIs и системный Git нужны заранее. Проверена только локальная macOS matrix Python 3.9.6 / Node 22.23.1 / npm 10.9.8.

`TASKCLOSUREKIT_PYTHON` задаёт явный абсолютный executable override. Иначе выбирается первый существующий `/usr/bin/python3`, затем `/bin/python3`; npm/project PATH не используется. Candidate разрешается в realpath, должен быть executable regular file; ошибка не вызывает fallback/install. Bootstrap `-I -S -B` импортирует bundled module из package parent, сохраняя cwd и argv. Эта граница защищает от подмены Python imports из caller project/PYTHONPATH/site customization, но не от владельца хоста или подменённого Node/npm/явного interpreter.

Неподдержанные Node/Python/platform/resource APIs, отсутствующий системный Git и некорректный override отклоняются до CLI mutations. Launcher/bootstrap failure возвращает exit 3; с `--json` — обычный `taskclosurekit/result/v2` envelope с `operational.status: environment_error`, `operation: unknown` и причиной `unsupported_node_version`, `unsupported_python_version`, `unsupported_platform`, `unsupported_resource_limits`, `git_unavailable`, `python_unavailable`, `invalid_python_override` либо `python_launch_failed`. Parsed CLI operations сохраняют собственный envelope и exit codes 0–4.

Python получает inherited stdin/stdout/stderr в отдельной session; Node ожидает его завершения и передаёт SIGINT/SIGTERM. Это предотвращает повторную доставку terminal SIGINT через общую process group и forwarding. Bootstrap поднимает KeyboardInterrupt при первом сигнале и игнорирует повторы во время cleanup; check runner очищает собственную process group. Отказ/прерывание подтверждения не создаёт approval. Прерывание check оставляет честный `interrupted_check`, который нельзя молча повторить. Interrupted launcher возвращает 130 для SIGINT и 143 для SIGTERM, пишет краткую причину в stderr. SIGKILL, crash Node/хоста и процессы, покинувшие process group, не получают гарантии cleanup; проверяйте persisted state после прерывания.

`authorize`, `review --human`, `close` требуют прежнего exact digest через terminal stdin; JSON и piped input не заменяют operator action. npx как launcher инструмента не разрешает npx launchers внутри project-defined check presets: их dispatch grammar сохраняется.


Локальный namespace — `python3 -m taskclosurekit`, без установки или внешних dependencies. Ни контракт, ни review JSON не исполняют произвольный shell.

## Запуск и файлы

Из корня этого проекта запускаются команды ниже. `--store` обязателен; пути repository/store/contract/review абсолютны и нормализованы. Store располагается физически вне проверяемого repository, contract/review — вне repository и store. Existing symlink components и aliases проверяются прежними safe-path helpers до создания/чтения данных; metadata источников также bound в snapshot.

Демонстрационный [контракт](../examples/contract-v2.json) относится к `/tmp/taskclosurekit-demo`, пишет только `src/foo.py` и связывает `README.md` как неизменяемый источник. Чтобы использовать его, подготовьте небольшой disposable Git repository с нейтральным `README.md`, `src/foo.py` и исходным commit, затем сохраните контракт вне этого repository, например `/tmp/taskclosurekit-contract.json`. Для реальной задачи замените task id, repository и scopes в новом contract file. Не используйте эти пути как готовый production store.

Поддержан обычный `.git` с SHA-1 loose objects; packs, alternates, linked worktrees, unsupported Git controls и неполные snapshots fail-closed. Все [limits/path/source/Git rules](SAFETY_BOUNDARIES.md) сохраняются. Sources/read/write — точные относительные файлы или поддеревья, globs не поддерживаются. Read scope декларативен и не является host sandbox.

## Основной цикл

```sh
python3 -m taskclosurekit --store /tmp/taskclosurekit-store task create /tmp/taskclosurekit-contract.json --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store authorize --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store baseline --json
# Внесите только разрешённые изменения, затем staged-ите их средствами Git.
python3 -m taskclosurekit --store /tmp/taskclosurekit-store check git-index-whitespace-v1 --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store evaluate --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store review --human --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store evaluate --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store close --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store status --next --json
```

`task create` записывает DRAFT и contract digest; он не подтверждает authority автоматически. `authorize`, `review --human` и `close` показывают в stderr local-operator prompt с exact action digest. Оператор читает относящееся к этому действию состояние и вводит digest точно. Для подтверждения stdin должен быть terminal; noninteractive bypass (`--yes`, env flag или предоставление human trust в JSON) отсутствует. `--json` делает вывод машиночитаемым, но не отменяет подтверждение. Semantic authority class — `operator_confirmed`, identity — `unverified`; legacy `human` spelling сохраняется. У local-operator source `host_operator_assumed=true`, `identity_verified=false`; доказательство human identity или независимости из терминала не возникает.

`baseline` фиксирует исходное разрешённое состояние после authorize. `check PRESET_ID` реально выполняет выбранный trusted preset и создаёт bound measured_local evidence. Builtin `git-index-whitespace-v1` остаётся fixed staged whitespace check. Project presets добавляются только external config при CREATE, затем definition и registry digest фиксированы. Contract не меняет command/args, cwd rules, environment или limits.

`evaluate` вычисляет текущую допустимость без запуска check и без закрытия. До обязательного review оно возвращает NOT_CLAIMABLE даже при current PASS. После review/evaluate возможен CLAIMABLE; только отдельный подтверждённый `close` сохраняет bounded Claim/Closure и CLOSED. Наличие candidate `claim` в evaluate ещё не означает recorded closure.

## Machine envelope

Внешний workflow, agent host, orchestrator или automation использует существующие `evaluate --json` и `status --next --json` и передаёт обычный [task contract `taskclosurekit/v2`](V2_CONTRACT.md#taskcontract). Trusted preset configuration передаётся отдельно при CREATE; contract, configuration и store соблюдают описанные выше path boundaries. Специальных полей consumer, отдельного API, RPC, SDK или сетевого adapter нет. Proposal/spec/design, decomposition и выбор моделей или subagents остаются ответственностью внешнего workflow; TaskClosureKit оценивает только configured acceptance для exact bindings.

Все существенные команды поддерживают `--json`; текущий CLI печатает JSON envelope и без этого флага. Schema — `taskclosurekit/result/v2`.

| Поле | Как читать |
| --- | --- |
| `operation`, `task_id` | Операция и task transaction |
| `operational.status` | `ok`, `invalid_input`, `environment_error`, `internal_error` |
| `state` | Confirmed/projected lifecycle или BLOCKED/STALE/INVALID |
| `decision` | `CLAIMABLE`, `NOT_CLAIMABLE`, `NOT_EVALUATED` |
| `reasons` | Machine reason codes, без необходимости парсить prose |
| `freshness` | Список `{id, state}` для evidence applicability |
| `next_action` | Следующий допустимый action или необходимость внешнего proof/new task |
| `contract_digest`, `snapshot`, `evidence_set` | Bindings, когда операция их вычислила |
| `evidence`, `review_present`, `independence` | Evidence/review projection при оценке текущего transaction |
| `assertions` | Отдельные agent declarations: digest/metadata, без measured evidence и raw statement |
| `claim`, `closure` | Candidate/current limited claim и отдельный persisted closure, когда существует |
| `last_confirmed` | Последнее подтверждённое событием состояние, отдельно от current projection |
| `authority` | `{class, source, identity, confirmed, read, write, presets, binding}`; semantic operator authority |
| `review` | `{verdict, trust, source, identity, independence, freshness}` или null; свойства разделены |
| `handoff` | `{task, current_state, authority, current_evidence, stale_evidence, review, claim_status, blockers, next_permitted_action}` |

`operational.status: "ok"` означает успешную обработку операции, включая вычисленное блокирующее решение. Оно не означает CLAIMABLE. `NOT_EVALUATED` у create/authorize/baseline не является одобрением claim. Unknown/stale evidence отражается freshness/reasons или отказом capture, а не общей зелёной operational отметкой.

Exit codes: **0** — успешная операция без blocked decision; **1** — policy-blocked NOT_CLAIMABLE; **2** — invalid input/state; **3** — environment/internal operational failure; **4** — state STALE либо причины `independence_unknown`/`unknown_state`. Stale/unknown решение остаётся NOT_CLAIMABLE, но имеет отдельный exit code. Совместимость описана в [руководстве](COMPATIBILITY.md). Машинная интеграция сначала проверяет operational status, затем decision/reasons. При NOT_CLAIMABLE агент продолжает только уже разрешённую работу или сообщает конкретный blocker; это не разрешение расширять scope.

## Failure branches

- Authorized preset не связан ни с одним criterion: `check` возвращает `preset_has_no_criterion`, invalid input/state с exit **2**, до любых journal events. Если нужна квитанция optional check, новый contract должен содержать optional criterion с этим preset ID.

- Изменение разрешённого input после check: historical PASS остаётся, freshness становится STALE_INPUT, evaluate не допускает claim. Повторите check для текущего staged state, затем review для нового snapshot/evidence set.
- Failed check/timeout/truncated capture или недостающее required evidence: claim blocked; устраняется причина, затем создаётся новая квитанция. Старый PASS не перекрывает более новый failed check.
- Изменение файла вне write scope: `write_scope_violation`; даже зелёный check не расширяет authority. Дальнейший шаг — осмотреть фактическое состояние и создать новый согласованный task transaction, если нужен другой scope.
- Изменение contract, source, Git controls или execution identity: authority/environment binding больше не подтверждает прежний цикл. Новый check не выдаёт новые полномочия; нужен новый task после проверки причины.
- Review относится к прошлому snapshot или evidence set: `stale_review`; повторите review только для exact current bindings.
- Policy `independence: required`, trusted provider отсутствует: `independence_unknown`, UNKNOWN и NOT_CLAIMABLE. Нельзя исправить этот blocker полем JSON или собственным assertion агента.
- Malformed/tampered/unsupported journal: операция fail-closed. Не редактируйте записи вручную и не считайте HMAC proof человеческой identity.
- После recorded closure изменение состояния не продолжает прежний claim. Сохраняется bounded historical closure; новый state требует нового task.

## Agent review import

```sh
python3 -m taskclosurekit --store /tmp/taskclosurekit-store review /tmp/taskclosurekit-review.json --json
```

Формат файла — [schema taskclosurekit/review/v2](V2_CONTRACT.md). Exact contract/snapshot/evidence-set bindings обязательны. Импорт записывает agent-attested review; `approve` в таком JSON не удовлетворяет required operator-confirmed review и не доказывает identity или PROVEN independence. Для локального confirmed review используется отдельный `review --human` path. Источники доверия выбирает trusted producer, не автор входного JSON.

## Agent assertion

После authorize/baseline можно записать отдельное утверждение агента:

```sh
python3 -m taskclosurekit --store /tmp/taskclosurekit-store attest /tmp/taskclosurekit-assertion.json --json
```

Файл assertion находится вне repository и store и имеет ровно такие поля:

```json
{
  "schema": "taskclosurekit/assertion/v2",
  "task_id": "whitespace-demo-v2",
  "statement": "Разрешённое изменение подготовлено; это заявление агента."
}
```

Statement — строка длиной 1–4096 символов. Journal/status сохраняет только statement digest, canonical `agent-import` / `agent_attested`, contract/snapshot bindings, sequence и timestamp; raw statement не сохраняется и не возвращается. `assertions` отделены от `evidence`: наличие assertion не удовлетворяет criterion, не меняет trust и не закрывает transaction. Digest не гарантирует анонимность исходного текста. Не помещайте secrets, `.env`, tokens или персональные данные в contract, review, assertion и другие входные файлы; отсутствие raw statement в journal не защищает сам внешний input file.

## Restart и handoff

```sh
python3 -m taskclosurekit --store /tmp/taskclosurekit-store status --next --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store resume --json
```

`resume` — compatibility alias текущего status projection v2. Новый процесс читает журнал, проверяет integrity/semantic replay, заново сопоставляет bindings с текущим состоянием и показывает current/stale evidence, missing requirements через reasons, last_confirmed и next_action. Обе команды только читают transaction: не повторяют check, не записывают evaluation event и не replay-ят transcript.

Interrupted check не повторяется автоматически и блокирует claim до осмотра состояния и нового transaction. `status --next` возвращает action id, а не agent plan; это closure recovery, не memory system. Real CI/network adapter, SDK, completion hook, installer и deployment отсутствуют.

## Real engineering checks

```sh
python3 -m taskclosurekit --store /tmp/taskclosurekit-store task create /tmp/taskclosurekit-contract.json --preset-config /tmp/taskclosurekit-presets.json --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store authorize --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store baseline --json
# Внесите только разрешённое изменение.
python3 -m taskclosurekit --store /tmp/taskclosurekit-store check project-tests --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store check project-typecheck --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store check project-build --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store evaluate --json
```

IDs должны входить в contract execution authority и external trusted registry. [Config schema](V2_CONTRACT.md) задаёт exact executable/argv/resources/inputs. [Engineering contract](../examples/contract-engineering-v21.json) и [preset template](../examples/presets-engineering-v21.json) не выполняются без адаптации runtime paths и нейтрального repository. Скопируйте [engineering fixture](../examples/engineering-fixture/README.md) в disposable Git repository с исходным commit; исходный `checks.py` и definitions фиксируются до CREATE. Определение compiler/SDK paths и indirect runtime dependencies остаётся ответственностью trusted configuration. Operator проверяет definitions до CREATE/authorize. Config не помещается в read/write task tree или store.

Изменённая config или dispatcher: STALE_AUTHORITY, next action требует нового task; rerun не подтверждает новую authority. Executable/runtime drift: STALE_ENVIRONMENT; source/tests/manifests/lock/config inputs: STALE_INPUT. Conservative snapshot может инвалидировать другие evidence. Declared check outputs разрешены только внутри одновременного preset и contract scope; отсутствие shell или post-run check не означает runtime sandbox.

Tests/typecheck/build PASS независимо удовлетворяют выбранные criteria; перед close всё равно требуется current exact review, сохранённая authority и permitted scope. Stale review после rerun требует нового review. `status --next`/`resume` показывают task, current state, authority, current/stale evidence, review, claim blockers и next action без transcript. Внешний workflow, agent host, orchestrator или automation читает эти fields как обычный consumer.

### Conservative output invalidation

Snapshot включает разрешённые generated outputs; они не исключаются автоматически. Если build впервые создаёт `.build/foo.o` после tests, предыдущий tests PASS может стать STALE_INPUT даже при неизменном source. Тогда tests нужно повторить на состоянии с build output; byte-identical повторный output не меняет snapshot. При дальнейших writes возможно снова потребуется recheck. Это намеренная conservative applicability, не вычисление точного dependency graph. Claim разрешён лишь когда все required receipts и review относятся к текущим bindings.

### Launcher validation и cleanup

При CREATE argv проверяется по [ограниченному dispatch grammar](V2_CONTRACT.md#поддержанный-dispatch-grammar): Python flags/operands, explicit local module/package bindings, Node loaders и Bun/package dispatch. Unknown/ambiguous flags, missing launch targets, bare Node loader packages и неподдержанные shell/env/npx launchers отклоняются как invalid input с exit **2**. Local `-m` target не должен допускать создание competing package `__init__.py` через write scope; `-I`/`-P` + `-m` отклоняются. Named package `run` требует string `scripts[name]`, trailing script args — explicit `--`; `--cwd` поддержан только Bun. Не заменяйте отказ новым runtime field во входном contract: definitions принадлежат отдельно подтверждаемой config.

Runner очищает собственную process group и при успешном завершении leader до post-run snapshot. Это не sandbox и не гарантия против процессов, покинувших группу.
