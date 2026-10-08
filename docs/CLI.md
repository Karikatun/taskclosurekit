# TaskClosureKit CLI v2

Локальный namespace — `python3 -m taskclosurekit`, без установки или внешних dependencies. Legacy `python3 -m taskproof` сохраняет отдельный [CLI v1](CLI_V1.md). Ни контракт, ни review JSON не исполняют произвольный shell.

## Запуск и файлы

Из корня этого проекта запускаются команды ниже. `--store` обязателен; пути repository/store/contract/review абсолютны и нормализованы. Store располагается физически вне проверяемого repository, contract/review — вне repository и store. Existing symlink components и aliases проверяются прежними safe-path helpers до создания/чтения данных; metadata источников также bound в snapshot.

Демонстрационный [контракт](../examples/contract-v2.json) относится к `/tmp/taskclosurekit-demo`, пишет только `src/foo.py` и связывает `README.md` как неизменяемый источник. Чтобы использовать его, подготовьте небольшой disposable Git repository с нейтральным `README.md`, `src/foo.py` и исходным commit, затем сохраните контракт вне этого repository, например `/tmp/taskclosurekit-contract.json`. Для реальной задачи замените task id, repository и scopes в новом contract file. Не используйте эти пути как готовый production store.

Поддержан обычный `.git` с SHA-1 loose objects; packs, alternates, linked worktrees, unsupported Git controls и неполные snapshots fail-closed. Все [прежние limits/path/source/Git rules](CLI_V1.md) сохраняются. Sources/read/write — точные относительные файлы или поддеревья, globs не поддерживаются. Read scope декларативен и не является host sandbox.

## Основной цикл

```sh
python3 -m taskclosurekit --store /tmp/taskclosurekit-store task create /tmp/taskclosurekit-contract.json --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store authorize --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store baseline --json
# Внесите только разрешённые изменения, затем staged-ите их средствами Git.
python3 -m taskclosurekit --store /tmp/taskclosurekit-store check git-index-whitespace-v1 --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store evaluate --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store review --human --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store evaluate --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store close --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store status --next --json
```

`task create` записывает DRAFT и contract digest; он не подтверждает authority автоматически. `authorize`, `review --human` и `close` показывают в stderr local-operator prompt с exact action digest. Оператор читает относящееся к этому действию состояние и вводит digest точно. Для подтверждения stdin должен быть terminal; noninteractive bypass (`--yes`, env flag или предоставление human trust в JSON) отсутствует. `--json` делает вывод машиночитаемым, но не отменяет подтверждение. У local-operator source `host_operator_assumed=true`, `identity_verified=false`; доказательство human identity или независимости из терминала не возникает.

`baseline` фиксирует исходное разрешённое состояние после authorize. `check PRESET_ID` реально выполняет выбранный trusted preset и создаёт bound measured_local evidence. В срезе существует один registry member — `git-index-whitespace-v1`, фиксированный staged whitespace Git check. Contract не меняет его command/args, cwd rules, environment или limits.

`evaluate` вычисляет текущую допустимость без запуска check и без закрытия. До обязательного review оно возвращает NOT_CLAIMABLE даже при current PASS. После review/evaluate возможен CLAIMABLE; только отдельный подтверждённый `close` сохраняет bounded Claim/Closure и CLOSED. Наличие candidate `claim` в evaluate ещё не означает recorded closure.

## Machine envelope

Все существенные команды поддерживают `--json`; текущий CLI печатает JSON envelope и без этого флага. Schema — `taskclosurekit/result/v2`.

| Поле | Как читать |
| --- | --- |
| `operation`, `task_id` | Операция и task transaction |
| `operational.status` | `ok`, `invalid_input`, `environment_error`, `internal_error` |
| `state` | Confirmed/projected lifecycle или BLOCKED/STALE/INVALID |
| `decision` | `CLAIMABLE`, `NOT_CLAIMABLE`, `NOT_EVALUATED` |
| `reasons` | Machine reason codes, без необходимости парсить prose |
| `freshness` | Список `{id, state}` для evidence applicability |
| `next_action` | Следующий допустимый action или необходимость внешнего proof/new task |
| `contract_digest`, `snapshot`, `evidence_set` | Bindings, когда операция их вычислила |
| `evidence`, `review_present`, `independence` | Evidence/review projection при оценке текущего transaction |
| `assertions` | Отдельные agent declarations: digest/metadata, без measured evidence и raw statement |
| `claim`, `closure` | Candidate/current limited claim и отдельный persisted closure, когда существует |
| `last_confirmed` | Последнее подтверждённое событием состояние, отдельно от current projection |

`operational.status: "ok"` означает успешную обработку операции, включая вычисленное блокирующее решение. Оно не означает CLAIMABLE. `NOT_EVALUATED` у create/authorize/baseline не является одобрением claim. Unknown/stale evidence отражается freshness/reasons или отказом capture, а не общей зелёной operational отметкой.

Exit codes: **0** — операция завершена без решения NOT_CLAIMABLE; **1** — решение NOT_CLAIMABLE; **2** — invalid input/state; **3** — environment/internal failure. Машинная интеграция сначала проверяет operational status, затем decision/reasons. При NOT_CLAIMABLE агент продолжает только уже разрешённую работу или сообщает конкретный blocker; это не разрешение расширять scope.

## Failure branches

- Изменение разрешённого input после check: historical PASS остаётся, freshness становится STALE_INPUT, evaluate не допускает claim. Повторите check для текущего staged state, затем review для нового snapshot/evidence set.
- Failed check/timeout/truncated capture или недостающее required evidence: claim blocked; устраняется причина, затем создаётся новая квитанция. Старый PASS не перекрывает более новый failed check.
- Изменение файла вне write scope: `write_scope_violation`; даже зелёный check не расширяет authority. Дальнейший шаг — осмотреть фактическое состояние и создать новый согласованный task transaction, если нужен другой scope.
- Изменение contract, source, Git controls или execution identity: authority/environment binding больше не подтверждает прежний цикл. Новый check не выдаёт новые полномочия; нужен новый task после проверки причины.
- Review относится к прошлому snapshot или evidence set: `stale_review`; повторите review только для exact current bindings.
- Policy `independence: required`, trusted provider отсутствует: `independence_unknown`, UNKNOWN и NOT_CLAIMABLE. Нельзя исправить этот blocker полем JSON или собственным assertion агента.
- Malformed/tampered/unsupported journal: операция fail-closed. Не редактируйте записи вручную и не считайте HMAC proof человеческой identity.
- После recorded closure изменение состояния не продолжает прежний claim. Сохраняется bounded historical closure; новый state требует нового task.

## Agent review import

```sh
python3 -m taskclosurekit --store /tmp/taskclosurekit-store review /tmp/taskclosurekit-review.json --json
```

Формат файла — [schema taskclosurekit/review/v2](V2_CONTRACT.md). Exact contract/snapshot/evidence-set bindings обязательны. Импорт записывает agent-attested review; `approve` в таком JSON не удовлетворяет required human review и не доказывает identity или PROVEN independence. Для локального confirmed review используется отдельный `review --human` path. Источники доверия выбирает trusted producer, не автор входного JSON.

## Agent assertion

После authorize/baseline можно записать отдельное утверждение агента:

```sh
python3 -m taskclosurekit --store /tmp/taskclosurekit-store attest /tmp/taskclosurekit-assertion.json --json
```

Файл assertion находится вне repository и store и имеет ровно такие поля:

```json
{
  "schema": "taskclosurekit/assertion/v2",
  "task_id": "whitespace-demo-v2",
  "statement": "Разрешённое изменение подготовлено; это заявление агента."
}
```

Statement — строка длиной 1–4096 символов. Journal/status сохраняет только statement digest, canonical `agent-import` / `agent_attested`, contract/snapshot bindings, sequence и timestamp; raw statement не сохраняется и не возвращается. `assertions` отделены от `evidence`: наличие assertion не удовлетворяет criterion, не меняет trust и не закрывает transaction. Digest не гарантирует анонимность исходного текста. Не помещайте secrets, `.env`, tokens или персональные данные в contract, review, assertion и другие входные файлы; отсутствие raw statement в journal не защищает сам внешний input file.

## Restart и handoff

```sh
python3 -m taskclosurekit --store /tmp/taskclosurekit-store status --next --json
python3 -m taskclosurekit --store /tmp/taskclosurekit-store resume --json
```

`resume` — compatibility alias текущего status projection v2. Новый процесс читает журнал, проверяет integrity/semantic replay, заново сопоставляет bindings с текущим состоянием и показывает current/stale evidence, missing requirements через reasons, last_confirmed и next_action. Обе команды только читают transaction: не повторяют check, не записывают evaluation event и не replay-ят transcript.

Interrupted check не повторяется автоматически и блокирует claim до осмотра состояния и нового transaction. `status --next` возвращает action id, а не agent plan; это closure recovery, не memory system. Real CI/network adapter, SDK, completion hook, installer и deployment отсутствуют.
