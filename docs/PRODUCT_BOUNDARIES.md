# Продуктовые границы TaskClosureKit v2

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

`status --next` допустим только для восстановления доказуемого состояния closure transaction. Preset registry выбирает заранее доверенные capabilities и не является plugin marketplace. HMAC остаётся локальной деталью хранения. Machine envelope позволяет внешнему агенту читать решение, но SDK и completion hook не входят в core domain model.

Claim `configured-acceptance-satisfied` означает, что все обязательные **configured** criteria удовлетворены допустимыми актуальными evidence для конкретного контракта и snapshot. Он не означает, что код полностью корректен, багов нет, production безопасен, security доказана вообще, все требования продукта удовлетворены или deployment разрешён.

Любое расширение оценивается по тому, помогает ли оно этому конкретному claim и сохраняет ли существующие границы доверия. Future DSSE/in-toto export, Sigstore/Witness или CI adapters возможны как отдельные bounded integrations после проверки основного среза; их наличие не обещается этой миграцией.
