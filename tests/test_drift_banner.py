"""Tests for the guard's drift banner (Ghost task whlive).

The banner exists because ``gits guard`` — which *refuses operations* — runs
from an editable checkout, so its behaviour is decided by whichever branch that
checkout is parked on. The banner says so out loud.

Its constraints are the interesting part and each has a test here: stderr only,
never the refusal path, never the exit code, and rate limited (an unthrottled
notice on every tool call is noise, which is the same as silence).

Every state is constructed from a real temporary git repository.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gits.hooks import drift_banner as db
from gits.hooks import impl_vault_preflight as pf


def _git(args: list[str], cwd: Path) -> str:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env, check=False
    )
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return proc.stdout.strip()


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A source checkout shaped like an editable install of this package."""
    repo = tmp_path / "ghost-in-the-shell"
    hooks = repo / "src" / "gits" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "drift_banner.py").write_text("# stand-in for the real module\n")
    _git(["init", "-b", "master", "."], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "initial"], repo)
    return repo


def module_file(checkout: Path) -> Path:
    return checkout / "src" / "gits" / "hooks" / "drift_banner.py"


def emit(checkout: Path, stamp: Path, *, now: float = 1000.0, ttl: float = 100.0):
    stream = io.StringIO()
    banner = db.emit(
        module_file=module_file(checkout),
        stamp_path=stamp,
        ttl=ttl,
        now=now,
        stream=stream,
    )
    return banner, stream.getvalue()


# ── what it says, and when ───────────────────────────────────────────────


def test_clean_master_says_nothing(checkout, tmp_path):
    banner, out = emit(checkout, tmp_path / "stamp")
    assert banner is None
    assert out == ""


def test_non_master_branch_is_announced(checkout, tmp_path):
    _git(["checkout", "-b", "task/utrref-relay-message-reference"], checkout)
    banner, out = emit(checkout, tmp_path / "stamp")
    assert banner is not None
    assert "task/utrref-relay-message-reference" in banner
    assert str(checkout) in banner
    assert "not master" in banner.lower() or "NOT master" in banner
    assert "ghost doctor" in banner
    assert out.strip() == banner


def test_dirty_master_is_announced(checkout, tmp_path):
    (checkout / "src" / "gits" / "hooks" / "drift_banner.py").write_text("# edited\n")
    banner, _ = emit(checkout, tmp_path / "stamp")
    assert banner is not None
    assert "uncommitted changes" in banner
    assert "branch" not in banner  # on master; only the dirtiness is news


def test_untracked_only_still_counts_as_dirty(checkout, tmp_path):
    (checkout / "src" / "gits" / "resolver.py").write_text("# another ticket\n")
    banner, _ = emit(checkout, tmp_path / "stamp")
    assert banner is not None and "uncommitted changes" in banner


def test_detached_head_is_announced(checkout, tmp_path):
    head = _git(["rev-parse", "HEAD"], checkout)
    _git(["checkout", "--detach", head], checkout)
    banner, _ = emit(checkout, tmp_path / "stamp")
    assert banner is not None and "detached HEAD" in banner


def test_branch_and_dirt_are_both_reported(checkout, tmp_path):
    _git(["checkout", "-b", "task/x"], checkout)
    (checkout / "src" / "gits" / "hooks" / "drift_banner.py").write_text("# edited\n")
    banner, _ = emit(checkout, tmp_path / "stamp")
    assert banner is not None
    assert "task/x" in banner and "uncommitted changes" in banner


def test_main_branch_named_main_is_also_clean(checkout, tmp_path):
    _git(["checkout", "-b", "main"], checkout)
    banner, _ = emit(checkout, tmp_path / "stamp")
    assert banner is None


# ── rate limiting ────────────────────────────────────────────────────────


def test_banner_is_rate_limited_within_the_ttl(checkout, tmp_path):
    _git(["checkout", "-b", "task/x"], checkout)
    stamp = tmp_path / "stamp"

    first, _ = emit(checkout, stamp, now=1000.0, ttl=100.0)
    assert first is not None

    for later in (1000.5, 1050.0, 1099.9):
        again, out = emit(checkout, stamp, now=later, ttl=100.0)
        assert again is None, f"banner repeated at t={later}"
        assert out == ""

    after_ttl, _ = emit(checkout, stamp, now=1101.0, ttl=100.0)
    assert after_ttl is not None


def test_rate_limit_gate_precedes_any_git_work(checkout, tmp_path, monkeypatch):
    """Inside the TTL the banner must not shell out — it runs on every tool call."""
    _git(["checkout", "-b", "task/x"], checkout)
    stamp = tmp_path / "stamp"
    emit(checkout, stamp, now=1000.0, ttl=100.0)

    def explode(*_args, **_kwargs):
        raise AssertionError("git must not run inside the rate-limit window")

    monkeypatch.setattr(db.subprocess, "run", explode)
    assert emit(checkout, stamp, now=1010.0, ttl=100.0) == (None, "")


def test_a_stamp_from_the_future_is_stale_not_a_permanent_mute(checkout, tmp_path):
    """A clock change must not be able to silence the banner forever."""
    _git(["checkout", "-b", "task/x"], checkout)
    stamp = tmp_path / "stamp"
    stamp.write_text("99999999999")
    banner, _ = emit(checkout, stamp, now=1000.0, ttl=100.0)
    assert banner is not None


def test_a_corrupt_stamp_does_not_wedge_the_banner(checkout, tmp_path):
    _git(["checkout", "-b", "task/x"], checkout)
    stamp = tmp_path / "stamp"
    stamp.write_text("not a number")
    banner, _ = emit(checkout, stamp, now=1000.0, ttl=100.0)
    assert banner is not None
    assert stamp.read_text() == "1000.0"


def test_ttl_zero_disables_the_banner(checkout, tmp_path):
    _git(["checkout", "-b", "task/x"], checkout)
    banner, out = emit(checkout, tmp_path / "stamp", ttl=0)
    assert banner is None and out == ""


def test_ttl_comes_from_the_environment(checkout, tmp_path, monkeypatch):
    monkeypatch.setenv(db.TTL_ENV, "0")
    assert db._resolve_ttl(None) == 0
    monkeypatch.setenv(db.TTL_ENV, "not-a-number")
    assert db._resolve_ttl(None) == db.DEFAULT_TTL
    monkeypatch.delenv(db.TTL_ENV)
    assert db._resolve_ttl(None) == db.DEFAULT_TTL


def test_stamp_is_written_even_when_there_is_nothing_to_say(checkout, tmp_path):
    """Otherwise a clean checkout pays for a git status on every single call."""
    stamp = tmp_path / "nested" / "stamp"
    emit(checkout, stamp)
    assert stamp.exists()


# ── stream discipline ────────────────────────────────────────────────────


def test_banner_goes_to_stderr_never_stdout(checkout, tmp_path, capsys):
    _git(["checkout", "-b", "task/x"], checkout)
    banner = db.emit(
        module_file=module_file(checkout),
        stamp_path=tmp_path / "stamp",
        ttl=100.0,
        now=1000.0,
    )
    captured = capsys.readouterr()
    assert banner is not None
    assert captured.out == ""  # PreToolUse stdout is protocol — never ours
    assert banner in captured.err


# ── checkout detection ───────────────────────────────────────────────────


def test_non_editable_install_has_no_checkout(tmp_path):
    """Code copied into site-packages has no branch to drift; stay silent."""
    site = tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "gits" / "hooks"
    site.mkdir(parents=True)
    module = site / "drift_banner.py"
    module.write_text("#\n")
    assert db.find_checkout(module) is None


def test_a_git_repo_that_is_not_this_package_is_not_our_checkout(tmp_path):
    repo = tmp_path / "unrelated"
    (repo / "vendor" / "gits" / "hooks").mkdir(parents=True)
    _git(["init", "-b", "master", "."], repo)
    module = repo / "vendor" / "gits" / "hooks" / "drift_banner.py"
    module.write_text("#\n")
    assert db.find_checkout(module) is None


def test_find_checkout_locates_the_editable_source(checkout):
    assert db.find_checkout(module_file(checkout)) == checkout


def test_read_branch_handles_a_linked_worktree(checkout, tmp_path):
    """A linked worktree's ``.git`` is a file — the common case on this machine."""
    linked = tmp_path / "wt-whlive"
    _git(["worktree", "add", "-b", "task/whlive", str(linked)], checkout)
    assert (linked / ".git").is_file()
    assert db.read_branch(linked) == "task/whlive"


def test_read_branch_returns_none_for_detached_head(checkout):
    head = _git(["rev-parse", "HEAD"], checkout)
    _git(["checkout", "--detach", head], checkout)
    assert db.read_branch(checkout) is None


def test_is_dirty_returns_none_outside_a_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert db.is_dirty(plain) is None


def test_default_stamp_path_honours_gits_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GITS_DIR", str(tmp_path / "state"))
    assert db.default_stamp_path() == tmp_path / "state" / "guard_drift_stamp"


# ── the guard contract: never the verdict, never the exit code ───────────


def _run_guard_main(monkeypatch, payload: dict, project_dir: str, *, emit_impl):
    calls: list[dict] = []

    def fake_emit(**kwargs):
        calls.append(kwargs)
        return emit_impl()

    monkeypatch.setattr(db, "emit", fake_emit)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", project_dir)
    with pytest.raises(SystemExit) as exc:
        pf.main()
    return int(exc.value.code or 0), calls


def _vault(tmp_path: Path) -> str:
    vault = tmp_path / "vault-test"
    (vault / "Projects").mkdir(parents=True)
    (vault / "MACHINES.md").write_text("x")
    return str(vault)


def _source_repo(tmp_path: Path) -> str:
    repo = tmp_path / "ai4stock"
    repo.mkdir()
    return str(repo)


def test_banner_runs_on_the_allow_path(monkeypatch, tmp_path, capsys):
    code, calls = _run_guard_main(
        monkeypatch,
        {"tool_name": "Edit", "tool_input": {"file_path": "/Users/sharon/src/ai4stock/x.py"}},
        _source_repo(tmp_path),
        emit_impl=lambda: "banner",
    )
    assert code == 0
    assert len(calls) == 1
    assert capsys.readouterr().out == ""


def test_banner_never_runs_on_the_refusal_path(monkeypatch, tmp_path, capsys):
    code, calls = _run_guard_main(
        monkeypatch,
        {"tool_name": "Edit", "tool_input": {"file_path": "/Users/sharon/src/ai4stock/x.py"}},
        _vault(tmp_path),
        emit_impl=lambda: "banner",
    )
    assert code == 2
    assert calls == []
    err = capsys.readouterr().err
    assert "Re-dispatch" in err  # the refusal message, unpolluted


def test_a_banner_failure_cannot_change_the_verdict(monkeypatch, tmp_path):
    """A cosmetic notice must never be able to turn an allow into anything else."""

    def boom():
        raise RuntimeError("stamp file on a read-only volume")

    code, calls = _run_guard_main(
        monkeypatch,
        {"tool_name": "Edit", "tool_input": {"file_path": "/Users/sharon/src/ai4stock/x.py"}},
        _source_repo(tmp_path),
        emit_impl=boom,
    )
    assert code == 0
    assert len(calls) == 1


def test_banner_failure_also_cannot_change_a_refusal(monkeypatch, tmp_path):
    def boom():
        raise RuntimeError("boom")

    code, _ = _run_guard_main(
        monkeypatch,
        {"tool_name": "Edit", "tool_input": {"file_path": "/Users/sharon/src/ai4stock/x.py"}},
        _vault(tmp_path),
        emit_impl=boom,
    )
    assert code == 2
