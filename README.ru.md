# TaskClosureKit

[English](README.md) · [Русский](README.ru.md)

TaskClosureKit — локальный CLI, который помогает AI coding agents и инженерам определить, когда инженерную задачу можно закрыть. Он связывает согласованную область, состояние репозитория, результаты проверок и review в зафиксированное ограниченное утверждение о завершении.

Успешный тест может потерять актуальность после следующей правки. Одобрение может относиться к прежнему diff. Новый агент может получить сообщение «готово» без подтверждающих доказательств. TaskClosureKit показывает, какие доказательства актуальны, что блокирует закрытие и какое действие допустимо дальше.

Утверждение **`configured-acceptance-satisfied`** означает, что обязательные критерии контракта задачи удовлетворены допустимыми актуальными доказательствами для связанного состояния. Его сила зависит от настроенных вами критериев.

## Как это работает

```text
create → authorize → baseline → implement and stage → check
       → evaluate → review → evaluate → CLAIMABLE → close → CLOSED
```

- **Контракт** задаёт репозиторий, разрешённые чтения и записи, IDs проверок, критерии приёмки и требования к review.
- **Проверки** запускают доверенные пресеты и сохраняют результаты, связанные с контрактом, репозиторием и средой исполнения.
- **Оценка** проверяет полномочия, область изменений, актуальность доказательств и review. Исторический `PASS` может устареть; отсутствующее или неизвестное подтверждение блокирует claim.
- **Review** относится к точному снимку и набору доказательств. Обязательная независимость остаётся блокирующим условием без доверенного подтверждения независимости.
- **Закрытие** — отдельное действие оператора. `CLAIMABLE` означает, что текущие условия допускают claim; `CLOSED` — что оператор его зафиксировал.

`status --next --json` позволяет другому процессу восстановить сохранённое состояние задачи и блокирующие причины без воспроизведения беседы.

## Установка и требования

Версия исходного кода и локального npm-пакета — **2.1.0**. Пакет содержит существующий CLI на стандартной библиотеке Python и один Node launcher `taskclosurekit` без зависимостей. Сторонних Node/Python dependencies, install scripts, загрузки runtime и автоматического обновления stores нет. Публикация в npm registry и доступность имени пакета не подтверждены.

Нужны Python **>=3.9**, macOS либо Linux с необходимыми POSIX API ограничения ресурсов и системный Git в `/usr/bin` или `/bin`. Для npm launcher также нужны Node **>=22** и npm/npx. Windows не поддерживается. Локально проверены macOS, Python 3.9.6, Node 22.23.1 и npm 10.9.8; подтверждённой матрицы Linux и других версий runtime нет.

При наличии локального tarball Git clone не нужен:

```sh
npx --offline --yes --ignore-scripts --package=/absolute/path/taskclosurekit-2.1.0.tgz -- taskclosurekit --help
npx --offline --yes --ignore-scripts --package=/absolute/path/taskclosurekit-2.1.0.tgz -- taskclosurekit --store /absolute/path/store status --next --json
```

Команды используют локальный архив и не скачивают неопубликованный пакет из registry. После отдельно разрешённой публикации архив можно будет заменить закреплённой registry version. Кешем управляет npm; проверяйте переданный пакет.

Launcher выбирает `/usr/bin/python3`, затем `/bin/python3`, используя первый существующий кандидат. Он не ищет Python в дополненном npm `PATH` и не пробует другой interpreter после ошибки runtime. Для другого заранее установленного Python задайте `TASKCLOSUREKIT_PYTHON` с абсолютным путём исполняемого файла. Путь разрешается один раз; bytes/path/version Python остаются частью существующего binding. Явный override означает доверенный выбор executable, а не установку Python или sandbox.

Python запускается с `-I -S -B` и импортирует bundled CLI из каталога пакета. Сохраняются cwd вызывающего процесса, границы аргументов, stdin/stdout/stderr, терминальные подтверждения и exit codes. Модули проекта, `PYTHONPATH` и site customization не подменяют bundled imports. Node и npm сами не аттестуются существующей execution identity. Подробности — в [CLI](docs/CLI.md#npm-launcher).

Запуск из исходников сохраняется:

```sh
git clone https://github.com/Karikatun/taskclosurekit.git
cd taskclosurekit
python3 -B -m taskclosurekit --help
```

Быстрый старт ниже запускается из этой копии репозитория и читает bundled examples. При наличии архива замените каждый CLI invocation префиксом локального npx выше и держите example inputs вне проверяемого repository/store. `-B` предотвращает Python bytecode. Не меняйте payload инструмента во время задачи: execution identity включает `taskclosurekit/` и `tests/`.

Для локального архива из проверенной копии используйте `npm pack --offline --ignore-scripts --pack-destination /absolute/path/to/output`. Команда упаковывает файлы без публикации. Архив содержит оба identity roots, документацию и публичные examples; metadata `UNLICENSED` сохраняет отсутствие лицензии и не предоставляет прав на ПО.

**Проверяемый репозиторий** должен иметь исходный commit и обычный каталог `.git` с отдельными SHA-1 objects. Packed objects, alternates и linked worktrees отклоняются. Поэтому обычный клонированный целевой репозиторий может оказаться неподдерживаемым. Быстрый старт создаёт поддерживаемый одноразовый репозиторий.

## Быстрый старт

Пример меняет один Python-файл и проверяет **только пробельные ошибки в staged diff**. Он демонстрирует закрытие задачи; поведение Python-программы не проверяется.

### 1. Подготовьте изолированный пример

Блок создаёт уникальный временный каталог с репозиторием, внешним контрактом и, позднее, отдельным store. Он читает встроенный контракт как JSON и создаёт исходный commit с демонстрационными данными автора. Ваши проекты и глобальная конфигурация Git не меняются.

```sh
TASKCLOSUREKIT_DEMO=$(python3 -B - <<'PY'
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

root = Path(tempfile.mkdtemp(prefix="taskclosurekit-demo-")).resolve()
repo = root / "repo"
repo.mkdir()
(repo / "src").mkdir()
(repo / "README.md").write_text("TaskClosureKit example.\n")
(repo / "src/foo.py").write_text("value = 1\n")
git = shutil.which("git", path="/usr/bin:/bin")
env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C",
       "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
def run(*args):
    subprocess.run([git, *args], cwd=repo, env=env, check=True,
                   stdout=subprocess.DEVNULL)
run("init", "--template=", "--initial-branch=master", "--object-format=sha1")
run("add", "README.md", "src/foo.py")
run("-c", "user.name=Example", "-c", "user.email=example@example.invalid",
    "commit", "-m", "Example baseline")
contract = json.loads(Path("examples/contract-v2.json").read_text())
contract["repository"] = str(repo)
contract["task"] = {"id": "quickstart", "title": "Check staged whitespace"}
(root / "contract.json").write_text(json.dumps(contract))
print(root)
PY
)
printf '%s\n' "$TASKCLOSUREKIT_DEMO"
```

Сохраните выведенный путь. Store не должен существовать до `task create`, который создаёт его с приватными правами. Пути должны быть абсолютными и физически находиться вне проверяемого репозитория. Файлы контракта, review, assertion и конфигурации пресетов также должны оставаться вне store. Symlink aliases отклоняются.

### 2. Создайте и разрешите задачу

Запускайте каждую команду отдельно в интерактивном терминале:

```sh
python3 -B -m taskclosurekit --store "$TASKCLOSUREKIT_DEMO/store" task create "$TASKCLOSUREKIT_DEMO/contract.json" --json
python3 -B -m taskclosurekit --store "$TASKCLOSUREKIT_DEMO/store" authorize --json
python3 -B -m taskclosurekit --store "$TASKCLOSUREKIT_DEMO/store" baseline --json
```

`authorize` просит точно ввести показанный digest действия после проверки контракта. Позднее `review --human` и `close` потребуют собственных терминальных подтверждений. JSON-вывод не отменяет подтверждение; piped input не может его предоставить. В реальных задачах это решения оператора. Assertion агента не заменяет их.

### 3. Внесите разрешённое изменение и проверьте его

```sh
python3 -B - "$TASKCLOSUREKIT_DEMO/repo/src/foo.py" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_text("value = 2\n")
PY
git -C "$TASKCLOSUREKIT_DEMO/repo" add src/foo.py
python3 -B -m taskclosurekit --store "$TASKCLOSUREKIT_DEMO/store" check git-index-whitespace-v1 --json
python3 -B -m taskclosurekit --store "$TASKCLOSUREKIT_DEMO/store" evaluate --json
```

Проверка должна сохранить `PASS`. Оценка должна вернуть `NOT_CLAIMABLE`, причину `missing_trusted_review` и exit code **1**, потому что review ещё отсутствует. Это ожидаемый промежуточный результат.

### 4. Проведите review и закройте задачу

Сначала изучите staged diff. Затем запускайте каждую команду TaskClosureKit отдельно и подтвердите запросы review и закрытия:

```sh
git -C "$TASKCLOSUREKIT_DEMO/repo" diff --cached
python3 -B -m taskclosurekit --store "$TASKCLOSUREKIT_DEMO/store" review --human --json
python3 -B -m taskclosurekit --store "$TASKCLOSUREKIT_DEMO/store" evaluate --json
python3 -B -m taskclosurekit --store "$TASKCLOSUREKIT_DEMO/store" close --json
python3 -B -m taskclosurekit --store "$TASKCLOSUREKIT_DEMO/store" status --next --json
```

Оценка должна перейти в `CLAIMABLE`; явное закрытие должно записать `CLOSED`. Последующие изменения требуют новых проверок и review, а при изменении полномочий — новой задачи. Сохранённое закрытие остаётся утверждением об исходном связанном состоянии.

## Использование в рабочем процессе

Для каждой реальной задачи создавайте новый контракт и store. Задавайте узкую область изменений и содержательные критерии приёмки. Встроенный `git-index-whitespace-v1` проверяет только staged whitespace. Для тестов, проверки типов и сборки передайте при создании отдельно проверенную доверенную конфигурацию пресетов:

```sh
python3 -B -m taskclosurekit --store /absolute/path/to/new-store task create /absolute/path/to/contract.json --preset-config /absolute/path/to/presets.json --json
```

Контракт выбирает IDs пресетов; пути исполняемых файлов, аргументы, окружение, ограничения, входы и разрешённые выходы принадлежат доверенной конфигурации. Перед использованием адаптируйте и проверьте [инженерный пример](examples/contract-engineering-v21.json) и [шаблон пресетов](examples/presets-engineering-v21.json). Они не устанавливают свои runtime.

Сохраняйте источники контракта неизменными, пишите только в согласованной области и повторяйте затронутые проверки после правок. Изменения инструмента, конфигурации или runtime могут инвалидировать полномочия либо доказательства исполнения. Консервативные снимки также могут сделать ранние проверки устаревшими, когда другая проверка создаёт выходные файлы. Следуйте указанной блокирующей причине, а не меняйте JSON, чтобы заявить более высокий уровень доверия.

Для машинной интеграции сначала читайте `operational.status`, затем `decision`, `reasons` и freshness. `operational.status: "ok"` может сопровождать заблокированный claim. Exit codes: **0** — успешная незаблокированная операция; **1** — claim заблокирован политикой; **2** — некорректный ввод/состояние; **3** — ошибка среды или внутренний сбой; **4** — устаревшее или неизвестное состояние. Формат ответа и причины отказа описаны в [CLI reference](docs/CLI.md#machine-envelope).

## Доверие и текущие ограничения

- Scope проверяется при оценке. Он не изолирует агента и не предотвращает чтения и записи на хосте.
- Терминальное подтверждение использует допущение локального оператора; оно не удостоверяет личность человека и не доказывает независимость reviewer. Импортированные review и assertions сохраняют agent-attested trust.
- Локальные HMAC-записи выявляют поддержанные нарушения целостности. Процесс с равными правами на хосте может заменить программу, ключ и журнал вместе.
- Исполнение пресетов ограничено по времени и объёму вывода и использует POSIX resource limits; ограничение адресного пространства действует только на Linux. Сырой вывод проверки отбрасывается и не сохраняется.
- Claim относится к настроенным критериям приёмки одного связанного состояния задачи. Он не доказывает полную корректность, security certification или production readiness.

Репозиторий содержит локальный CLI и тесты. Сетевого CI adapter, SDK, completion hook, установщика Python runtime и deployment service нет. CI interface имеет тестовый adapter, который не является доказательством от реального CI provider. Подробности — в [границах продукта](docs/PRODUCT_BOUNDARIES.md) и [модели контракта](docs/V2_CONTRACT.md).

Не помещайте credentials, исходные `.env` и персональные данные во входы задачи или доказательства. Журнал хранит метаданные задачи и снимки; отбрасывание вывода проверок не делает остальные файлы анонимными.

## Удаление и данные задач

Прекратите вызовы CLI и удалите собственную копию исходников либо переданный архив, когда они больше не нужны, предварительно проверив локальную работу, которую хотите сохранить. Записи npm cache существуют отдельно; удаляйте только найденные вами записи, которые решили больше не хранить. Добавленные вами wrapper или интеграцию в workflow удалите отдельно. TaskClosureKit не имеет hook для удаления task data.

Данные задачи не зависят от копии исходников. Каждый каталог `--store` содержит журнал и его локальный HMAC-ключ. Внешние контракты, конфигурации пресетов и импортированные файлы review/assertion хранятся отдельно. Удаление инструмента не удаляет эти файлы или проверяемый репозиторий.

Обновление программы меняет execution identity. Прежние baseline и PASS не становятся current автоматически; после проверки состояния нужен новый согласованный task и store. Предыдущего CLI alias и importer журналов нет. См. [совместимость](docs/COMPATIBILITY.md) и [границы безопасности](docs/SAFETY_BOUNDARIES.md).

Перед удалением store решите, нужно ли сохранить доказательства. Если запись нужна, сохраняйте полный приватный store, включая ключ, вместе с относящимися к нему внешними входами. Для продолжения задачи также нужны исходные связанные пути, состояние репозитория и идентичность исполняемого кода; архив сам по себе не гарантирует возобновление. Удаление ключа или журнала лишает возможности проверяемого восстановления. Удаляйте только конкретные каталоги и файлы, которые решили больше не хранить. Выведенный временный каталог быстрого старта содержит все демонстрационные данные.

## Участие в разработке

Для работы с исходниками прочитайте [AGENTS.md](https://github.com/Karikatun/taskclosurekit/blob/master/AGENTS.md), [CONTEXT.md](CONTEXT.md) и документацию затронутой области. Сохраняйте чужую работу, определяйте scope и критерии приёмки до правок и проверяйте затронутое поведение. Работу с репозиторием регулируют его инструкции; этот README не даёт агентам дополнительных полномочий.

Тесты используют одноразовые fixtures и стандартный test runner Python:

```sh
python3 -B -m unittest discover -s tests -v
```

Если системный временный каталог сильно заполнен, используйте для fixtures временный каталог с небольшим числом записей: проверка физических путей имеет ограничения. Успешный локальный suite не доказывает удалённый CI, публикацию или deployment.

## Документация

- [Команды CLI, JSON-ответы и восстановление](docs/CLI.md)
- [Контракты, пресеты и привязки доказательств](docs/V2_CONTRACT.md)
- [Модель продукта](docs/PRODUCT_BRIEF.md) и [границы](docs/PRODUCT_BOUNDARIES.md)
- [Инженерный fixture](examples/engineering-fixture/README.md)

Подробная документация сейчас написана по-русски. Оба README описывают одну и ту же текущую функциональность.

## Лицензия

В репозитории сейчас нет файла `LICENSE` или объявленной лицензии на программное обеспечение. Лицензия здесь не указана.
