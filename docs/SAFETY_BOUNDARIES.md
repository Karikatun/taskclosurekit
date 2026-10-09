# Границы безопасности TaskClosureKit

Этот документ описывает текущий код. Он не предоставляет агенту новых полномочий и не утверждает изоляцию ОС.

## Пути и размещение

Repository, contract, configuration, review/assertion и store задаются абсолютными нормализованными путями. Существующие компоненты должны точно совпадать с именами directory entries. Symlink, case и Unicode aliases отклоняются; сравнение размещения использует device/inode существующих предков. Path validation имеет бюджет 10 000 entries и 10 секунд; гонка или незавершённая проверка блокирует операцию.

Store физически вне repository и не является его предком. Contract/configuration физически вне repository/store до создания journal; review/assertion размещение проверяется до чтения их JSON. Private store имеет mode 0700, key/lock/events — 0600. Exact проверки не доказывают безопасность bind mount или других хостов.

Scope/source paths точные, без globs, `.`, `..`, пустых компонентов, backslash или `.git` при любом регистре. Sources должны совпадать с actual spelling обычных файлов и worktree/index binding; sources неизменяемы даже внутри write scope. Полный conservative snapshot учитывает hidden/ignored files; read scope декларативен и не является запретом чтений ОС.

## Git

Нужны initial commit, обычный `.git` и SHA-1 loose objects. Linked worktrees, packs, alternates, submodules, shallow/replace/graft layouts, unsupported config, active hooks и skip/assume index flags отклоняются. Фактическое имя `.git` должно быть exact lowercase; отдельные `.GIT`/`.Git` также блокируют capture.

Loose objects имеют lowercase 2+38 hex имя. Ограниченная проверка zlib/header/body подтверждает тип `blob/tree/commit/tag`, заявленный размер и SHA-1 имени. `.git/objects/info` и `pack` могут быть только пустыми каталогами. Проверка объектов происходит до Git metadata reads; после них инвентарь должен совпадать. Это не `git fsck`, проверка семантики графа или авторства.

File/compressed object/declared body limit — 8 MiB; object header с разделителем — 64 bytes. Общий snapshot budget — 64 MiB, 10 000 entries и 10 секунд, включая повторные чтения и распаковку. Причины отказа включают `file_limit`, `snapshot_limit`, `snapshot_race`, `git_object_limit`, `unsupported_git_object_layout`, `invalid_git_object`, `git_object_identity_mismatch`, `unsupported_git_control`.

HEAD/refs/config/hooks/ignore/attributes и sources связаны с baseline. Создание/изменение/удаление `.gitattributes` в worktree или index, включая case variants и область write scope, блокирует цикл. Обычный staged loose object и `.git/index` могут измениться вместе с разрешённым source edit. PASS следует зафиксированным правилам Git и не означает отсутствие всех видов whitespace.

## Исполнение и identity

Builtin выполняет только `git --no-pager diff --cached --check --no-ext-diff --no-textconv HEAD --` системным Git из `/usr/bin:/bin`. Environment не наследует HOME, пользовательский PATH, Git variables, secrets, pager или proxy variables. Raw stdout/stderr считаются и отбрасываются.

Timeout не более 5 секунд, output limit не более 65536 bytes; квитанция при превышении может учесть последний read chunk до 8192 bytes. POSIX limits ограничивают CPU, file size, open files и core dump; address-space bound 512 MiB действует только на Linux. Windows и среда без нужных resource APIs не поддержаны. V2 project runner очищает launched process group на normal/timeout/output/error paths; это не isolation от потомка, сменившего process group/session. Builtin остаётся отдельным bounded process path.

Project config фиксирует argv/environment/runtime/dispatcher до authorize. Runtime resource binding имеет отдельный бюджет 384 MiB на файл, 1 GiB суммарно, 32 файла и 10 секунд. Permitted outputs ограничены 8 MiB/file и одновременно входят в contract write scope. Эти hashes не удостоверяют весь compiler/SDK или supply chain.

Execution identity включает `taskclosurekit/` и `tests/`. Изменение private helper или tests меняет binding; snapshot не исключает их ради продолжения старой задачи. Отсутствующее дерево или неполная identity fail-closed.

## Persistence, trust и recovery

HMAC journal имеет максимум 64 events; event read/write limit 16 MiB и общий bounded-read budget. JSON input limit 65536 bytes; duplicate/unknown fields, malformed data, non-finite numbers и unsupported state отклоняются. Atomic writes и nonblocking lock оставляют interrupted persistence явной ошибкой; pending/unknown entries не игнорируются. HMAC проверяет bytes/chain, semantic replay отдельно проверяет domain state/bindings.

HMAC key хранится локально. Равноправный владелец хоста может заменить программу/key/store вместе; HMAC не удостоверяет человека, reviewer independence или remote attestation. Терминал подтверждает exact authority/review/closure digest с identity unverified. Agent assertion/import/presentation не повышают trust. UNKNOWN independence блокирует required claim.

`status --next`, `resume`, `evaluate` читают journal и текущее состояние без исполнения checks и без journal writes. Interrupted check не повторяется автоматически. [Совместимость](COMPATIBILITY.md) описывает обновление программы и сохранение исторических данных. Не включайте secrets, исходные `.env` или персональные данные во входы и evidence.
