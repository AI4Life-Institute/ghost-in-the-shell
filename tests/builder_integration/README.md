# tv6q3n — isolated ghost ⇄ builder-os integration gate (codex gate #8)

The automated merge gate for `builder-os-rollout-v2 → master`. It drives the
**real** ghost builder chain (registry → event monitor → renderer → response
adapter → engine disposer) against the **real** builder-os CLI on a throwaway git
repo, with only the Discord transport and the unavoidable external providers
(github / Eva-LLM / reviewer-LLM) swapped for offline doubles.

Test file: `tests/test_builder_os_integration.py` (1 happy path + 10 fault
scenarios, incl. the codex-mandated #9 `request_changes` and #10 crash-between-
respond-and-dispose).

## Pinned refs

| repo | ref | carries |
|------|-----|---------|
| ghost | `builder-os-rollout-v2` @ `a91df9e` | B1-route/B2/B3/B4-ghost/B5/B6-ghost/B7 + `integrity_fault` + engine `_builder_dispose` |
| builder-os | `master` @ `ec23fdb` | #21 real publisher/disposition executors + #22 admit `--requester/--remote`, cleanup exit code, B6 `--resume`, B2 `capability_sha256` |

## Running

```
BOS_REPO=<path-to-pinned-builder-os-checkout> \
BOS_PYTHON=$BOS_REPO/.venv/bin/python \
PYTHONPATH=$PWD/src python -m pytest tests/test_builder_os_integration.py -q
```

`BOS_REPO`/`BOS_PYTHON` default to `/Users/sharon/src/builder-os` for local dev.
If the builder-os venv python is absent the module **skips** (loud reason) rather
than failing — CI must provision the pinned checkout so the gate actually runs.

## Shape (why it's honest)

* `bos_offline.py` — a **ghost-only** shim run as a subprocess under the
  builder-os venv python. It monkeypatches the five provider seams
  (`GhIssueSource`, `build_disposition_executor`, `build_publisher`,
  `build_reviewer_runner`, `eva.build_runner`) **inside its own process**, then
  calls the real `driver.cli.main`. No builder-os edit.
* disposition uses a `LocalGitDispositionExecutor` — a **real local `git merge`**
  on the throwaway clone, so "real merged/closed PR" (AC#1) is observable git
  state, not a recorded intent. A live github.com PR remains an optional
  post-merge smoke, out of this CI gate.
* everything else is real: the state machine, `events.jsonl`, `runtime-state`,
  the capability-token hash, stable exit codes (0/8/9), and the ghost chain.
