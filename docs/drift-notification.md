# Telling someone the deployment drifted — `ghost doctor --outstanding`

*Ghost task drftnt, closing [ghost#37](https://github.com/AI4Life-Institute/ghost-in-the-shell/issues/37).
Companion to [deployment-provenance.md](deployment-provenance.md) (task `whlive`), which made this
state **answerable**. This makes it **heard**.*

## Why a second piece

`ghost doctor` only helps someone who already suspects something. Drift is
precisely the condition nobody suspects.

On 2026-08-01, while seventeen PRs were merged with CI verified, master head
verified, and the post-merge run watched green on every one of them, the live
`builder-os` checkout sat 18 commits behind master all day, the ghost uv
snapshot was 1 behind and the editable checkout 10. Every step of that merge
discipline answers *"is the code right?"*. **No step answers "is it running?"**

## The part that is a policy question, not an implementation

Two failure modes bracket this feature, and **both end in silence**:

* **The latch.** "Report each drift once" inverts urgency. A drift unresolved a
  week later matters *more* than on day one — and that is exactly when a naive
  latch has stopped speaking.
* **The noise.** Alerting on "not on master" fires on every ordinary working
  branch, gets muted, and then does not ring when it should.

So the alert predicate is deliberately **not** `Report.errors`. Doctor grades
`not-on-master` and `dirty-worktree` as errors, which is correct for a
diagnostic you *invoke*: it answers "is this exactly master?". It is wrong for
something that interrupts people, because that is the shape of somebody
working. The two commands answer different questions on purpose.

## What counts as alertable drift

A deployment is **tended** when its checkout is dirty or on a non-main branch
**and** shows human activity within 24h. Tended deployments are recorded but
never pushed to. Activity is measured as the newer of the HEAD commit date and
the mtimes of the dirty files themselves — deliberately *not* the index mtime,
because reading a repo's status can rewrite the index and a scanner keyed on
that would keep marking every tree it looked at as freshly active, suppressing
itself into silence. (`inspect_worktree` also passes `--no-optional-locks` so
the scan cannot disturb a checkout somebody else is working in.)

Past that TTL the tending claim expires and becomes a `stale-tended` finding.
Otherwise "someone is working here" is a permanent excuse, and an abandoned
dirty tree that the *hooks* run from would be immune forever.

For everything else:

| condition | when it speaks |
|---|---|
| `config-key-missing` | immediately — that deployment is broken today, not drifting toward broken |
| `behind` ≥ 5 | immediately |
| `behind` ≥ 1 | once it has been outstanding 24h; below that it is a normal post-merge window |
| commits touching the hook surface | drops the age gate above |
| `distance-unmeasured` | after 6h — one flaky fetch is not an incident, a day of them is |
| no deployments scanned at all | immediately |

**Suppression hides the notification, never the record.** A tended deployment
18 commits behind is still in the ledger and still answers
`ghost doctor --outstanding`. Quiet is not the same as forgotten.

### "Commits touching the hook surface" is named for what it counts

The escalation signal counts commits in `sha..origin/master` that touch
`src/gits/hooks/` — the code paths that *refuse operations*, so running a stale
copy of them changes what the machine permits.

It is **not** a security-fix detector and is never described as one. This
repository has no security-marker convention; inventing one is a larger change
than this ticket, and a claim written into wording gets believed every time it
is read.

## The three dedupe questions (ghost#37)

**1. Does the dedupe key include the state?** Coarse state only. The key is
`(executable, finding code)` — **not** the sha (that re-fires whenever *master*
moves, which is the wrong axis) and **not** the commit count (re-fires on every
merge). Severity *bands* (1–4 / 5–19 / 20+) live in the value, and crossing one
upward re-arms immediately. "1 behind" and "18 behind" are one incident, but the
second is worse, and worsening speaks.

**2. Can the outstanding set be queried?** `ghost doctor --outstanding`, over
`~/.gits/drift_incidents.json`. It lists what is unresolved, **including drift
that was deliberately not notified about**, what was recently resolved, whether
each send succeeded, and when the watcher last scanned. Exit 1 while anything
alertable is outstanding.

Two ways that query could itself go quiet are closed: a machine where the
watcher has never run prints *"never run — unknown, not a clean bill of
health"* rather than an empty reassuring list, and a watcher that has stopped
scanning says so above the results.

**3. What re-arms it?** Three things, each observable:

* **A capped backoff ladder** — 6h → 24h → 72h, and never longer, because
  week-old drift is more urgent, not less. Every re-notice carries how long the
  drift has been outstanding and which notice this is, which is what turns
  repetition into pressure instead of noise.
* **Escalation** — a severity band increase, or a suppression lifting, notifies
  now and resets the ladder to its first rung.
* **Resolution** — one closing notice, so you learn the channel is alive rather
  than inferring it from silence. Only incidents that were actually announced
  get closing notices. A new drift afterwards notifies immediately, no timer.

A **restart is deliberately not a re-arm**: the ledger is on disk, so a bouncing
bot neither replays everything nor forgets what it owes.

**A send that fails does not advance `last_notified_at`.** It records the error
and retries on the next scan. Delivery failure must not be indistinguishable
from delivery — that is one more way a notifier goes quiet while looking fine.

## Fetching, and the signal it costs

A watcher that never fetches measures `0 behind` forever. So it fetches — and
narrowly: `git fetch --no-tags --quiet origin`, no `--prune`, no refspec, no
`--force`. That writes only remote-tracking refs, which is what makes it safe
against a checkout someone else is working in.

**A failed fetch is reported as `unmeasured`, never as `behind=0`.** Against
stale refs `behind` reads 0, and 0 looks exactly like health — one network blip
would otherwise impersonate a clean machine. This is the same vacuum as a scan
that found no deployments. Real drift found against stale refs is still
reported: a stale ref proves a lower bound.

**The cost:** `.git/FETCH_HEAD`'s mtime is how one dates a deployment's last
pull, and anything fetching on a cadence overwrites it. The signal is therefore
**relocated rather than destroyed**: before each fetch the watcher reads the
existing `FETCH_HEAD` mtime, and any value it did not write itself is kept as
`last_foreign_fetch` and printed by `--outstanding`.

## Where notices go

To the **butler home channel** of the checkout the bot runs from — not
`_broadcast_to_bindings`, which fans out to every session binding. A
machine-level alert delivered into everyone's working session is the noise that
gets the whole mechanism muted (operator answer Q1, 2026-06-01).

No new channel setting was added. ghost#18 proposes
`GITS_WATCHDOG_ALERT_CHANNEL` for the same job and has not landed; if it does,
the two should converge on one key rather than each owning half the routing.

## Configuration

Two keys, both declared in `gits.config.Settings`:

| key | default | meaning |
|---|---|---|
| `GHOST_DRIFT_WATCH_ENABLED` | `true` | run the scan at all |
| `GHOST_DRIFT_WATCH_INTERVAL_S` | `3600` | cadence; always far below the fastest ladder rung, so scan rate never sets notification rate |

The thresholds are constants in `DriftPolicy`, not settings. `~/.gits/config.env`
is validated with `extra='forbid'` (a pydantic *default*, invisible in
`model_config`), so every key added is a key that must exist forever, and an
undeclared one takes down every `Settings()` in the bot, the hooks and the CLI.
See ghost#18, and `tests/test_config_drift_keys.py`, which writes a real
config.env and constructs the model — the test whose absence let 26 undeclared
keys pass CI for two months.

## Constraints honoured

* **No new daemon.** `DriftWatcher` is an asyncio task on the existing engine
  loop, shaped like `TokenRefreshScheduler`.
* **Not reachable from the guard.** The guard runs on every tool call and gains
  no network call, no notification path, and no opinion here. The stderr drift
  banner from `whlive` is unchanged and remains the guard's only say.
* **No refusal path or exit code touched.**

## Honest limits

* Tending is inferred from file mtimes and commit dates. Someone thinking hard
  without saving anything for 25 hours reads as absent.
* The age gate starts when the watcher *first saw* the drift, not when the drift
  began. A watcher that was down for a day starts its clock late.
* The hook-surface count is a proxy for "this drift matters more". It has no
  recall guarantee for anything else that matters.
* Notices depend on a bound butler home channel. Unbound, the incident stays
  outstanding with a recorded send error and is visible only to
  `--outstanding` — which is the honest failure, but still a quiet one.
