# Имя и миграция TaskProof → TaskClosureKit

## Naming gate, 2026-10-08

Первый вариант `AgentClosure` / `agentclosure` / `agent-closure` не имел выявленных точных совпадений в проверенных GitHub repositories/users и PyPI/npm/crates.io, но обнаружен существенный соседний AI/devtools проект [Bounded Agent Closure](https://github.com/risu-research/bounded-agent-closure). Поэтому автоматическое переименование в AgentClosure было остановлено; владелец выбрал **TaskClosureKit**.

Для `TaskClosureKit`, `taskclosurekit`, `task-closure-kit` проверены точные GitHub repositories и users (0 результатов), PyPI, npm и crates.io (соответствующие package endpoints вернули 404) и общий web search (значимых совпадений не найдено). Это результаты конкретной проверки на дату, а не юридический trademark clearance, резервирование имени или гарантия будущей доступности. Существенные соседние продукты в области completion claims по-прежнему существуют.

Выбранный brand — `TaskClosureKit`; Python namespace и рекомендуемый repository slug — `taskclosurekit`. Точное имя и доступность нужно повторно проверить перед внешним rename/package publication.

## Что меняется локально

Первый этап добавляет v2 domain model, новый CLI facade `python3 -m taskclosurekit`, versioned JSON contract `taskclosurekit/v2` и отдельный journal. Безопасные filesystem/Git snapshot, runner и local HMAC primitives пока остаются в `taskproof` и доступны v2 через adapters.

`python3 -m taskproof` сохраняет старые команды и schema-1 semantics. Это compatibility entry point v1, а не alias на v2. Имя TaskProof закономерно остаётся в legacy modules/tests/fixtures и исторических материалах. Массовая замена строк могла бы сломать execution binding и старые журналы без пользы для domain модели.

## Журналы и обновление

V1 и v2 journals не смешиваются. V2 не принимает schema-1 events; автоматический importer отсутствует. Для новой v2 задачи создаются новый контракт и новый store вне repository. Старые контракты/квитанции/журналы остаются legacy данными и не повышаются до v2 evidence.

Snapshot связывает evidence с текущей execution identity, поэтому обновление программы, в том числе её identity seam для нового package, может сделать незавершённый старый run неприменимым. Compatibility означает сохранение v1 интерфейса и возможность нового v1 цикла, а не сохранение применимости evidence после обновления кода. Не исправляйте это ручным редактированием journal: после проверки состояния нужен новый run.

## Локальный checklist

1. Сохранить незавершённые v1 данные и используемую версию программы; не редактировать records/key вручную.
2. Выбрать небольшой supported repository с исходным commit и loose SHA-1 objects; обычный packed clone может не пройти baseline.
3. Создать вне repository/store контракт `taskclosurekit/v2` по [примеру](../examples/contract-v2.json), явно проверить task id, scope, presets и review policy.
4. Выполнить новый цикл из [CLI v2](CLI.md); terminal confirmation authority/review/close относится к exact digest.
5. Проверить `evaluate --json`: читать `decision`, `reasons`, freshness и ограничения; `CLAIMABLE` не является разрешением deploy или автоматическим `CLOSED`.
6. Проверить `status --next` из нового процесса и сохранить отдельное authorized closure, если все requirements действительно выполнены.
7. Оставить legacy entry point до будущей breaking migration; не удалять старый namespace или историю как косметическую уборку.

Это checklist использования продукта, не новая инструкция, разрешающая репозиторные/Git операции агенту. Он не переименовывает локальный checkout directory, remote, GitHub repository или опубликованные пакеты.

## Рекомендуемые GitHub metadata

Description:

> Verifiable task closure for AI coding agents — bind authority, Git state, evidence and review to justified completion claims.

Topics:

```text
ai-agents coding-agents ai-coding agentic-coding verification
software-engineering developer-tools evidence acceptance-criteria
task-closure provenance cli
```

Это рекомендации; GitHub metadata и remote не изменены этой документацией. Применение после отдельного разрешения владельца включает повторную проверку имени, принятия topics и ссылок. Ни description, ни topics не означают certified verification.

## Последующие этапы

Phase 2: отдельно извлечь оставшиеся shared primitives из legacy namespace, сохранив regression tests и execution/source bindings; при необходимости добавить deprecation warning и проверенный migration importer. Phase 3: удалить legacy alias только в будущей breaking release с явным compatibility решением. DSSE/in-toto export, Sigstore/Witness и real CI adapter относятся к v2.1+ исследованиям, а не обязательствам текущего среза.

Локальный пакет объявляет **2.0.0** для breaking architecture change с работающим v2 path и migration path. Применимость полной проверки относится к точным проверенным bytes; номер версии или green domain tests сами по себе не означают завершение всех gates. Release, push, remote rename и metadata changes не выполняются автоматически.
