# Versioned JSON contract и evidence v2

## TaskContract

Схема — строка `taskclosurekit/v2`. Все поля текущей схемы обязательны; неизвестные поля отклоняются. Минимальный пример находится в [examples/contract-v2.json](../examples/contract-v2.json). Это JSON, не YAML.

| Поле | Смысл и первый срез |
| --- | --- |
| `task.id`, `task.title` | Идентификатор transaction и краткое описание |
| `repository` | Абсолютный нормализованный путь к supported Git repository |
| `authority.task.issuer` | Только `human`; контракт создаётся отдельно от authority confirmation |
| `authority.read` | Непустой список относительных файлов/поддеревьев; декларативная область, не host read sandbox |
| `authority.write` | Относительные файлы/поддеревья разрешённых изменений; может быть пустым |
| `authority.execution.presets` | Выбор известных builtin/project trusted presets только по ID |
| `sources` | Непустой список обычных файлов внутри read scope, связанных с authority и snapshot |
| `acceptance` | Отдельные criteria `{id, required, evidence}`; минимум один required |
| `review` | `{required: boolean, independence: "not_required" или "required"}` |
| `claim.type` | Только `configured-acceptance-satisfied` |
| `closure.authority` | Только `human`; отдельное подтверждение при close |

Task/criterion id: `[a-z0-9][a-z0-9_.-]{0,63}`. Title — 1–256 символов без control characters. Scope/source paths — до 1024 символов, до 100 записей в списке; без symlink, absolute path, `.`, `..`, пустых компонентов, backslash и `.git` в любом регистре. Globs (`*`, `?`) не поддерживаются: `src` означает поддерево, `src/foo.py` — точный путь. Источники должны точно совпадать с actual spelling обычных файлов, включая index binding; они неизменяемы внутри transaction, даже если входят в write scope.

Execution authority может содержать preset без criterion, но его `check` отклоняется с `preset_has_no_criterion` до CHECK_STARTED и любых записей journal. Для сохраняемой квитанции добавьте criterion (при необходимости optional) в новый согласованный contract до CREATE; authority сама по себе не задаёт acceptance binding.

Criteria имеют уникальные id; `evidence` содержит ровно одно из `all_of` или `any_of` с непустым списком preset ids из execution authority. `all_of` требует current PASS для всех references, `any_of` — хотя бы для одного. Необязательный criterion не блокирует claim; хотя бы один required criterion обязателен. `independence: required` нельзя сочетать с `review.required: false`.

Контракт не задаёт shell, executable/argv, implementation plan, decomposition, модель агента, chain of thought или orchestration. Его текст и source content — данные; они не предоставляют новых полномочий.

## Trusted preset

Registry сохраняет builtin `git-index-whitespace-v1` и принимает внешнюю project configuration только при `task create INPUT --preset-config ABS`. Config физически вне repository/store, contract не задаёт его определения. Формат strict JSON — `taskclosurekit/presets/v1`, объект с `schema` и массивом `presets`.

Каждое определение задаёт `id`, абсолютный canonical regular `executable`, `argv`, `cwd_rule: contract-repository`, literal `environment_allowlist`, `timeout` (не более 5 секунд), `output_limit` (не более 65536 bytes), `permitted_writes`, `relevant_inputs` с категориями `source`, `tests`, `manifests`, `lockfiles`, `config`, а также `authority_inputs` и абсолютные `runtime_inputs`. Environment names ограничены `PATH`, `LC_ALL`, `LANG`, `PYTHONDONTWRITEBYTECODE`, `CI`, `NODE_NO_WARNINGS`, `TMPDIR`; объявленный PATH равен `/usr/bin:/bin`, TMPDIR — относительный путь внутри permitted writes. Executable/runtime inputs физически вне repository/store. Resource identity имеет отдельные bounds: 384 MiB на файл, 1 GiB суммарно, 32 файла и 10 секунд capture; repository limits не увеличены. Это policy execution definition, не plugin framework.

Relevant input declaration непустая; relevant/authority repository paths входят в read scope. Permitted writes входят в contract write scope и не пересекаются с relevant/authority inputs. Authority inputs не могут входить в task write scope. Прямые repository launchers и package `run` dispatcher manifest должны быть объявлены authority inputs; indirect imports и полный command/dependency graph остаются ответственностью trusted configuration, автоматического discovery нет. Resource-file hashes не удостоверяют всю установку compiler/SDK или supply-chain provenance. Config identity, definitions digest и authority input bindings фиксируются при CREATE. Authorize подтверждает authority с registry binding. Изменение config/dispatcher не может self-authorize command внутри transaction: оно означает STALE_AUTHORITY и требует нового task. Executable/runtime drift означает STALE_ENVIRONMENT. Изменение обычных объявленных inputs означает STALE_INPUT; unknown applicability блокирует required criteria.

Runner v2 — bounded adapter без shell; используются фиксированные argv/environment, timeout/output/resource limits и snapshot до/после. Разрешённые check outputs проверяются по объявленным writes и contract scope (file bound 8 MiB), без объявления OS sandbox. Builtin остаётся staged whitespace check:

```text
git --no-pager diff --cached --check --no-ext-diff --no-textconv HEAD --
```

[Engineering examples](../examples/contract-engineering-v21.json) требуют адаптации paths и trusted definitions до CREATE. Проверки tests/typecheck/build независимо формируют measured evidence; PASS одной capability не заменяет другую.

## Snapshot и freshness

RepositorySnapshot, AuthoritySnapshot и ExecutionSnapshot объединяются в Snapshot digest. Capture учитывает repository files/index/HEAD/Git controls, source bindings, contract bytes, программу/registry/runner/executable и ограниченное environment. Инвентаризация repository консервативна и не ограничена read scope; это проверка применимости и write changes, не система запрещённых чтений.

| Freshness | Интерпретация |
| --- | --- |
| `CURRENT` | Все relevant bindings совпадают с текущим supported состоянием |
| `STALE_INPUT` | Repository/input snapshot изменился после evidence |
| `STALE_ENVIRONMENT` | Execution/environment identity изменилась |
| `STALE_AUTHORITY` | Contract/authority identity изменилась |
| `UNKNOWN` | Текущая применимость не установлена; такая информация не допускает required claim |

Freshness вычисляется относительно текущего snapshot, не заменяет исторический `result`. Historical PASS со stale freshness больше не удовлетворяет required criterion. UNKNOWN входит в fail-closed vocabulary; неполный или неподдержанный capture может завершить операцию ошибкой до вычисления usable evidence. Ни такой отказ, ни отсутствие freshness не означают CURRENT.

## Evidence и источники

Evidence имеет id, type (preset id), source/trust class, contract digest, typed Snapshot, criterion bindings, result (`PASS`/`FAIL`), sequence и timestamp. Freshness возвращается отдельной current projection. Источник доверия назначает trusted producer, а не импортированное поле JSON.

| Trust class | Источник и предел |
| --- | --- |
| `observed` | `repository-observer`: наблюдение; не успешный measured check |
| `agent_attested` | `agent-import`: assertion/review агента; не measured/human evidence |
| `measured_local` | `local-preset`: runner выполнил выбранную trusted capability |
| `measured_ci` | `ci-adapter`: seam с проверкой repository/commit/exact snapshot/workflow/check/criterion; CI HEAD PASS не доказывает dirty tree/index; fake в tests, production adapter отсутствует |
| `human_confirmed` | Compatibility spelling; semantic authority `operator_confirmed`, `local-operator`, identity `unverified`, terminal exact-digest confirmation |

Числовые trust scores не используются. Source id и class должны соответствовать доверенной таблице. Agent-import, renderer или downstream API не получают право повысить trust. Measured evidence не расширяет read/write/execution authority. Форма fake CI adapter не удостоверяет реальный workflow. Public JSON import measured_ci отсутствует; future adapter обязан доказать соответствие commit exact repository snapshot, а не прикладывать HEAD PASS к другим worktree/index bytes.

## Отдельные AgentAssertion

`attest INPUT` принимает `taskclosurekit/assertion/v2` с `{schema, task_id, statement}` после baseline. В journal хранится отдельный AgentAssertion: id, statement digest, `agent-import`/`agent_attested` source, contract/snapshot digests, sequence и timestamp. Raw statement не сохраняется; assertion не включается в measured evidence set и не удовлетворяет criterion. Status показывает assertions отдельно от evidence. Digest не является гарантией анонимности; внешний input file остаётся ответственностью оператора.

## Review

Review binding включает contract digest, exact snapshot digest, relevant evidence-set digest, verdict, source, independence и sequence. Relevant evidence set связан с актуально выбранными measured receipts, их sequence/results/snapshots; новый check после review может потребовать нового review даже при тех же файлах.

Импортированный файл имеет ровно такой формат:

```json
{
  "schema": "taskclosurekit/review/v2",
  "task_id": "whitespace-demo-v2",
  "contract_digest": "<digest из evaluate>",
  "snapshot_digest": "<snapshot из evaluate>",
  "evidence_set": "<evidence_set из evaluate>",
  "verdict": "approve"
}
```

Вердикт — `approve` или `reject`. Импорт остаётся agent-attested и не удовлетворяет обязательному trusted operator-confirmed review. JSON не содержит предоставляемых автором `trust`, `identity`, `independence` или human confirmation: такие поля не создают разрешение и отклоняются схемой. Отдельный `review --human` требует local terminal confirmation.

Четыре свойства независимы: verdict (`approve`/`reject`), source trust, independence и freshness exact bindings. `approve` + operator-confirmed + CURRENT + UNKNOWN не удовлетворяет required independence.

Independence имеет `NOT_REQUIRED`, `UNKNOWN`, `PROVEN`. В текущей среде provider доказанной независимости отсутствует: required policy → UNKNOWN → NOT_CLAIMABLE, независимо от существования agent/operator-confirmed review. LocalOperator не является identity/independence provider.

## Evaluation, Claim, Closure

Evaluation содержит state, decision, reasons, freshness, evidence-set binding, independence, candidate Claim и next action. `CLAIMABLE` — вычисляемая допустимость текущего claim, не закрытие. Closure отдельно сохраняет Claim, authority source, action binding, sequence и `identity_verified=false`.

Claim фиксирует type `configured-acceptance-satisfied`, contract digest, exact snapshot, evidence set, required criteria и limitations. Он не доказывает universal correctness, отсутствие багов, general security или production readiness и не разрешает deploy. Closure authority не может закрыть current NOT_CLAIMABLE. После изменения закрытого состояния нужен новый task transaction.

## Persistence и совместимость

EvidenceStore имеет append/read/verify boundary; LocalHmacStore использует private HMAC chain с versioned v2 domain events и строгим semantic replay. Неподдержанные schemas/events, malformed/tampered records и несогласованные bindings не становятся valid state. Ключ хранится локально вместе с данными; HMAC не является remote attestation, human identity или защитой от malicious equivalent host authority.

Числовая schema 1 task contract и прежний lifecycle не поддержаны. HMAC envelope schema 1 сохраняется для текущих domain events; automatic importer отсутствует. Paths и capture/resource limits описаны отдельно: [детали snapshot/path/Git limits](SAFETY_BOUNDARIES.md). Это совместимость primitives, не обещание сохранения evidence applicability после обновления программы.

## Real engineering checks

Criterion A может требовать tests, B — typecheck, C — tests AND build (`all_of`). Каждый required criterion требует собственных current PASS references; optional criterion не блокирует. Более новый FAIL не скрывается старым PASS. Review после нового check привязывается к новому exact evidence set. Config/input/executable identities устанавливают применимость, а не качество проверки. Tests/typecheck/build остаются ограниченными проверками configured properties; claim не является universal proof.

[Совместимость](COMPATIBILITY.md) отделяет schema compatibility, operator semantics и changed exit codes.

### Conservative output invalidation

Snapshot включает разрешённые generated outputs; они не исключаются автоматически. Если build впервые создаёт `.build/foo.o` после tests, предыдущий tests PASS может стать STALE_INPUT даже при неизменном source. Тогда tests нужно повторить на состоянии с build output; byte-identical повторный output не меняет snapshot. При дальнейших writes возможно снова потребуется recheck. Это намеренная conservative applicability, не вычисление точного dependency graph. Claim разрешён лишь когда все required receipts и review относятся к текущим bindings.

## Поддержанный dispatch grammar

CREATE проверяет явные поддержанные формы launcher argv; первый operand не угадывается эвристикой. Для Python поддержаны перечисленные короткие flags/clusters `bBdEiIOPqsSuv`, отдельные/attached operands `-W`, `-X`, `-c`, `-m`, `--` перед script и `--check-hash-based-pycs`. Direct repository script должен существовать и быть immutable authority input; внешний script — явно bound runtime file. `-m` разрешает только существующий локальный `.py` либо package с явно связанными `__init__.py`/`__main__.py`. Missing/ambiguous module target отклоняется; write scope не может разрешать будущий competing package `__init__.py`, который подменил бы выбранный module file. Site/global module resolution не поддержан; сочетание `-I`/`-P` (включая clusters) с `-m` отклоняется, поскольку local resolution меняется. Direct script с этими flags по-прежнему требует binding.

Node поддерживает фиксированный список options; preload/import/loader принимает только явный `./file` или canonical absolute file с binding, без bare package specifiers. Loader options проверяются и после inline eval. Bun test/build поддерживают ограниченные flags и `--cwd`; loader/config/plugin targets связываются явно. Existing implicit `bunfig.toml` должен быть authority input; возможность создать его через write scope при исходном отсутствии отклоняется. Named package `run` требует существующий string `scripts[name]` в bounded immutable `package.json`, без missing-name fallback; script args допустимы только после `--`. `--cwd` поддержан только для Bun, flags других package managers отклоняются. Unknown/ambiguous flags и неподдержанные launchers (`sh`, `bash`, `dash`, `zsh`, `ruby`, `perl`, `npx`, `env`) fail-closed с `unsupported_dispatch_arguments` либо причиной отсутствующего target/binding. Это ограниченный parser, не полное воспроизведение поведения всех runtime. Native fixed executable и indirect imports/resources остаются ответственностью trusted configuration.

V2 runner завершает собственную launched process group перед post-run capture при normal return, timeout, output limit и исключении. Это cleanup зарегистрированной проверки; процесс, покинувший группу через новую session, и hostile host/process isolation этим не доказываются. Builtin runner остаётся отдельным path.
