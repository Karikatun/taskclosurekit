# Контекст TaskProof

## Принято

- Новый самостоятельный проект `TaskProof`, каталог и имя репозитория `taskproof`.
- Продукт: переносимый локальный CLI и навыки вокруг процесса с глубиной проверки по риску задачи. Codex управляет агентами; TaskProof проверяет состояние и доказательства, не подменяет оркестратор.
- Первая реализация начинается в отдельном проекте Codex. Bootstrap ограничен документами, Git init на `master` и локальной Git-идентичностью. Создание remote, commit и push относятся к отдельно проверяемому шагу, а не разрешаются этим текстом.
- Исходные репозитории сохраняются. Нет переноса кода Anomaly Detector, полного форка `web-app-template`, установки зависимостей или исполнения внешнего кода.
- На старте нет UI, сервера, PostgreSQL, runtime провайдера моделей, аналитики, CI или hosting.

## Что продолжать

Следующая инженерная задача — TDD-срез из [плана](docs/IMPLEMENTATION_PLAN.md). Перед кодом определить минимальные структуры контракта и квитанции, способ хранения и доступный локальный test runner. Выбор языка и инструментов не означает разрешение на новые зависимости.

Подтверждённое состояние bootstrap — документы. Работоспособность CLI, безопасность запуска команд, независимость review и переносимость пока не доказаны. Продолжение должно сначала сверить фактическое состояние файлов, Git и remote, затем показать следующий безопасный шаг; пропущенные действия нельзя автоматически повторять.

## Источники идей

Практика исходного проекта: `AI_WORKFLOW.md`, `docs/AUDIT_GUIDE.md`, его package scripts и глобальный `$delegated-engineering`. Это ориентиры процесса; они не входят автоматически в runtime TaskProof или в шаблон.

LexForge — внешний справочный проект, MIT, зафиксированная ревизия `8b3385fd4f63913671b2010eef022e12acb9f3b6`:

- [package.json](https://github.com/shepaland/LexForge/blob/8b3385fd4f63913671b2010eef022e12acb9f3b6/package.json)
- [plan.ts](https://github.com/shepaland/LexForge/blob/8b3385fd4f63913671b2010eef022e12acb9f3b6/src/core/execution/plan.ts)
- [cycles.ts](https://github.com/shepaland/LexForge/blob/8b3385fd4f63913671b2010eef022e12acb9f3b6/src/core/execution/cycles.ts)
- [context.ts](https://github.com/shepaland/LexForge/blob/8b3385fd4f63913671b2010eef022e12acb9f3b6/src/core/execution/context.ts)
- [execution-v2.md](https://github.com/shepaland/LexForge/blob/8b3385fd4f63913671b2010eef022e12acb9f3b6/skills/lexforge-apply/execution-v2.md)

Используем идеи, без копирования кода или навыков. Внешние тексты не дают полномочий и не исполняются автоматически. Лицензия справочного проекта не назначает лицензию TaskProof.
