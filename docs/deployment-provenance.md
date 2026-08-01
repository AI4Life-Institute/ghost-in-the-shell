# Which ghost code is live — `ghost doctor`

*Ghost task whlive.*

## Why this exists

On 2026-08-01 this machine ran **two coexisting ghost deployments and neither
pointed at master**, and answering "which code is live?" took an hour of
archaeology:

| role | ran from | state |
|---|---|---|
| `ghost` / `gits` CLI | uv tool snapshot under `~/.local/share/uv/tools/` | files dated 5 June; its uv receipt named a **local directory** as the requirement |
| hooks (`gits guard`) | `~/src/ghost-in-the-shell/.venv/bin/gits`, **editable** | that checkout was parked on a dirty feature branch |
| Discord bot | the same editable venv | — |

Three consequences, none hypothetical:

1. **The CLI was broken.** A new `GHOST_CORE_OS_MANDATE` key landed in
   `~/.gits/config.env`; the field was declared on master but not in the June
   snapshot, and `Settings` is `extra='forbid'` — so `ghost info` tracebacked.
   A pure *configuration* action injured a deployment nobody remembered to
   redeploy.
2. **Reinstalling was dangerous.** The receipt pointed at a dirty tree holding
   another ticket's untracked source, so `uv tool install --reinstall` would
   have packed unfinished code into the live tool.
3. **Hook behaviour was decided by that checkout's branch, not by master** —
   and these are hooks that *refuse operations*.

## What the command answers

```
ghost doctor                 # full report; exit 1 on provable drift
ghost doctor --json          # same, machine-readable
ghost doctor --fetch         # fetch first (default: as of the last fetch)
ghost doctor --compare-ref X # measure against something other than origin/master
ghost doctor --preinstall [PATH]
```

For each deployment — CLI, every hook binary named in a `~/.claude*/settings.json`,
and the running bot — it reports:

* **source and sha**, read from the installer's own :pep:`610` record
  (`direct_url.json`). A git install carries an exact `commit_id`; an editable
  install carries a path, which is then interrogated with git.
* **distance from `origin/master`**, and for editable checkouts the branch,
  dirtiness, and any untracked files under `src/` (the ones a wheel build would
  pack).
* **config compatibility**: which deployments do *not* declare a key that is
  already present in `~/.gits/config.env` — i.e. which ones a new key would
  crash. `extra='forbid'` is deliberately left alone; the fix for failure ①
  is visibility, not weaker validation.

### Verdicts

`clean` · `drift` (exit 1) · `unresolved`. **`unresolved` is never folded into
`clean`** — a probe that could not run is not evidence of agreement.

## The drift banner

The hooks stay **editable**: they are iterated on constantly, and a redeploy per
tweak pushes people toward disabling them. The concession is that `gits guard`
prints one line to **stderr** on its *allow* path when its own checkout is off
master or dirty.

Hard rules, each with a test in `tests/test_drift_banner.py`:

* stderr only — PreToolUse stdout is protocol;
* never on the refusal path, and wrapped so it can never change a verdict or
  exit code;
* rate limited to one banner (and one `git status`) per hour via
  `~/.gits/guard_drift_stamp`, because a notice on every tool call is noise,
  which is the same as silence.

Set `GHOST_GUARD_DRIFT_TTL=0` to silence it (the guard's own tests do).

## Honest limits

* **`--preinstall` prevents nothing.** A check you have to remember to run
  fails the same way that remembering to reinstall fails. It is worth having
  only because `doctor` is already the thing you reach for when something is
  wrong — not because it automates anything.
* Distance is measured **as of the last fetch** unless `--fetch` is passed.
* The config probe executes each deployment's own interpreter in a
  timeout-bounded subprocess. Every failure becomes `unknown` **with a reason**,
  never a silent pass.
* A dirty editable checkout means **the sha does not describe what runs**; the
  report says so rather than printing a sha that implies more than it knows.
