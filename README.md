# TaskClosureKit

[English](README.md) · [Русский](README.ru.md)

TaskClosureKit is a local command-line tool for engineers and AI coding agents.
It checks whether an engineering task meets its agreed acceptance criteria.
It records the evidence and lets an operator close the task.

Start here:

```sh
npx taskclosurekit --help
```

npm can ask for permission to download the package on first use.
A Git clone is not necessary.

## Requirements

- Node **22 or later**, with npm/npx.
- Python **3.9 or later**.
- macOS or Linux with POSIX resource-limit APIs.
- System Git in `/usr/bin` or `/bin`.

The tool does not support Windows.
Local checks cover macOS with Python 3.9.6, Node 22.23.1 and npm 10.9.8.
Linux and other versions have no verified test matrix here.

The launcher uses the first existing Python in `/usr/bin/python3`, then `/bin/python3`.
For another installed Python, set `TASKCLOSUREKIT_PYTHON` to its absolute executable path.
The package has no third-party dependencies or install scripts.
It does not install Python or migrate existing stores.

## How a task closes

A **contract** defines the repository, allowed reads and writes, checks, acceptance criteria and review requirements.
A **store** holds the task journal and its local HMAC key.
Each check result applies to a specific contract, repository state and execution environment.
Changes can make an earlier `PASS` result stale.

```text
create → authorize → baseline → implement and stage → check
       → evaluate → review → evaluate → CLAIMABLE → close → CLOSED
```

`CLAIMABLE` means that current evidence permits the completion claim.
`CLOSED` means that the operator has recorded that claim with a separate `close` action.
The claim is `configured-acceptance-satisfied`.
It covers the configured criteria for one task state.
It does not prove complete correctness or production readiness.

## Run your first task

### 1. Prepare the contract

Open the [example contract](examples/contract-v2.json).
Save its JSON as a file outside your target repository.
Set its `repository` to the absolute physical path of your repository.
Set the task ID and permitted file paths for your task.
Inspect the complete contract before use.

The example permits changes to `src/foo.py` and requires `README.md` as an unchanged source.
Its check, `git-index-whitespace-v1`, checks only whitespace in staged changes.
It does not test program behavior.
For tests or builds, use a reviewed [preset configuration](examples/presets-engineering-v21.json).
See the [engineering contract](examples/contract-engineering-v21.json) and [CLI reference](docs/CLI.md#real-engineering-checks).

The target repository must have an initial commit and an ordinary `.git` directory with SHA-1 loose objects.
The tool rejects packed objects, alternates and linked worktrees.
A typical cloned repository can therefore be unsupported.
Other Git restrictions appear in [safety boundaries](docs/SAFETY_BOUNDARIES.md#git).

Use absolute physical paths for the contract and store.
Keep both outside the target repository.
Keep the contract outside the store.
The store directory must not exist before `task create`.
The tool rejects symlink aliases.

### 2. Create and authorize the task

Replace the example paths below with your own paths.
Keep these variables in the same terminal for the remaining steps.
Run each command separately in an interactive terminal.

```sh
TASK_STORE=/absolute/path/to/new-store
TASK_CONTRACT=/absolute/path/to/contract.json
npx taskclosurekit --store "$TASK_STORE" task create "$TASK_CONTRACT" --json
npx taskclosurekit --store "$TASK_STORE" authorize --json
npx taskclosurekit --store "$TASK_STORE" baseline --json
```

Before confirmation, inspect the contract.
For `authorize`, type the exact action digest shown in the terminal.
`review --human` and `close` need separate operator confirmations.
JSON output and piped input cannot replace these confirmations.
An agent assertion cannot replace an operator decision.

### 3. Make and check the change

Change only the permitted files.
Stage those changes with Git.
Run the check from the example contract:

```sh
npx taskclosurekit --store "$TASK_STORE" check git-index-whitespace-v1 --json
npx taskclosurekit --store "$TASK_STORE" evaluate --json
```

A successful check records `PASS`.
Before review, evaluation returns `NOT_CLAIMABLE` with reason `missing_trusted_review` and exit code **1**.
This result is expected.

### 4. Review and close the task

Inspect the staged diff in the target repository.
Run each command separately.
Confirm review only after you have inspected the current changes and evidence.

```sh
npx taskclosurekit --store "$TASK_STORE" review --human --json
npx taskclosurekit --store "$TASK_STORE" evaluate --json
npx taskclosurekit --store "$TASK_STORE" close --json
npx taskclosurekit --store "$TASK_STORE" status --next --json
```

If evaluation returns `CLAIMABLE`, confirm the separate `close` action.
Successful closure records `CLOSED`.
`status --next --json` shows current evidence, blockers and the next permitted action.
It can recover task state for another process without the chat history.

Before closure, repeat the affected checks after an edit.
Then, repeat review for the current state.
After closure, use a new task for a new state.
If authority changes, create a new agreed task and store.
Program updates can also invalidate earlier evidence.
For a fixed package version, use `npx taskclosurekit@2.1.0` instead of `npx taskclosurekit` throughout a task.

## Trust and privacy

- Scope checks do not create an operating-system sandbox or prevent host file access.
- Terminal confirmations assume a local operator. They do not verify human identity or reviewer independence.
- Required reviewer independence blocks the claim without trusted proof. Imported reviews and assertions remain agent-attested.
- Local HMAC records detect supported integrity failures. A process with equal host privileges can replace the tool, key and journal together.
- Check execution has time, output and POSIX resource limits. Address-space limits apply only on Linux.
- The tool discards raw check output. The journal still contains task metadata and snapshots.

Do not put credentials, source `.env` files or personal data in task inputs or evidence.
The tool has no network CI adapter, SDK, completion hook or deployment service.
Its test CI adapter does not prove results from a real CI provider.
See [product boundaries](docs/PRODUCT_BOUNDARIES.md) and [safety boundaries](docs/SAFETY_BOUNDARIES.md).

## Task data and removal

Removing the tool does not remove stores, external inputs or the target repository.
Before deletion, decide which task evidence you must keep.
Keep the complete private store, including its key, with the relevant external inputs.

Recovery also needs the original paths, repository state and execution identity.
An archive alone does not guarantee recovery.
Deleting the journal or key loses verifiable recovery.

Remove only the exact files and directories you choose to discard.
npm manages its cache separately.
See [compatibility and updates](docs/COMPATIBILITY.md).

## Source use and contributions

From a source checkout, use `python3 -B -m taskclosurekit --help`.
For contributions, read [AGENTS.md](https://github.com/Karikatun/taskclosurekit/blob/master/AGENTS.md), [CONTEXT.md](CONTEXT.md) and the documents for your change.
Repository instructions govern this work.
This README does not grant additional agent permissions.

Run the source test suite with:

```sh
python3 -B -m unittest discover -s tests -v
```

Local tests do not prove remote CI or deployment.

## Reference

- [CLI commands, JSON results and recovery](docs/CLI.md)
- [Contracts, presets and evidence](docs/V2_CONTRACT.md)
- [Product model](docs/PRODUCT_BRIEF.md)
- [Engineering fixture](examples/engineering-fixture/README.md)

Detailed documents are in Russian.
Both README files describe the same functionality.

## License

The repository has no `LICENSE` file or declared software license.
The npm package uses `UNLICENSED`; this value does not grant a software license.
