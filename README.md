TaskClosureKit is a verifiable task closure protocol for AI coding agents. It binds task authority, repository state, execution evidence and review to determine which completion claims are justified.

# TaskClosureKit

Главный вопрос продукта: **какое ограниченное утверждение об этой задаче допустимо прямо сейчас?** TaskClosureKit формализует task closure для конкретного контракта и снимка. Он сопоставляет разрешения, Git state, результаты исполнения, актуальные evidence, acceptance criteria и review; отдельное подтверждение оператора фиксирует итоговый claim.

Для coding agent verification важно отличать выполненную проверку от вывода о готовности. Успешная проверка может стать stale evidence после изменения входов. Review относится к точному снимку и набору доказательств. Обязательное independent review без доверенного подтверждения независимости блокирует claim. Эти границы полезны в agentic software engineering, когда работу продолжает другой агент или инженер.

## Локальный срез v2.1

Новый namespace — `python3 -m taskclosurekit`. Контракт JSON имеет `schema: "taskclosurekit/v2"`; внешние зависимости и установка пакета не требуются. Основной путь:

```text
task create → authorize → baseline → изменения кода → check
→ evaluate → review → evaluate → CLAIMABLE → close → CLOSED
```

`CLAIMABLE` означает, что текущие обязательные условия позволяют claim `configured-acceptance-satisfied`. `CLOSED` возникает только после отдельного действия closure authority. Claim сохраняет ограничения: он подтверждает выбранные критерии допустимыми актуальными evidence для данного контракта и снимка, а не все требования продукта.

Builtin `git-index-whitespace-v1` сохраняет ограниченную проверку staged diff с `HEAD`. Отдельная trusted project configuration добавляет meaningful presets tests/typecheck/build: contract выбирает только IDs, а определения фиксируются при `task create --preset-config`. Criteria независимо выбирают проверки через `all_of` или `any_of`. Каждый существенный CLI-вызов поддерживает `--json`; интеграция читает `decision` и `reasons`, отдельно от `operational.status`.

Начать: [CLI v2 и полный цикл](docs/CLI.md), [пример контракта](examples/contract-v2.json), [модель доказательств](docs/V2_CONTRACT.md). Для демонстрации нужен небольшой одноразовый Git-репозиторий с исходным commit; snapshot пока поддерживает только ограниченную раскладку loose Git objects, поэтому обычный упакованный clone может быть отклонён. Контракт, review JSON и хранилище располагаются вне проверяемого репозитория; contract/review — также вне store.

## Доверие и ограничения

Task/closure authority семантически имеет класс `operator_confirmed`, источник `local-operator` и identity `unverified`. Старое schema spelling `human` сохраняется для совместимости. Локальное терминальное подтверждение привязано к точному authority digest, включая registry, но использует допущение `host_operator_assumed`: `identity_verified=false`. Доступ к терминалу сам по себе не удостоверяет человека. Отдельный `attest` сохраняет agent assertion как `agent_attested` metadata/digest без raw statement; assertion не становится measured evidence и не доказывает независимость.

Локальный HMAC защищает целостность поддержанных записей; он не защищает от вредоносного процесса с эквивалентными правами на хосте, который может подменить программу, ключ и журнал вместе. Разрешённый scope проверяется при оценке изменений и не является hostile-agent sandbox. Пресеты не принимают произвольный shell из контракта; runner ограничивает время и вывод, применяет POSIX resource limits, а адресное пространство ограничивает только на Linux. Сырой вывод проверки не сохраняется.

Проект не доказывает universal correctness, отсутствие багов или общую security certification; не удостоверяет identity без trusted identity provider; не заменяет CI, Git или in-toto/Witness/Sigstore. Локальный цикл и тесты не доказывают публикацию, production, deployment или работу внешнего провайдера. `measured_ci` представлен интерфейсом и fake adapter для тестов; сетевой CI adapter отсутствует.

## How TaskClosureKit differs

Это сравнение границ по категориям, заданным в исходном запросе на миграцию; оно не является независимым аудитом актуальных возможностей проектов и не утверждает превосходство.

| Категория/проект | Рассматриваемая область |
| --- | --- |
| BeforeDone | Fresh verifier evidence / completion gate |
| AET | Evidence plane |
| Lians | State recovery / completion guard |
| NAEOS | Engineering control plane |
| in-toto / Witness | Общие attestations и provenance |
| TaskClosureKit | Ограниченное закрытие инженерной задачи и обоснованные completion claims |

Поддержанные проверки и recovery служат closure transaction. Они не превращают продукт в orchestrator, memory system или общую платформу evidence; [полный список non-goals](docs/PRODUCT_BOUNDARIES.md) задаёт продуктовую границу.

## Совместимость и статус

`python3 -m taskproof` сохраняет отдельный legacy CLI и schema-1 журнал: [CLI v1](docs/CLI_V1.md). Он не является alias семантики v2. Старые журналы не конвертируются и не открываются v2; для v2 нужен новый контракт и новое хранилище. Не смешивайте namespaces или версии журнала в одном store.

Локальный пакет объявляет breaking version **2.1.0**; release не опубликован. Готовность конкретного состояния подтверждается полным срезом, compatibility path и обязательными проверками; номер версии и подготовка этих документов сами по себе не являются такой проверкой. Удалённое имя и GitHub metadata остаются отдельным действием владельца.

- [Контекст](CONTEXT.md), [описание продукта](docs/PRODUCT_BRIEF.md)
- [Архитектура и staged migration](docs/ARCHITECTURE_V2.md)
- [План и критерии завершения](docs/IMPLEMENTATION_PLAN.md)
- [Имя, локальная миграция и рекомендации GitHub](docs/MIGRATION_V2.md)

## Real engineering checks

[Engineering contract](examples/contract-engineering-v21.json) и [trusted presets](examples/presets-engineering-v21.json) демонстрируют tests/typecheck/build composition на нейтральном C fixture. Это templates с paths, которые оператор заменяет и проверяет перед CREATE; они не устанавливают runtime или dependencies. [Reference mapping web-app-template](docs/WEB_APP_TEMPLATE_REFERENCE.md) описывает небольшую реальную задачу, отдельно от синтетических fixture runs.

Изменение config/dispatcher делает authority stale и требует нового task; изменение executable/runtime — STALE_ENVIRONMENT; изменение source/tests/manifests/lock/config inputs — STALE_INPUT. Conservative full snapshots могут инвалидировать больше evidence, чем минимальный dependency graph. PASS checks допускают только bounded configured acceptance, не universal correctness.

Для внешнего workflow используется [CLI JSON seam LexForge](docs/LEXFORGE_INTEGRATION.md). `CLAIMABLE` остаётся отдельным от `CLOSED`; required independence при UNKNOWN блокирует claim. [Migration v2.1](docs/MIGRATION_V21.md) описывает новые exit semantics и совместимость.
