# Контекст TaskClosureKit

TaskClosureKit — локальный инструмент для проверки допустимости и отдельной фиксации ограниченного закрытия инженерной задачи. Текущий публичный namespace — `python3 -m taskclosurekit`, контракт — `taskclosurekit/v2`.

## Архитектурный срез v2.1

V2 отделяет authority, execution, evidence, review, claim и closure. Новый контракт `taskclosurekit/v2` задаёт идентичность задачи, repository, read/write scope, разрешённые presets, композицию acceptance criteria и review/closure policy. Контракт не содержит implementation plan, произвольную команду или выбор модели агента.

Основной claim — `configured-acceptance-satisfied`: обязательные configured acceptance criteria удовлетворены допустимыми актуальными evidence для конкретного контракта и снимка. Успех проверки недостаточен для такого claim, если scope нарушен, review устарел или обязательная независимость неизвестна. `CLAIMABLE` и `CLOSED` разделены; закрытие требует отдельного действия authority.

Проверки физических путей, bounded snapshot/Git IO, фиксированный runner и HMAC chain принадлежат закрытому слою `taskclosurekit._primitives`. Snapshot поддерживает обычный `.git`, SHA-1 loose objects, без packed objects/alternates. [Архитектура](docs/ARCHITECTURE_V2.md) описывает текущие модули, [границы безопасности](docs/SAFETY_BOUNDARIES.md) — реальные ограничения. Предыдущий namespace и его lifecycle удалены; [совместимость](docs/COMPATIBILITY.md) отделяет форматы от применимости старых доказательств.

## Границы доказательств

`CURRENT`, `STALE_INPUT`, `STALE_ENVIRONMENT`, `STALE_AUTHORITY`, `UNKNOWN` характеризуют применимость evidence, отдельно от исторического результата `PASS`. Изменённый вход не делает прошлый запуск несуществовавшим; он делает его недостаточным для текущего claim. `status --next` восстанавливает только persisted closure transaction, а не память беседы.

Локальная semantic authority — `operator_confirmed`, source `local-operator`, identity `unverified`; legacy schema spelling `human` остаётся compatibility field. Терминальное подтверждение exact authority digest использует допущение `host_operator_assumed` и `identity_verified=false`. Импортированный review остаётся agent-attested. `independence: required` при отсутствии доверенного доказательства даёт `UNKNOWN` и блокирует claim; локальные JSON, HMAC и терминал не создают `PROVEN` independence.

HMAC подтверждает поддержанную локальную целостность, не identity, remote attestation или защиту от владельца хоста. Scope не ограничивает процесс средствами ОС. Read scope задаёт разрешённую область контракта, но не обещает запрет всех чтений хоста; полный repository snapshot может учитывать metadata и файлы для проверки изменений. Runner использует ограниченный environment и POSIX resource limits; ограничение памяти зависит от Linux. Сырой вывод не сохраняется.

`measured_ci` имеет seam для будущего доверенного адаптера и fake для локальных тестов. Реальные GitHub Actions, сеть, SDK, completion hook, installer, DSSE/in-toto/Sigstore export и публикация не входят в первый срез. Источники из других проектов остаются справочными материалами; тесты используют disposable fixtures.

## Приёмка

Документы описывают контракт среза; его реализацию подтверждают реальные CLI integration tests и проверка invariants, а не наличие новых dataclasses или имени. Сценарии и обязательные проверки перечислены в [плане](docs/IMPLEMENTATION_PLAN.md). Номер версии, локальный commit, push, CI и deployment требуют собственных фактических подтверждений и не следуют из green unit tests.

## Engineering checks и внешняя граница

Project-defined presets фиксируются внешней trusted configuration при CREATE, входят в authority binding и подтверждаются оператором. Contract выбирает IDs без executable/shell. Definitions/dispatcher не становятся новой authority после изменения: STALE_AUTHORITY требует нового task. Runtime/executable drift даёт STALE_ENVIRONMENT; input drift — STALE_INPUT. Явные source/tests/manifests/lockfiles/config declarations не заменяют полный conservative snapshot.

Review verdict, source trust, independence и freshness независимы. Approve от local operator с CURRENT binding и UNKNOWN independence не удовлетворяет `independence: required`. Ни measured checks, ни assertion внешнего workflow не расширяют scope.

[web-app-template reference](docs/WEB_APP_TEMPLATE_REFERENCE.md) — read-only mapping команд, не настоящий запуск этих checks. [Универсальный machine interface](docs/CLI.md#machine-envelope) использует CLI JSON и оставляет implementation cycle внешнему workflow, agent host, orchestrator или automation. Совместимость и обновление описаны в [COMPATIBILITY.md](docs/COMPATIBILITY.md).
