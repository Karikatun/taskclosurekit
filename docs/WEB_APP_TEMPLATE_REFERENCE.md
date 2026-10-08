# web-app-template: engineering reference

Источник — локальная read-only копия `Karikatun/web-app-template`, revision `fae928bdb069187a16fbbead9c877635058b488a`. Mapping основан на `package.json`, `packages/contracts/package.json`, `webapp/package.json` и расположении contract source/tests. Он не является запуском checks внешнего проекта, изменением этого репозитория или переносом его workflow. Исходные scripts не исполнялись и закрытый код не копировался.

## Возможные trusted capabilities

| Capability | Команда и cwd в reference | Relevant inputs | Особенность |
| --- | --- | --- | --- |
| `project-contract-tests` | `bun test src`, cwd `packages/contracts` | contract source/tests, manifests, lockfile, test/compiler config | Узкий regression slice |
| `project-contract-typecheck` | TypeScript `tsc --noEmit`, cwd `packages/contracts` | source, compiler config, manifests и lockfile | Executable и argv pin-ит оператор |
| `webapp-tests` | `bun test tests`, cwd `webapp` | webapp source/tests/config, shared contracts, manifests и lockfile | Не заменяет contracts tests |
| `architecture-check` | `bun scripts/architecture-check.mjs`, cwd repository | source layout, architecture rules, script и config | Возможное отдельное расширение registry |
| `project-build` | TypeScript `tsc -b`, затем Vite build, cwd `webapp` | source/shared contracts, compiler/Vite config, manifests и lockfile | Два процесса требуют отдельных presets; сборка пишет outputs/cache |

Registry v2.1 использует cwd repository; package-cwd команды требуют фиксированных tool args для выбора cwd/project, например Bun `--cwd packages/contracts` или TypeScript `--project packages/contracts/tsconfig.json`. Mapping не меняет cwd rule самовольно. Root `bun run check` намеренно не отображается целиком: это широкий workflow с Docker, E2E, secret checks и writes. Mapping не обещает поддержку всех этих процессов. Перед запуском оператор устанавливает точные trusted definitions, runtime inputs, environment и output paths. TaskClosureKit не устанавливает Bun/TypeScript/Vite или dependencies.

## Небольшая задача

Пример: добавить поддерживаемый код ошибки в `packages/contracts/src/errors.ts` и regression в `packages/contracts/src/errors.test.ts`. Write scope ограничивается этими двумя файлами. Criterion `contracts-regression` требует `project-contract-tests`; criterion `contracts-types` требует `project-contract-typecheck`. Source authority связывает неизменяемый `packages/contracts/package.json`; compiler config и lockfile входят в relevant input declaration. Review обязателен, independence явно `not_required`, поскольку trusted provider здесь отсутствует.

Contract выражает настоящий размер инженерной задачи, но сам не вносит изменение и не доказывает результат. [Contract для этой задачи](../examples/web-app-template-v21.json) фиксирует scopes и два независимых criteria; [Preset template](../examples/presets-web-app-template-v21.json) содержит абсолютные placeholders; определения tools оператор готовит отдельно с учётом pinned runtime paths и текущих limits. Он не был выполнен на внешнем repository. [Engineering examples](../examples/contract-engineering-v21.json) используют нейтральные синтетические fixtures и отдельную trusted configuration. Их успешный tests/typecheck/build-like flow не является PASS для исходного web-app-template. Реальный reference run в этой итерации не выполнен.

После source или test изменения evidence становится STALE_INPUT. После определения другой команды/config требуется новый task и новое authority confirmation. Review после rerun должен быть связан с новым exact evidence set. Даже при всех PASS запись за пределами двух разрешённых файлов блокирует claim.
