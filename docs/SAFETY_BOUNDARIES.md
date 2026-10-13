# Границы безопасности TaskClosureKit

Этот документ описывает текущий код. Он не предоставляет агенту новых полномочий и не утверждает изоляцию ОС.

## Пути и размещение

Repository, contract, configuration, review/assertion и store задаются абсолютными нормализованными путями. Существующие компоненты должны точно совпадать с именами directory entries. Symlink, case и Unicode aliases отклоняются; сравнение размещения использует device/inode существующих предков. Path validation имеет бюджет 10 000 entries и 10 секунд; гонка или незавершённая проверка блокирует операцию.

Store физически вне repository и не является его предком. Contract/configuration физически вне repository/store до создания journal; review/assertion размещение проверяется до чтения их JSON. Private store имеет mode 0700, key/lock/events — 0600. Exact проверки не доказывают безопасность bind mount или других хостов.

Scope/source paths точные, без globs, `.`, `..`, пустых компонентов, backslash или `.git` при любом регистре. Sources должны совпадать с actual spelling обычных файлов и worktree/index binding; sources неизменяемы даже внутри write scope. Полный conservative snapshot учитывает hidden/ignored files; read scope декларативен и не является запретом чтений ОС.

## Git

Нужны initial commit, ordinary `.git` либо проверенный linked worktree, SHA-1 loose/packed objects, files refs и полный index v2. Alternates, submodules, shallow/replace/graft layouts, unsupported config и skip/assume index flags отклоняются. Active hooks связываются как controls и не исполняются/не отключаются inspection. Фактическое имя `.git` должно быть exact lowercase; отдельные `.GIT`/`.Git` также блокируют capture.

Loose objects имеют lowercase 2+38 hex имя. Ограниченная проверка zlib/header/body подтверждает тип `blob/tree/commit/tag`, заявленный размер и SHA-1 имени. Pack v2/v3 и pack index v2 проверяются по bounded envelope/checksum/delta limits; обязательная pack/index пара должна быть полной. Проверка объектов происходит до Git metadata reads; после них инвентарь должен совпадать. Затем read-only `git fsck --strict --no-reflogs` проверяет граф; он не доказывает авторство или качество кода.

В strict fallback file/compressed object/declared body limit — 8 MiB; object header с разделителем — 64 bytes. Общий snapshot budget — 64 MiB, 10 000 entries и 10 секунд, включая повторные чтения и распаковку. Причины отказа включают `file_limit`, `snapshot_limit`, `snapshot_race`, `git_object_limit`, `unsupported_git_object_layout`, `invalid_git_object`, `git_object_identity_mismatch`, `unsupported_git_control`.

Это legacy fallback. Отдельно доверенный integration/v2 задаёт finite capture budgets для ordinary inputs/approved outputs; writer file bound должен помещаться в capture bound. Object body/index formats сохраняют отдельные узкие пределы. Immutable dependency reuse допускается только после полного initial material binding и текущего authenticated complete protected-interval host guard. Он охватывает root/provenance/external writers и отзыв до required append; core после capture и перед append проверяет текущие physical/provenance/live state/time без внешнего callback. JSON/HMAC, mode, stat и lockfile не являются proof. Все неохваченные roots/control/source bytes по-прежнему читаются; unavailable/expired/revoked/incomplete guard блокирует capture. Production proof provider отсутствует; test adapter не доказывает OS enforcement. [Контракт интеграции](INTEGRATION.md) задаёт precise schema и ограничения.

HEAD/refs/config/hooks/ignore/attributes и sources связаны с baseline. Создание/изменение/удаление `.gitattributes` в worktree или index, включая case variants и область write scope, блокирует цикл. Обычный staged loose object и `.git/index` могут измениться вместе с разрешённым source edit. PASS следует зафиксированным правилам Git и не означает отсутствие всех видов whitespace.

## Исполнение и identity

Builtin выполняет только `git --no-pager diff --cached --check --no-ext-diff --no-textconv HEAD --` системным Git из `/usr/bin:/bin`. Environment не наследует HOME, пользовательский PATH, Git variables, secrets, pager или proxy variables. Raw stdout/stderr считаются и отбрасываются.

Для builtin и presets/v1 timeout не более 5 секунд, output limit не более 65536 bytes; квитанция при превышении может учесть последний read chunk до 8192 bytes. POSIX limits ограничивают CPU, file size, open files и core dump; address-space bound 512 MiB действует только на Linux. Windows и среда без нужных resource APIs не поддержаны. Project runner и builtin очищают launched process group на normal/timeout/output/error paths; это не isolation от потомка, сменившего process group/session. Presets/v2 используют отдельно frozen конечную execution policy; [контракт интеграции](INTEGRATION.md) задаёт новые пределы и diagnostics.

Project config фиксирует argv/environment/runtime/dispatcher до authorize. Runtime resource binding имеет отдельный бюджет 384 MiB на файл, 1 GiB суммарно, 32 файла и 10 секунд. Permitted outputs одновременно входят в contract write scope. В strict fallback/presets/v1 действует 8 MiB/file; отдельно доверенные presets/v2 и integration/v2 могут задать больший конечный writer/capture bound, согласованный до CREATE. Overlimit или неполный capture не даёт PASS. Эти hashes не удостоверяют весь compiler/SDK или supply chain.

Execution identity включает `taskclosurekit/` и `tests/`. Изменение private helper или tests меняет binding; snapshot не исключает их ради продолжения старой задачи. Отсутствующее дерево или неполная identity fail-closed.

## Persistence, trust и recovery

HMAC journal имеет максимум 64 events; event read/write limit 16 MiB и общий bounded-read budget. JSON input limit 65536 bytes; duplicate/unknown fields, malformed data, non-finite numbers и unsupported state отклоняются. Atomic writes и nonblocking lock оставляют interrupted persistence явной ошибкой; pending/unknown entries не игнорируются. HMAC проверяет bytes/chain, semantic replay отдельно проверяет domain state/bindings.

HMAC key хранится локально. Равноправный владелец хоста может заменить программу/key/store вместе; HMAC не удостоверяет человека, reviewer independence или remote attestation. Терминал подтверждает exact authority/review/closure digest с identity unverified. Agent assertion/import/presentation не повышают trust. UNKNOWN independence блокирует required claim.

Delegated close записывает durable consumption-start до внешнего callback. После последнего callback идёт новый capture, pure fresh gates и current-time capability/guard validity перед append. Callback drift/expiry не даёт CLOSED. STARTED, CONSUMED и точный `consume_audit: true` обязательны для delegated CLAIM_CLOSED; unaudited delegated legacy variant не существует. Manual operator history сохраняет свой отдельный schema. Interrupted/ambiguous/consumed-refused attempt остаётся читаемым recovery blocker, без повторного consume или agent-issued replacement.

`status --next`, `resume`, `evaluate` читают journal и текущее состояние без исполнения checks и без journal writes. Interrupted check не повторяется автоматически. [Совместимость](COMPATIBILITY.md) описывает обновление программы и сохранение исторических данных. Не включайте secrets, исходные `.env` или персональные данные во входы и evidence.

## npm entry point и supply chain

Dependency-free CJS launcher передаёт argv массивом, `shell: false`, inherited cwd/stdio в bundled Python bootstrap. Python выбирается только из fixed system candidates или explicit absolute `TASKCLOSUREKIT_PYTHON`; npm-added PATH не выбирает interpreter. `-I -S -B` исключает caller cwd/PYTHONPATH/user site до bootstrap; package parent добавляется явно. Это import boundary, а не host sandbox; preinstalled Python, Node/npm, явный override и writable package cache остаются trusted host inputs.

Node >=22/Python >=3.9 и supported platform/resource/system Git проверяются до операций CLI. Windows отклоняется. Локально проверена только macOS matrix 3.9.6 / 22.23.1 / npm 10.9.8; Linux support declaration не означает проверку этой платформы. Node/npm execution identity не аттестует.

Package files allowlist включает Python source/tests, launcher, public example assets и docs. Dependencies, install/postinstall scripts, runtime downloads, telemetry, credential reads и store migration отсутствуют. Allowlist проверяется на реальном tarball, включая synthetic excluded `.env`, store, cache, `.scratch` и AGENTS sentinels в disposable candidate. Публикация, registry name availability и provenance не доказаны локальным pack/npx smoke. npm cache не является приватным store и не должен содержать ключи или task records.

SIGINT/SIGTERM передаются в отдельную Python session с inherited TTY descriptors; первый сигнал вызывает cleanup через KeyboardInterrupt, повторы игнорируются. Реальные controlling PTY подтверждения и Ctrl-C, targeted SIGTERM и interruption project check проверены локально. SIGKILL/crash host и escaped descendants остаются границами cleanup. Перед retry читайте `status --next`/`resume`; wrapper не повторяет команды и не заменяет interrupted evidence.


## Контракт интеграции

[Контракт интеграции](INTEGRATION.md)
