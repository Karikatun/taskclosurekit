# Продуктовые границы TaskClosureKit v2.1

Единственная специализация — ограниченное закрытие инженерной задачи: вычислить и отдельно зафиксировать допустимый completion claim для текущих authority, repository/execution state, evidence и review. Этот документ описывает продукт; он не изменяет AGENTS.md, полномочия оператора или ограничения среды.

TaskClosureKit не является:

- **orchestrator**: не назначает агентов, не управляет их работой;
- **coding agent**: не пишет реализацию задачи вместо исполнителя;
- **planner**: не строит decomposition или implementation plan;
- **memory system**: не хранит память беседы, знания или общий cross-agent context;
- **agent framework**: не предоставляет общий runtime агентов;
- **model router**: не выбирает модель и reasoning effort;
- **sandbox**: не ограничивает полномочия hostile agent средствами ОС;
- **generic policy engine**: не содержит универсальный policy DSL/compiler;
- **generic Evidence Plane**: не строит Evidence Atlas, knowledge/evidence graph или planner поверх evidence;
- **CI replacement**: не заменяет workflow, реальные внешние проверки или CI identity;
- **Git replacement**: не управляет Git историей, branches, commits или remotes;
- **universal code correctness proof**: не доказывает отсутствие ошибок и выполнение всех продуктовых требований;
- **security certification tool**: не сертифицирует общую безопасность или production readiness;
- **package manager**: не устанавливает пакеты и не разрешает supply-chain provenance;
- **deployment system**: не публикует, не выпускает release, не выполняет deploy;
- **alternative to in-toto/Witness/Sigstore**: не создаёт универсальную PKI или публичный стандарт attestations.

`status --next` допустим только для восстановления доказуемого состояния closure transaction. Preset registry выбирает заранее доверенные capabilities и не является plugin marketplace. HMAC остаётся локальной деталью хранения. Machine envelope позволяет внешнему агенту читать решение; существующий CLI JSON служит [универсальным machine interface](CLI.md#machine-envelope) для внешнего workflow, agent host, orchestrator или automation. SDK, RPC, сервер и completion hook отсутствуют. TaskClosureKit не принимает proposal, spec, design, decomposition, model assignment или subagents внешнего workflow.

Claim `configured-acceptance-satisfied` означает, что все обязательные **configured** criteria удовлетворены допустимыми актуальными evidence для конкретного контракта и snapshot. Он не означает, что код полностью корректен, багов нет, production безопасен, security доказана вообще, все требования продукта удовлетворены или deployment разрешён.

Любое расширение оценивается по тому, помогает ли оно этому конкретному claim и сохраняет ли существующие границы доверия. Future DSSE/in-toto export, Sigstore/Witness или CI adapters возможны как отдельные bounded integrations после проверки основного среза; их наличие не обещается текущим срезом.

## Real engineering checks

Заранее подтверждённые project presets могут представлять tests, typecheck и build. Contract выбирает только их IDs; executable, argv, cwd, environment, limits, permitted writes и relevant inputs принадлежат отдельной trusted configuration. Конфигурация фиксируется при создании transaction и подтверждается оператором вместе с authority. Изменённая конфигурация не может сама разрешить новую проверку в текущей задаче.

PASS означает результат выбранной проверки на связанном состоянии. Tests проверяют наблюдаемые сценарии, typecheck — свойства выбранной системы типов, build — выбранный процесс сборки. Ни один результат, ни их композиция не доказывают universal correctness, отсутствие багов или production readiness. Scope violation блокирует claim даже при всех PASS. Required independence без trusted provider остаётся UNKNOWN.

Синтетические engineering fixtures демонстрируют механизм closure, а [mapping web-app-template](WEB_APP_TEMPLATE_REFERENCE.md) показывает возможные реальные capabilities. Это разные уровни evidence: passing fixture не означает, что проверки внешнего проекта были выполнены.
