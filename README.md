# agentgate

A small, readable example of how to let AI coding agents change a codebase **without letting them approve their own work**.

Agents are fast and unreliable. Left alone they edit files they should not touch, say "done" when the tests fail, and cost a lot when every step runs on the most expensive model. This project shows one way to handle that:

- every task has a written **scope**: files it may change, files it must not touch;
- a fixed **lifecycle** that no one can skip;
- **validators** that check the result instead of trusting the worker's report;
- an independent **second-model review gate** that must answer `APPROVE` or `REJECT`;
- **risk levels**: only low-risk work can be approved automatically, anything else needs a human;
- a **cost rule**: a cheap tier implements, a premium tier reviews, and the same unchanged work is never reviewed twice.

It runs fully offline. The demo uses deterministic fake workers and a fake reviewer, so it needs no API keys, no network and no model. It has no dependencies (pytest only for the tests).

## Quickstart

Python 3.12 or newer.

```
pip install -e ".[dev]"
python -m agentgate demo
python -m pytest
```

(`pip install` is optional: both commands also work straight from the repository folder.)

The demo runs the same toy task four times, with four workers, so each layer is seen catching something:

```
Run 1: good worker, edits only discount.py
  review    premium tier -> APPROVE: claim matches check results
  validate  scope=pass tests=pass secrets=pass
  decide    approved by auto-policy (risk=low)
  result    DONE

Run 2: worker that also rewrites the test file
  review    premium tier -> REJECT: out of scope: test_discount.py is forbidden
  result    REJECTED

Run 3: worker that says it is done but is not
  review    premium tier -> REJECT: claim of success contradicted by checks (3 failed, 1 passed)
  result    REJECTED

Run 4: worker that leaves a credential in discount.py
  review    premium tier -> APPROVE: claim matches check results
  validate  scope=pass tests=pass secrets=fail
            secrets: assigned credential in discount.py
  result    VALIDATION_FAILED
```

(Shortened here; the real output also shows each worker's claim. Run 4 is the point of having both layers: the reviewer looked at the diff and approved it, and the validator still caught what the reviewer did not look for. The scope validator also stops an out-of-scope change on its own, even when the reviewer approves it; the tests cover that case.)

## Architecture

```
            task.json (scope, checks, risk)
                     |
                     v
              +--------------+     picks tiers      +---------+
              |  Supervisor  |--------------------->|  Router |
              |  (lifecycle, |                      +---------+
              |   state)     |                       cheap -> workers
              +--------------+                       premium -> reviewers
                |    |    |                          no repeat reviews
   dispatch     |    |    |  validate
        +-------+    |    +--------+
        v            | submit      v
  +-----------+      v        +------------+
  |  Worker   |  +--------+   | Validators |
  |  adapter  |  | Review |   |  scope     |
  | (cheap)   |  |  gate  |   |  tests     |
  +-----------+  |(premium|   |  secrets   |
   edits files   | APPROVE|   +------------+
   in workspace  | /REJECT)|        |
                 +--------+         v
                                Policy: low risk -> auto-approve
                                        otherwise -> human decision
```

Package layout:

| File | What it does |
|---|---|
| `agentgate/schema.py` | The task record, statuses, legal status transitions |
| `agentgate/policies.py` | Protected files, scope rules, who may approve what |
| `agentgate/validators.py` | Scope check, tests-pass check, no-secret check, rollup |
| `agentgate/workspace.py` | Baseline copy, changed files and diff (no git needed) |
| `agentgate/adapters/base.py` | The worker interface: `dispatch`, `poll`, `fetch_result` |
| `agentgate/adapters/fake.py` | Deterministic fake worker |
| `agentgate/adapters/command.py` | Generic worker that runs any command-line tool you configure |
| `agentgate/adapters/reviewer.py` | Review gate: fake reviewer, command reviewer, verdict parsing |
| `agentgate/routing.py` | Cost tiers, different-family rule, repeat-review refusal |
| `agentgate/supervisor.py` | The lifecycle and the task store |
| `agentgate/cli.py`, `demo.py` | Command line and the demo |
| `examples/` | The toy project and its task file |

## Lifecycle

```
create -> start -> dispatch -> submit -> validate -> decide -> close
```

| Step | What happens | Can stop here |
|---|---|---|
| create | Task is checked against the schema and policy. A task that asks for forbidden or protected files is born `blocked`. | `blocked` |
| start | The workspace is copied as the baseline. | |
| dispatch | A cheap-tier worker edits files in the workspace. Its summary is kept as a *claim*, not as proof. | `blocked` if the worker cannot run |
| submit | The diff is taken against the baseline, the acceptance checks are run, and the premium reviewer, from a different model family, must answer `APPROVE` or `REJECT`. It sees the scope, the diff, the worker's claim and the check results. An unclear answer is an error, never an approval. | `rejected` |
| validate | Scope check (only allowed files changed), tests-pass check (your acceptance commands), no-secret check (added lines only; reports the file, never the value). | `validation_failed` |
| decide | Approve, reject, or needs-changes. `auto-policy` may approve only `low` risk tasks with no policy flags. Medium, high and critical wait for a person. | `rejected` |
| close | An approved task becomes `done`. | |

After `validation_failed`, `rework_task` returns the task to `in_progress`. Submitting identical work again is refused: the review is not bought twice.

Every step can also be run on its own from the command line, with state kept in `.agentgate/`:

```
python -m agentgate create examples/task.json
python -m agentgate start T-0001 --workspace path/to/project
python -m agentgate dispatch T-0001
python -m agentgate submit T-0001
python -m agentgate validate T-0001
python -m agentgate decide T-0001 --decision approved --by <your name> --rationale "checked the diff"
python -m agentgate close T-0001
python -m agentgate run examples/task.json --workspace path/to/project   # all steps at once
```

## Plugging in a real model

`CommandWorker` and `CommandReviewer` run any command-line tool you choose. The prompt arrives on the command's standard input, and the command runs inside the workspace. The code contains no vendor names, keys or endpoints; whatever login the tool needs stays in the tool's own configuration.

```
# the worker: a command that reads the prompt on stdin and edits files in its working directory
export AGENTGATE_WORKER_CMD="your-cheap-model-cli --non-interactive"
export AGENTGATE_WORKER_FAMILY=vendor-a

# the reviewer: a command that reads the prompt on stdin and prints APPROVE or REJECT first
export AGENTGATE_REVIEWER_CMD="your-premium-model-cli --no-tools"
export AGENTGATE_REVIEWER_FAMILY=vendor-b

python -m agentgate run examples/task.json --workspace path/to/copy-of-project
```

The two families must differ; the router refuses to use a reviewer from the same family as the worker. From Python, write your own adapter by subclassing `WorkerAdapter` (three methods) or `Reviewer` (one method) and pass it to `Router([...workers], [...reviewers])`.

Run it on a **copy** of a project first. A worker edits files in the workspace directly.

## What is left out

This is a teaching sample, not a product.

- No git integration. Changes are found by comparing against a copied baseline.
- No sandbox for workers. The generic adapter runs your command with your user's rights.
- No parallel tasks, queues, retries or timeouts beyond a simple per-command limit.
- The fake reviewer is deliberately simple. It rejects an empty diff, a change to a file outside the task's scope, and a success claim that the check results contradict. A real reviewer also judges whether the diff actually meets the goal.
- The secret scan is a handful of patterns, not a full scanner.
- No desktop-app or chat-window adapters, no bounded "no tools, one turn" reviewer mode, no hashing of review sources.
- No web interface and no cost accounting; routing is by tier name only.

## Licence

MIT. See `LICENSE`.
