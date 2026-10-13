# Контракт интеграции: локальный срез

Внешний инженерный процесс передаёт TaskClosureKit контракт задачи и отдельно доверенные конфигурации. CLI остаётся основным машинным интерфейсом. Для этого среза подготовлена версия `2.2.0`. Номер в source package metadata не доказывает публикацию: наличие и содержимое registry artifact проверяются отдельно.

## Поддержанные capabilities

`capabilities.schema` — `taskclosurekit/adapters/v1`. Ответ перечисляет bounded local inspection, trusted presets, bounded rehash либо authenticated protected immutable-root interval, finite capture budgets, явную content equivalence стадий, host-injected review/authority и внешний CI port. Provider negotiation проверяет точную версию, identity, capability и требование `trusted-host-code`. JSON не выбирает модуль, executable provider или класс доверия. SDK, daemon, сеть, установка и новая orchestration отсутствуют.

Repository inspection поддерживает ordinary `.git`, packed SHA-1 objects и linked worktree с физическими backlinks; refs — files, полный index — v2, pack — v2/v3, pack index — v2. Unsupported index v4/split/sparse, SHA-256, alternates, submodules и существенный неподдержанный config блокируются. Действующие hooks включаются в controls, но read-only inspection их не исполняет и не отключает. Для `hooksPath` поддержан простой canonical physical path; Git quoting/escape/comment spelling блокируется, чтобы не связать другой каталог.

`taskclosurekit/presets/v1` сохраняет строгие пределы. `taskclosurekit/presets/v2` добавляет отдельно утверждаемую `taskclosurekit/execution-policy/v1`: конечные timeout/output/resource/diagnostic limits и явный внешний PATH. Defaults: 300 секунд, 1 MiB output, 300 CPU seconds, 8 MiB file writes, 64 descriptors, 512 MiB address space на Linux, 4096 bytes diagnostics. Полные допустимые пределы проверяет `execution.policy`. Preset хранит argv массивом, repository cwd, literal environment allowlist, executable/runtime bytes и first-match resolution объявленных tools. Ambient secrets не наследуются. Indirect tool graph должен быть полно объявлен доверенной конфигурацией; хост не изолирован.

Bounded diagnostics удаляют целые поддержанные quoted credential values, включая пробелы, escaped quotes и незакрытый retained quote после truncation. Неподдержанный произвольный формат не даёт гарантии очистки секрета. `require_complete_diagnostics=true` делает потерю необходимого вывода blocker. Timeout, output/resource limit, failure и interruption не дают PASS. Cleanup охватывает process group на всех путях; escaped descendants и SIGKILL хоста остаются ограничением.

## Roots и входы

Необязательный `task create ... --integration-config ABS` принимает внешний frozen `taskclosurekit/integration/v1`. Config и store размещаются вне monitored repository, config также вне store. Изменение этих правил означает `integration_authority_changed`; CREATE/authorize не обновляют старый binding молча.

```json
{
  "schema": "taskclosurekit/integration/v1",
  "stage_equivalence": "content",
  "roots": [{
    "path": "generated",
    "type": "generated",
    "acceptance": "exclude",
    "rationale": "Выходы не читаются ни одной настроенной проверкой.",
    "dependency_provenance": null
  }],
  "coverage": "bounded-full-rehash"
}
```

Типы: `source`, `dependency`, `generated`, `cache`, `evidence/orchestration`, `unknown`. Root paths не пересекаются. Исключение acceptance требует rationale, frozen config, write scope и отсутствия пересечения с sources, execution authority и declared relevant inputs. Только generated/cache могут исключаться из acceptance. Имя каталога и `.gitignore` ничего не исключают. Evidence/orchestration root внутри repository отклоняется.

Исключённый output остаётся в полном bounded byte inventory и проверке scope. Hidden, ignored, untracked, mode, delete, rename, links и внешние writers учитываются point-in-time capture; это не запрет конкурентной записи ОС. Controls проверяются отдельно от working content и review index binding. Гонка, escaping link, unreadable/special entry или бюджет блокируют capture. Неполное состояние не считается CURRENT. Unknown roots проходят только полное bounded inspection, без доверия по basename.

V1 dependency root требует `{method: "rehash", identity_inputs: [...]}` в read scope. Lockfile связывает provenance, но не доказывает неизменность installed bytes. V1 остаётся строгим fallback; `immutable-host` в нём отклоняется.

V2 (`taskclosurekit/integration/v2`) требует `coverage: bounded-host-proof-or-rehash` и явный `capture_budget`: `file_bytes`, `aggregate_bytes`, `entries`, `seconds`. Конечные ceilings: 1 GiB/file, 4 GiB суммарно, 1 000 000 entries и 300 s; legacy budgets не меняются. Writer file bound не может превышать выбранный capture file bound. Большой разрешённый output учитывается полно, а превышение бюджета, escaping link и unknown coverage блокируют completion. Git object/index formats сохраняют собственные узкие ограничения.

Для immutable dependency root V2 поддерживает `{method: "immutable-host", identity_inputs: [...]}`. Root входит в read scope, не пересекается с write scope и не исключается из acceptance. При CREATE реальные material bytes/modes полного root и provenance inputs читаются bounded способом, связываются с физической root identity, manifest и frozen provider identity. `dependency_provider` инжектируется host code как `ImmutableRootProvider`. Помимо instantaneous `verify`, reuse требует capability `repository.immutable-root-guard` и `protect`: exact typed `ProtectedImmutableRoot` с authenticated `VerifiedImmutableRoot` для task/root/manifest/physical/provenance binding, complete interval protection, plain live `ImmutableRootGuardState` и built-in `RLock`. Subclasses/properties и JSON не принимаются как live state.

Host guard охватывает реальный capture/check и необходимый final append: host защищает root и provenance от всех writers до конца interval, а mutex синхронизирует invalidation/revocation. Complete protection обязана сохраняться до core release, включая время required append; истечение proof запрещает новые решения, но не разрешает хосту снять protection посреди удерживаемого critical interval. Generation увеличивается при любом отзыве, включая revoke→restore. Acquisition callbacks выполняются до capture; core удерживает mutex, открывает cached root через `O_NOFOLLOW`, проверяет точный device/inode/mode и после capture и непосредственно перед append перечитывает physical/provenance, live state и время. После final capture нет host callback. При interruption mutex освобождается core; host protection имеет finite self-cleaning lease до 60 s, внешние release callbacks не поддержаны. Provider, требующий их либо не доказывающий весь interval, не подходит этому port. Слишком долгий check/capture должен отказать по expiry, а не продлевать lease callback после capture. Source inputs/controls вне root продолжают полностью читаться. Каждый новый guarded operation может получить свежий lease для того же frozen manifest; historical evidence bytes не меняются.

Persisted JSON/HMAC, chmod, stat, basename, lockfile и mutex сами по себе не предоставляют runtime proof или защиту внешних процессов. Без provider/guard capability, при revoked/stale/incomplete/forged proof результат UNKNOWN/blocked. Замена root identity или provenance требует нового binding/task. Test adapter демонстрирует положительный путь и отказ, исходя из явно принятого trusted-host enforcement; он не реализует и не доказывает OS immutability. Production provider не включён. В default environment immutable method остаётся неподдержан без proof. Полный rehash installed bytes остаётся безопасным fallback, а compiler/runtime bytes без такого proof повторно хешируются. Cache/generated не становятся автоматически пропущенными деревьями.

Sources контракта остаются frozen authority. Обычные source/tests/manifests/build configuration могут меняться внутри write scope: затронутые checks и review становятся stale и повторяются. Execution-changing script, command resolution, preset definition и полномочия требуют нового согласованного task/binding. V2 `dispatcher_inputs` для `package-scripts/v1` допускает только явно выбранные metadata fields из ограниченного allowlist; scripts, dependencies и unknown fields остаются frozen. Editable manifest целиком остаётся check input.

## Стадии

Без integration config сохраняется exact conservative режим. `stage_equivalence: content` разрешает перенос measured evidence при равенстве working bytes/modes и всех существенных authority/execution/control inputs. Этот opt-in применяется к checks, читающим working content; builtin staged whitespace требует exact mode и не допускает его.

Precommit tests могут остаться CURRENT после эквивалентного staging, но изменение index content делает прежний review stale. Новый staged review связывает exact index content и evidence set. Commit metadata, HEAD и текущий branch ref могут измениться без повторного запуска дорогих tests при равенстве content. Unrelated ref, Git layout, hooks, config и attributes drift не разрешаются. Необъявленный ignore edit остаётся out-of-scope; `.gitignore` не исключает bytes из capture. Attributes любого регистра остаются controls даже в write scope или generated output: такой check получает FAIL до записи success receipt. Historical receipts не переписываются. Review/close сохраняют отдельно проверенный snapshot для semantic replay.

`result_identity` показывает `precommit`, `staged-review`, `committed` либо `unknown`, текущий commit SHA и snapshot. `local_ready`, `remote_verified`, `ci_verified`, `deployed` независимы. Последние три остаются false для локального lifecycle. Ошибка дополнительной stage inspection возвращает `unknown` и `stage_inspection_unavailable`; это не доказательство committed result. Наличие SHA само по себе не связывает dirty/untracked inputs с commit.

## Host review и authority

Python boundaries находятся в `integration.adapters`: repository inspection protocol, execution/evidence protocol, `HostReviewProvider` и `AuthorityProvider`. Они инжектируются доверенным host code; CLI не загружает provider из JSON. Реального production host provider нет, тестовый provider демонстрирует контракт, но не удостоверяет реальные identities.

Host review должен подтвердить reviewer agent/session, creator agent/session sets, verified identity, fresh independent context, task/scope/contract/snapshot/evidence bindings и proof digest. Равные creator/reviewer identities отклоняются; разные строки без identity/context proof тоже отклоняются. Provider identity фиксируется. Каждое чтение с таким review требует актуального подтверждения provider; при его отсутствии independence становится UNKNOWN и required claim блокируется. Close перепроверяет proof после confirmation и capture. Manual operator path сохраняется с identity unverified и не устанавливает required independence.

Delegated authority отдельно от operator confirmation: внешний provider проверяет capability с точным task, operation, scope, binding, единственным action, nonce, временем выдачи/истечения, revoked=false, verified identity и externally-issued proof. Максимальная длительность capability — сутки. Host provider обязан атомарно consume одноразовую capability между процессами, проверяя expiry/revocation/replay; агент не выпускает её себе. На authorize/close проверка выполняется отдельно; перед consume expiry проверяется снова. Audit хранит binding/provider/proof/nonce/time, без сырого token. Authorize capability не может повторно использоваться для close и не разрешает push/deploy.

Close сохраняет `CLOSE_CAPABILITY_STARTED` до внешнего consume, затем `CLOSE_CAPABILITY_CONSUMED`; interruption или ambiguous consume оставляет durable pending blocker. STARTED/CONSUMED и точный `consume_audit: true` обязательны для delegated CLAIM_CLOSED, а authorization/body/transaction/sequence должны совпасть; unaudited delegated legacy variant не поддерживается. Manual operator history имеет отдельную сохранённую форму. После последнего внешнего callback заново захватываются repository/execution inputs, чисто вычисляются gates и проверяются current-time authorization и protected root interval непосредственно перед CLAIM_CLOSED. Callback writer, потеря proof или expiry не создаёт CLOSED. Refusal после consume сохраняет audit; `authority_attempt` показывает status/binding/nonce/provider и `inspect_then_new_task`. Повторное consume, автоматическая выдача replacement и silent recovery запрещены. Host verification callbacks обязаны быть read-only; всё равно после последнего callback действует final capture guard.

External CI port принимает только typed verified result: authenticated source, run ID, workflow/check/criteria, exact clean commit/snapshot, input/execution digests и свежий runtime proof. Файл со `success` не становится measured evidence. Реального сетевого provider нет; consumer без доверенного proof не удовлетворяет measured gate.

## Lifecycle и migration

Task schema/result schema и storage envelope сохраняются. Новые config schemas и adapter capability schema версионированы отдельно. В result/v2 добавлены `capabilities` и `result_identity`; consumer должен терпеть additive output fields, но все inputs/journal variants остаются exact-field typed schemas. Legacy event variants принимаются отдельно; integration/host/delegated variants требуют всех новых bindings. Unknown поля и boolean вместо integer отклоняются.

`evaluate`, `status`, `resume` не запускают checks, не consume capability и не пишут journal. Host verification в этих read-only операциях тоже обязана быть read-only. Store lock обеспечивает реальную межпроцессную сериализацию; interrupted CHECK_STARTED остаётся blocker. HMAC и semantic replay проверяются раздельно. Update не мигрирует store, не освежает старые authority или historical evidence. Сохраняйте старую программу для проверки её исторических записей; новый runtime требует нового task.

Примеры конкретного reference проекта переименованы в neutral `contract-ui-engineering-v21.json` и `presets-ui-engineering-v21.json`. Paths/executables внутри — placeholders; никакой прикладной repository этим срезом не изменён.

## Проверки и пилот

Первые end-to-end срезы: real Python tests/build → review → CLAIMABLE → отдельный CLOSED; generated output не инвалидирует unrelated tests; editable build input требует recheck/review; content-equivalent commit сохраняет historical evidence. Пилот использует только disposable Python и C fixtures с уже установленными binaries, packs, linked worktree, действующими hooks и editable build inputs. Это ограниченный эксперимент в пределах бюджета одного рабочего дня, а не наблюдение длиной в рабочий день.

Бюджет пилота: 120 секунд на fixture; cumulative logical hash reads — 512 MiB Python, 8 GiB C. Первый C запуск выявил слишком малый общий 512 MiB budget: 5,536,631,622 bytes за 5.800 секунд. Увеличение fixture budget отражает размер выбранного installed compiler и обязательные повторные identity reads, а не улучшение производительности. Итоговые наблюдения и negative gates фиксируются в [плане проверки](IMPLEMENTATION_PLAN.md). Эти counters не являются physical disk IO и не включают Git/compiler child IO либо незавершённые чтения. Большой runtime rehash остаётся измеренным ограничением масштабирования.
