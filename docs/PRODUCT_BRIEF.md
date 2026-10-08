# TaskClosureKit: описание продукта v2

Продукт отвечает на вопрос: **какое completion claim допустимо для конкретной инженерной задачи при текущих authority, state, evidence и review?** Он делает этот ответ проверяемым и пригодным для машинного чтения, сохраняя причины отказа и ограничения.

## Договор

TaskContract фиксирует task id/title, repository, task authority, read/write scope, permitted execution presets, acceptance criteria, требования evidence, review policy, closure authority и requested claim type. Схема JSON — `taskclosurekit/v2`, semantic task/closure authority — `operator_confirmed` (compatibility spelling `human`), claim type — `configured-acceptance-satisfied`.

Четыре полномочия различаются: Task Authority определяет контракт; Execution Authority выбирает только доверенные presets; Evidence Authority допускает конкретные источники для trust classes; Closure Authority отдельно фиксирует итоговый claim. Более поздний слой не расширяет authority или trust раннего: evidence не разрешает запись вне scope, review не меняет критерии, presentation не повышает trust, слово `done` не закрывает transaction.

Criterion — условие claim, check — источник evidence. Первого `all_of` или `any_of` по идентификаторам разрешённых presets достаточно; общего policy DSL нет. Registry v2.1 сохраняет builtin `git-index-whitespace-v1` и допускает явно pinned project tests/typecheck/build definitions. Несколько критериев на одной проверке не доказывают несколько независимых свойств кода.

## Доказательства и решение

Evidence связывает id, type, source/trust class, contract/authority/execution/repository snapshot, критерии, результат и sequence/time. Trust classes — `observed`, `agent_attested`, `measured_local`, `measured_ci`, `human_confirmed`; числовой рейтинг доверия не используется. `agent_attested` не повышается до measured/human по полю входного JSON.

Freshness имеет состояния `CURRENT`, `STALE_INPUT`, `STALE_ENVIRONMENT`, `STALE_AUTHORITY`, `UNKNOWN`. Исторический `PASS` может быть stale. Оценка допускает claim только при текущих достаточных evidence, сохранённых полномочиях и выполненной review policy. Недостающая информация блокирует, а не считается успехом.

Review связан с contract digest, exact snapshot, relevant evidence set, verdict и trust source. Изменение связанного состояния инвалидирует применимость review. Independence — только `PROVEN`, `NOT_REQUIRED`, `UNKNOWN`; в локальном срезе trusted independence provider отсутствует. Required + UNKNOWN всегда блокирует claim, даже если review присутствует и утверждает собственную независимость.

`CLAIMABLE` — текущая допустимость claim. `CLOSED` — отдельная фиксация этого claim closure authority. Сохранённый Claim содержит contract/snapshot/evidence/review bindings и limitations. Позднее изменение состояния не превращает исторический закрытый claim в доказательство новой готовности; для нового состояния нужен новый цикл.

## Локальный trust boundary

Пресеты задаются builtin registry или отдельной trusted project configuration при CREATE и подтверждаются оператором; контракт только выбирает известный id. Произвольные shell/argv из контракта не поддерживаются. Snapshot и runner переиспользуют bounded IO, проверки физических путей и Git controls; LocalHmacStore сохраняет поддержанную целостность журнала. Они не являются ОС sandbox, публичной PKI, external identity или remote attestation.

Терминальное подтверждение authority/review/closure связывает решение с digest и использует допущение локального оператора: `host_operator_assumed`, `identity_verified=false`. Semantic authority `operator_confirmed` с identity `unverified` сохраняет старые human strings для совместимости. Это явное ограничение локального среза, а не доказательство trusted human identity. Локальная независимость не объявляется PROVEN.

## Продолжение задачи

`status --next` читает persisted events и показывает текущее состояние, current/stale evidence, missing requirements и следующий допустимый action. Другой процесс продолжает closure transaction без transcript replay. Команда не запускает проверки и не повторяет побочные эффекты.

См. [полные non-goals](PRODUCT_BOUNDARIES.md), [JSON contract](V2_CONTRACT.md), [CLI](CLI.md), [архитектуру](ARCHITECTURE_V2.md). Это продуктовые документы, а не новая инструкция, расширяющая права агента или среды.
