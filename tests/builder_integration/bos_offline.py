"""Offline ``builder-os`` CLI shim for the ghost integration gate (tv6q3n).

This is the seam-swap the tv6q3n harness drives instead of the bare ``builder-os``
console script. It runs as a **subprocess** under the builder-os venv python, so
it executes the *real* :mod:`driver.cli` dispatch, the real state machine, the
real ``events.jsonl`` / ``runtime-state`` / capability-token hash, and the real
stable exit codes. Only the **unavoidable external providers** are swapped for
offline doubles (matching builder-os's own T4/T5 DI discipline — real behaviour
on fakes, never production stubs on the live path):

* issue fetch (``gh api``)         → :class:`FixtureIssueSource` seeded from env
* PR publish (``git push``/``gh``) → :class:`StubPrPublisher` (branch ref already
  lives in the local clone, so a publish is a no-op record)
* disposition (``gh pr merge``)    → :class:`LocalGitDispositionExecutor` — a
  **real local ``git merge``** on the throwaway clone (AC#1 "real merged PR"),
  never a stub that only records intent
* Eva / Reviewer LLM runners       → conforming stubs (the scripted driver injects
  review verdicts explicitly via the real ``ingest-review`` verb)

The shim is intentionally ghost-only: it imports builder-os and monkeypatches the
five ``build_*`` / issue seams **inside its own process** before calling
``driver.cli.main`` — no builder-os edit, no single-writer contention.

Env contract (set by the ghost harness):
    BOS_CHECKOUT_ROOT   runtime-state root (real builder-os env var)
    BOS_LOCAL_CONFIG    local-config.yaml with clones_root (real builder-os env var)
    BOS_OFFLINE_ISSUE   JSON {number,title,body,labels} for the fixture issue
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def _fixture_issue_source_factory():
    """A zero-arg callable that stands in for ``GhIssueSource()`` in ``admit``."""
    from driver.runtime.issue import FixtureIssueSource, Issue

    raw = os.environ.get("BOS_OFFLINE_ISSUE")
    if not raw:
        raise SystemExit("bos_offline: BOS_OFFLINE_ISSUE is required for admit")
    data = json.loads(raw)
    issue = Issue(
        number=int(data["number"]),
        title=data.get("title") or "",
        body=data.get("body") or "",
        labels=list(data.get("labels") or []),
    )
    return FixtureIssueSource({issue.number: issue})


def _local_git_disposition_executor():
    from driver.core.config import resolve_clone_path
    from driver.runtime.dispose import DispositionError, DispositionExecutor

    class LocalGitDispositionExecutor(DispositionExecutor):
        """Real local ``git merge`` / close on the throwaway clone (no GitHub).

        The ticket branch ``bos/<n>`` is a real ref in the clone's object store
        (the ticket worktree was cut from it), so ``merge`` folds it into the
        clone's default branch with a real merge commit — an observable git fact
        the QA assertions inspect. Idempotent: an already-merged branch is a
        clean no-op (mirrors the real executor's PR-state probe). ``cleanup``
        still owns local branch deletion (M3), so neither op deletes the branch.
        """

        def _git(self, clone, *args, check=True):
            return subprocess.run(
                ["git", "-C", str(clone), *args],
                capture_output=True, text=True, check=check,
            )

        def merge(self, paths, *, branch: str) -> dict:
            clone = resolve_clone_path(paths.repo_alias, paths.layout)
            # already merged? (branch tip is an ancestor of the default HEAD)
            probe = self._git(clone, "merge-base", "--is-ancestor", branch, "HEAD",
                              check=False)
            if probe.returncode == 0:
                return {"action": "merge", "executed": True, "already": True,
                        "branch": branch}
            res = self._git(clone, "merge", "--no-ff", "-m",
                            f"merge {branch} (disposition)", branch, check=False)
            if res.returncode != 0:
                raise DispositionError(
                    f"local merge of {branch!r} failed: {res.stderr.strip()}")
            return {"action": "merge", "executed": True, "already": False,
                    "branch": branch}

        def close_without_merge(self, paths, *, branch: str) -> dict:
            # No merge; the branch is left for cleanup to delete (M3). Recording
            # the intent is enough — closing a local branch has no remote analogue.
            return {"action": "close_without_merge", "executed": True,
                    "already": False, "branch": branch}

    return LocalGitDispositionExecutor()


def _install_offline_seams() -> None:
    from driver.runtime import admit as admit_mod
    from driver.runtime import candidate as candidate_mod
    from driver.runtime import dispose as dispose_mod
    from driver.runtime import eva as eva_mod
    from driver.runtime import review as review_mod

    # Clear the fail-closed tripwire envs so a stray shell value can't interfere
    # (we replace the builders wholesale, so these are never consulted anyway).
    for var in ("BUILDER_OS_DISPOSITION_STUB", "BUILDER_OS_PUBLISHER_STUB",
                "BUILDER_OS_REVIEWER_STUB", "BUILDER_OS_EVA_STUB"):
        os.environ.pop(var, None)

    # issue fetch → fixture (admit binds ``GhIssueSource()`` when no source is
    # injected; the CLI never injects one, so swap the name it resolves).
    admit_mod.GhIssueSource = _fixture_issue_source_factory

    # disposition → real local git merge
    dispose_mod.build_disposition_executor = _local_git_disposition_executor

    # PR publish → stub (the branch ref already exists locally)
    candidate_mod.build_publisher = lambda: candidate_mod.StubPrPublisher()

    # reviewer / Eva LLM runners → conforming stubs
    review_mod.build_reviewer_runner = lambda: review_mod.StubReviewerRunner()
    eva_mod.build_runner = lambda *a, **k: eva_mod.StubEvaRunner(verdict="conforming")


def main(argv: list[str] | None = None) -> int:
    _install_offline_seams()
    from driver.cli import main as cli_main

    return cli_main(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
