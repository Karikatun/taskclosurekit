# Граница интеграции с LexForge

TaskClosureKit v2.1 предоставляет существующий CLI JSON как machine boundary; отдельные API, RPC, SDK, зависимости или сетевой adapter не вводятся. LexForge ведёт свой implementation cycle, формирует bounded task contract и решает, может ли его собственный workflow продолжаться после результата TaskClosureKit.

```text
LexForge implementation cycle
  → TaskClosureKit transaction
  → evaluate --json
  → CLAIMABLE / NOT_CLAIMABLE
  → решение собственного workflow LexForge
```

## Передаваемые данные

Внешний consumer создаёт файл настоящего `taskclosurekit/v2` contract, содержащий `task`, `repository`, `authority.read`, `authority.write`, `authority.execution.presets`, `sources`, `acceptance`, `review`, `claim` и `closure`. Формат задаёт [V2_CONTRACT.md](V2_CONTRACT.md); упрощённая структура `required_checks` не является принимаемым API. Trusted configuration передаётся отдельно через `task create INPUT --preset-config ABS`. Contract, configuration и store соблюдают path boundaries CLI.

```sh
python3 -m taskclosurekit --store /tmp/taskclosurekit-store evaluate --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store status --next --json
```

Consumer проверяет `schema: taskclosurekit/result/v2`, `operational.status`, `decision`, `state`, `reasons` и `next_action`; читает `contract_digest`, `snapshot`, `evidence_set` и `claim`, когда они присутствуют. JSON projection не меняет persisted trust. Exit codes и поля handoff описаны в [CLI.md](CLI.md). `operational.status: ok` допускает блокирующий результат; `NOT_EVALUATED` не является одобрением.

`CLAIMABLE` допускает только claim `configured-acceptance-satisfied` для точных bindings. `next_action: close` — следующий допустимый closure action; это не команда deployment или разрешение обходить terminal confirmation. `close` требует отдельного действия closure authority. После изменений consumer повторяет оценку, а не переносит старый claim на новые bytes.

## Без кругового доверия

LexForge assertion «task completed» может быть лишь `agent_attested` assertion. Оно не удовлетворяет measured acceptance criterion, не подтверждает operator identity и не создаёт PROVEN independence. PASS должен возникнуть от предусмотренного trusted producer и иметь current exact bindings.

Обратно, TaskClosureKit `CLAIMABLE` не удостоверяет семантическую правильность всей реализации LexForge. Он подтверждает configured acceptance при сохранённых authority и review policy, с явными limitations. LexForge сохраняет ответственность за собственные proposal/spec/design, decomposition и workflow; TaskClosureKit не знает эти сущности и не назначает модели или subagents.

В v2.1 отсутствуют verified human identity provider, independence provider, hostile-agent sandbox и реальный сетевой CI adapter. Успешная локальная интеграция не доказывает remote CI, публикацию или production.
