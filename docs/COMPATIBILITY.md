# Совместимость и обновление TaskClosureKit

Текущий публичный interface — `python3 -m taskclosurekit`; source version — 2.1.0. Task contract `taskclosurekit/v2`, result `taskclosurekit/result/v2`, preset configuration `taskclosurekit/presets/v1`, review/assertion schemas, builtin `git-index-whitespace-v1` и reason codes сохраняются.

## Форматы

Поддержан только текущий task contract и current domain events (`CONTRACT_CREATED` и последующие допустимые события). Числовой schema-1 task contract и прежний lifecycle `DRAFT` journal не принимаются. Отдельного предыдущего namespace, CLI alias или автоматического importer нет.

Числовое `schema: 1` во внешнем HMAC event envelope — версия формата хранения, а не версия task contract. Она по-прежнему используется текущим journal. HMAC integrity и обязательный semantic replay проверяются отдельно; формат envelope не делает неподдержанный payload допустимым.

`human`, `human_confirmed` и `review --human` остаются compatibility spelling текущего interface. Семантическая authority — `operator_confirmed`, source `local-operator`, identity `unverified`, `identity_verified=false`; это не verified identity и не PROVEN independence. Required independence остаётся UNKNOWN без trusted provider.

## Применимость после обновления

Проверки связаны с точными bytes программы и `tests/`, runtime, contract, repository и preset definitions. Перемещение private primitives и смена program roots меняют execution identity. Baseline и PASS предыдущей версии не становятся current автоматически. Старый current-format store может пройти integrity/semantic replay, но текущая оценка обнаруживает изменённый binding, блокирует claim и требует нового task. Некоторые несовместимые records могут отклоняться уже на replay; отказ не означает успешное восстановление.

Historical PASS и recorded closure остаются утверждениями о прежнем состоянии. Обновление не переписывает events, keys, signatures или digests, не выдаёт новые полномочия и не гарантирует продолжение незавершённой задачи. Для нового цикла нужны новый согласованный contract/store и повторные checks/review/closure confirmation. Сохраняйте при необходимости полную приватную старую запись и использованную копию программы вместе с внешними inputs; архив сам по себе не гарантирует resumability. Эти рекомендации не разрешают агенту удалять или изменять внешние stores.

## Конфигурация и машинный consumer

Project config фиксируется при CREATE; config/dispatcher drift означает STALE_AUTHORITY и новый task, runtime drift — STALE_ENVIRONMENT, ordinary input drift — STALE_INPUT. Ни confirmation, ни check не освежают изменённую authority молча. Неизвестный/неполный replay fail-closed. Поддержанный dispatch grammar и resource limits описаны в [контракте](V2_CONTRACT.md) и [границах](SAFETY_BOUNDARIES.md).

Consumer сначала читает `operational.status`, затем `decision`, `reasons`, freshness и next action. Exit codes: 0 — успешно без blocked claim; 1 — policy blocker; 2 — invalid input/state; 3 — environment/internal failure; 4 — stale/unknown state. JSON с operational status `ok` может содержать NOT_CLAIMABLE. Consumer не закрывает task автоматически и не расширяет scope по assertion.

Установщик, package release, сеть, SDK, completion hook и внешний store migration не реализованы. [CLI](CLI.md) описывает текущие команды.
