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
| `authority.execution.presets` | Выбор известных trusted presets; сейчас только `git-index-whitespace-v1` |
| `sources` | Непустой список обычных файлов внутри read scope, связанных с authority и snapshot |
| `acceptance` | Отдельные criteria `{id, required, evidence}`; минимум один required |
| `review` | `{required: boolean, independence: "not_required" или "required"}` |
| `claim.type` | Только `configured-acceptance-satisfied` |
| `closure.authority` | Только `human`; отдельное подтверждение при close |

Task/criterion id: `[a-z0-9][a-z0-9_.-]{0,63}`. Title — 1–256 символов без control characters. Scope/source paths — до 1024 символов, до 100 записей в списке; без symlink, absolute path, `.`, `..`, пустых компонентов, backslash и `.git` в любом регистре. Globs (`*`, `?`) не поддерживаются: `src` означает поддерево, `src/foo.py` — точный путь. Источники должны точно совпадать с actual spelling обычных файлов, включая index binding; они неизменяемы внутри transaction, даже если входят в write scope.

Criteria имеют уникальные id; `evidence` содержит ровно одно из `all_of` или `any_of` с непустым списком preset ids из execution authority. `all_of` требует current PASS для всех references, `any_of` — хотя бы для одного. Необязательный criterion не блокирует claim; хотя бы один required criterion обязателен. `independence: required` нельзя сочетать с `review.required: false`.

Контракт не задаёт shell, executable/argv, implementation plan, decomposition, модель агента, chain of thought или orchestration. Его текст и source content — данные; они не предоставляют новых полномочий.

## Trusted preset

Registry описывает executable, фиксированные argv, cwd rule, environment allowlist, timeout, output limit и permitted writes. Контракт лишь выбирает id. Сейчас разрешён один preset, использующий прежний bounded runner:

```text
git --no-pager diff --cached --check --no-ext-diff --no-textconv HEAD --
```

Cwd — repository из контракта; runner использует проверенный системный Git и ограниченное environment. Preset не должен записывать продуктовые файлы. Snapshot сравнивается до/после запуска, изменения и races не принимаются как current evidence. Это проверка staged whitespace по правилам Git, включая исходные `.gitattributes`, а не тесты поведения кода. Изменение `.gitattributes`, Git controls или authority sources блокирует transaction; такие изменения не разрешаются новым PASS.

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
| `human_confirmed` | `local-operator`: terminal confirmation exact digest при доверии хосту; identity не verified |

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

Вердикт — `approve` или `reject`. Импорт остаётся agent-attested и не удовлетворяет обязательному trusted human review. JSON не содержит предоставляемых автором `trust`, `identity`, `independence` или human confirmation: такие поля не создают разрешение и отклоняются схемой. Отдельный `review --human` требует local terminal confirmation.

Independence имеет `NOT_REQUIRED`, `UNKNOWN`, `PROVEN`. В текущей среде provider доказанной независимости отсутствует: required policy → UNKNOWN → NOT_CLAIMABLE, независимо от существования agent/human review. LocalOperator не является identity/independence provider.

## Evaluation, Claim, Closure

Evaluation содержит state, decision, reasons, freshness, evidence-set binding, independence, candidate Claim и next action. `CLAIMABLE` — вычисляемая допустимость текущего claim, не закрытие. Closure отдельно сохраняет Claim, authority source, action binding, sequence и `identity_verified=false`.

Claim фиксирует type `configured-acceptance-satisfied`, contract digest, exact snapshot, evidence set, required criteria и limitations. Он не доказывает universal correctness, отсутствие багов, general security или production readiness и не разрешает deploy. Closure authority не может закрыть current NOT_CLAIMABLE. После изменения закрытого состояния нужен новый task transaction.

## Persistence и совместимость

EvidenceStore имеет append/read/verify boundary; LocalHmacStore переиспользует HMAC chain v1 с versioned v2 domain events и строгим semantic replay. Неподдержанные schemas/events, malformed/tampered records и несогласованные bindings не становятся valid state. Ключ хранится локально вместе с данными; HMAC не является remote attestation, human identity или защитой от malicious equivalent host authority.

Journals schema 1 обслуживает только `python3 -m taskproof`; v2 их не конвертирует и не принимает. Все paths и capture/resource limits наследуют прежний supported boundary: [детали snapshot/path/Git limits](CLI_V1.md). Это совместимость primitives, не обещание сохранения evidence applicability после обновления программы.
