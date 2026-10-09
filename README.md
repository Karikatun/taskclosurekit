# TaskClosureKit

[English](README.md) · [Русский](README.ru.md)

TaskClosureKit is a local CLI that helps AI coding agents and engineers decide when an engineering task can be closed. It binds the agreed scope, repository state, check results and review into a recorded, limited completion claim.

A passing test can stop being relevant after another edit. An approval can refer to an older diff. A new agent can inherit a “done” message without supporting evidence. TaskClosureKit reports which evidence is current, what blocks closure and which action is permitted next.

Its claim is **`configured-acceptance-satisfied`**: the required criteria in the task contract are satisfied by admissible, current evidence for the bound state. Its strength depends on the criteria you configure.

## How it works

```text
create → authorize → baseline → implement and stage → check
       → evaluate → review → evaluate → CLAIMABLE → close → CLOSED
```

- A **contract** names the repository, allowed reads and writes, check IDs, acceptance criteria and review policy.
- **Checks** run trusted presets and record results bound to the contract, repository and execution environment.
- **Evaluation** checks authority, scope, evidence freshness and review. Historical `PASS` results can become stale; missing or unknown proof blocks the claim.
- **Review** applies to the exact snapshot and evidence set. Required independence stays blocked without trusted proof of independence.
- **Closure** is a separate operator action. `CLAIMABLE` means the conditions currently permit the claim; `CLOSED` means the operator has recorded it.

`status --next --json` recovers the persisted task state and blockers for another process without replaying a conversation.

## Installation and requirements

The source and local npm package version is **2.1.0**. The package ships the existing Python standard-library CLI behind one dependency-free `taskclosurekit` Node launcher. There are no third-party Node/Python dependencies, install scripts, runtime downloads or automatic store updates. No npm registry publication or package-name availability is confirmed.

You need Python **>=3.9**, macOS or Linux with the required POSIX resource-limit APIs, and system Git under `/usr/bin` or `/bin`. The npm launcher also requires Node **>=22** and npm/npx. Windows is unsupported. Local validation covers macOS, Python 3.9.6, Node 22.23.1 and npm 10.9.8; Linux and other runtime versions have no verified matrix here.

With a supplied local tarball, no Git clone is needed:

```sh
npx --offline --yes --ignore-scripts --package=/absolute/path/taskclosurekit-2.1.0.tgz -- taskclosurekit --help
npx --offline --yes --ignore-scripts --package=/absolute/path/taskclosurekit-2.1.0.tgz -- taskclosurekit --store /absolute/path/store status --next --json
```

These commands consume a local archive; they do not fetch an unpublished registry package. After a separately authorized publication, a pinned registry version could replace the archive. npm manages its own cache; review the package you supply.

The launcher selects `/usr/bin/python3`, then `/bin/python3`, using the first existing candidate. It does not search npm's augmented `PATH` or retry another interpreter after a runtime failure. To use another preinstalled interpreter, set `TASKCLOSUREKIT_PYTHON` to its absolute executable path. The path is resolved once; its bytes/path/version remain part of the existing Python binding. The explicit override is a trusted executable choice, not a Python installer or sandbox.

Python runs with `-I -S -B` and imports the bundled CLI from the package location. Caller cwd, argument boundaries, stdin/stdout/stderr, terminal confirmations and exit codes are retained. Project modules, `PYTHONPATH` and site customization cannot replace bundled imports. Node and npm themselves are not attested by the existing execution identity. See [CLI details](docs/CLI.md#npm-launcher).

Source-checkout use remains available:

```sh
git clone https://github.com/Karikatun/taskclosurekit.git
cd taskclosurekit
python3 -B -m taskclosurekit --help
```

The quick start below runs from this checkout and reads bundled example files. For a supplied archive, replace each CLI invocation with the local npx prefix above and keep example inputs outside the checked repository/store. `-B` avoids Python bytecode. Keep the tool payload unchanged during a task: its execution identity includes `taskclosurekit/` and `tests/`.

To produce a local archive from a reviewed checkout, use `npm pack --offline --ignore-scripts --pack-destination /absolute/path/to/output`. This packages files without publishing them. The archive includes both identity roots, documentation and public examples; its manifest uses `UNLICENSED` to preserve the absence of a license grant.

The **repository being checked** must have an initial commit and an ordinary `.git` directory with SHA-1 loose objects. Packed objects, alternates and linked worktrees are rejected. A typical cloned target repository may therefore be unsupported. The quick start creates a supported disposable repository.

## Quick start

This example changes one Python file and checks **staged whitespace only**. It demonstrates task closure; it does not test the Python program's behavior.

### 1. Prepare an isolated example

This block creates a unique temporary directory containing a repository, an external contract and, later, a separate store. It reads the bundled contract as JSON and creates a baseline commit with a fixture identity. Your projects and global Git configuration are unaffected.

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

Keep the printed path. The store must not exist before `task create`, which creates it with private permissions. Paths must be absolute and physically outside the checked repository. Contract, review, assertion and preset configuration files must also stay outside the store. Symlink aliases are rejected.

### 2. Create and authorize the task

Run each command separately in an interactive terminal:

```sh
python3 -B -m taskclosurekit --store "$TASKCLOSUREKIT_DEMO/store" task create "$TASKCLOSUREKIT_DEMO/contract.json" --json
python3 -B -m taskclosurekit --store "$TASKCLOSUREKIT_DEMO/store" authorize --json
python3 -B -m taskclosurekit --store "$TASKCLOSUREKIT_DEMO/store" baseline --json
```

`authorize` asks you to type the exact displayed action digest after inspecting the contract. Later, `review --human` and `close` require their own terminal confirmations. JSON output does not bypass confirmation; piped input cannot provide it. For real tasks, these are operator decisions. An agent assertion cannot replace them.

### 3. Make the permitted change and check it

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

The check should record `PASS`. Evaluation should return `NOT_CLAIMABLE`, reason `missing_trusted_review` and exit code **1**, because review is still missing. This is the expected intermediate result.

### 4. Review and close

Inspect the staged diff first. Then run each TaskClosureKit command separately and confirm the review and closure prompts:

```sh
git -C "$TASKCLOSUREKIT_DEMO/repo" diff --cached
python3 -B -m taskclosurekit --store "$TASKCLOSUREKIT_DEMO/store" review --human --json
python3 -B -m taskclosurekit --store "$TASKCLOSUREKIT_DEMO/store" evaluate --json
python3 -B -m taskclosurekit --store "$TASKCLOSUREKIT_DEMO/store" close --json
python3 -B -m taskclosurekit --store "$TASKCLOSUREKIT_DEMO/store" status --next --json
```

Evaluation should become `CLAIMABLE`; explicit closure should record `CLOSED`. Later edits require fresh checks and review, or a new task when authority changes. A recorded closure remains a claim about its original bound state.

## Using it in your workflow

Start each real task with a new contract and store. Declare narrow scopes and meaningful acceptance criteria. The built-in `git-index-whitespace-v1` checks only staged whitespace. For tests, type checking or builds, supply a separately reviewed trusted preset configuration at creation:

```sh
python3 -B -m taskclosurekit --store /absolute/path/to/new-store task create /absolute/path/to/contract.json --preset-config /absolute/path/to/presets.json --json
```

The contract selects preset IDs; executable paths, arguments, environment, limits, inputs and permitted outputs belong to the trusted configuration. Adapt and inspect the [engineering example](examples/contract-engineering-v21.json) and [preset template](examples/presets-engineering-v21.json) before use. They do not install their runtimes.

Keep contract sources unchanged, write only within the agreed scope, and rerun affected checks after edits. Tool, configuration or runtime changes can invalidate authority or execution evidence. Conservative snapshots can also make earlier checks stale when another check creates outputs. Follow the reported blocker rather than editing JSON to claim greater trust.

For machine consumers, read `operational.status` first, then `decision`, `reasons` and freshness. `operational.status: "ok"` can accompany a blocked claim. Exit codes: **0** successful unblocked operation; **1** policy-blocked claim; **2** invalid input/state; **3** environment/internal failure; **4** stale or unknown state. The [CLI reference](docs/CLI.md#machine-envelope) describes the envelope and failure branches.

## Trust and current limits

- Scope is checked during evaluation. It does not sandbox an agent or prevent host reads and writes.
- Terminal confirmation assumes a local operator; it does not verify human identity or prove reviewer independence. Imported reviews and assertions retain agent-attested trust.
- Local HMAC records detect supported integrity failures. An equally privileged host process can replace the program, key and journal together.
- Preset execution has bounded time/output and POSIX resource limits; address-space limits apply only on Linux. Raw check output is discarded rather than stored.
- The claim covers configured acceptance for one bound task state. It does not establish universal correctness, security certification or production readiness.

The repository contains a local CLI and tests. It has no network CI adapter, SDK, completion hook, Python runtime installer or deployment service. The CI interface has a test adapter, which is not evidence from a real CI provider. See [product boundaries](docs/PRODUCT_BOUNDARIES.md) and the [contract model](docs/V2_CONTRACT.md).

Do not put credentials, source `.env` files or personal data in task inputs or evidence. The journal stores task metadata and snapshots; discarding check output does not make the other files anonymous.

## Removal and task data

Stop invoking the CLI and remove your own source checkout or supplied archive when no longer needed, after checking for local work you want to keep. npm cache entries are separate; remove only entries you have identified and chosen to discard. Remove any wrapper or workflow integration you added separately. There is no TaskClosureKit data-removal hook.

Task data is independent of the source checkout. Each `--store` directory contains the journal and its local HMAC key. External contracts, preset configurations and imported review/assertion files are separate. Removing the tool does not remove these files or the checked repository.

Program updates change execution identity. Earlier baselines and PASS receipts do not become current automatically; use a new agreed task and store after checking the state. There is no previous CLI alias or journal importer. See [compatibility](docs/COMPATIBILITY.md) and [safety boundaries](docs/SAFETY_BOUNDARIES.md).

Before deleting a store, decide whether its evidence must be retained. To keep the record, preserve the complete private store, including the key, together with relevant external inputs. Live recovery also depends on the original bound paths, repository state and execution identity; an archive is not automatically resumable. Deleting the key or journal loses verifiable recovery. Remove only the exact directories and files you chose to discard. The quick start's printed temporary directory contains all demonstration data.

## Contributing

For source contributions, read [AGENTS.md](https://github.com/Karikatun/taskclosurekit/blob/master/AGENTS.md), [CONTEXT.md](CONTEXT.md) and the documentation for the area you change. Preserve unrelated work, define scope and acceptance criteria before editing, and validate affected behavior. Repository instructions govern engineering work; this README does not grant agents additional permissions.

The test suite uses disposable fixtures and Python's standard test runner:

```sh
python3 -B -m unittest discover -s tests -v
```

If the system temporary directory is heavily populated, use a temporary directory with few entries for fixture runs: physical-path validation is bounded. A passing local suite does not establish remote CI, publication or deployment.

## Documentation

- [CLI commands, JSON results and recovery](docs/CLI.md)
- [Contracts, presets and evidence bindings](docs/V2_CONTRACT.md)
- [Product model](docs/PRODUCT_BRIEF.md) and [boundaries](docs/PRODUCT_BOUNDARIES.md)
- [Engineering fixture](examples/engineering-fixture/README.md)

Detailed documentation is currently in Russian. Both README files describe the same current functionality.

## License

This repository currently contains no `LICENSE` file or declared software license. A license has not been specified here.
