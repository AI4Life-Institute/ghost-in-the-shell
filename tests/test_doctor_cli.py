"""Tests for the ``ghost doctor`` CLI surface (Ghost task whlive).

The logic lives in :mod:`gits.core.deployments` and is tested there against
constructed git states. What is asserted here is the *contract of the command*:
which shas and paths reach the output, what the exit code is, and that
"unresolved" is never printed as "clean".
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

import pytest

from gits import cli_doctor
from gits.core import deployments as dm

from .conftest import (  # the constructed-world helpers; `upstream` is a fixture
    clone_at,
    editable_direct_url,
    git_direct_url,
    make_env,
)
from .conftest import run_git as _git


def _args(**overrides) -> argparse.Namespace:
    base = dict(
        as_json=False,
        fetch=False,
        compare_ref=dm.DEFAULT_COMPARE_REF,
        no_config_probe=True,
        preinstall=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _run(monkeypatch, report: dm.Report, **overrides) -> tuple[int, str]:
    monkeypatch.setattr(dm, "collect_report", lambda **_kw: report)
    monkeypatch.setattr(cli_doctor.dep_mod, "collect_report", lambda **_kw: report)
    with pytest.raises(SystemExit) as exc:
        cli_doctor.dispatch(_args(**overrides))
    return int(exc.value.code or 0), exc


# ── the parser is actually wired into `ghost` ────────────────────────────


def test_doctor_is_reachable_from_the_real_cli():
    proc = subprocess.run(
        [sys.executable, "-m", "gits", "doctor", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--preinstall" in proc.stdout
    assert "--fetch" in proc.stdout


# ── report rendering ─────────────────────────────────────────────────────


def _world(tmp_path, upstream, *, stale: bool = False, dirty: bool = False):
    origin, shas = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    if dirty:
        _git(["checkout", "-b", "task/x"], checkout)
        (checkout / "src" / "gits" / "resolver.py").write_text("# other ticket\n")
    cli = make_env(tmp_path / "uvtool", git_direct_url(shas[0] if stale else shas[2]))
    hook = make_env(tmp_path / "venv", editable_direct_url(checkout))
    report = dm.build_report(
        [
            dm.DeploymentRef(role="cli", label="PATH:ghost", executable=cli),
            dm.DeploymentRef(role="hook", label="settings.json", executable=hook),
        ],
        compare_repo=checkout,
        probe_config=False,
    )
    return report, shas, checkout


def test_clean_report_prints_every_sha_and_exits_zero(tmp_path, upstream, monkeypatch, capsys):
    report, shas, checkout = _world(tmp_path, upstream)
    code, _ = _run(monkeypatch, report)
    out = capsys.readouterr().out

    assert code == 0
    assert shas[2] in out  # the CLI's exact commit_id
    assert str(checkout) in out  # the editable source path
    assert "up to date" in out
    assert "verdict: clean" in out


def test_stale_cli_is_named_with_its_sha_and_exits_one(tmp_path, upstream, monkeypatch, capsys):
    report, shas, _ = _world(tmp_path, upstream, stale=True)
    code, _ = _run(monkeypatch, report)
    out = capsys.readouterr().out

    assert code == 1
    assert shas[0] in out
    assert "behind-master" in out
    assert "2 commit(s) behind origin/master" in out
    assert "verdict: drift" in out
    assert "verdict: clean" not in out


def test_dirty_hook_checkout_is_named_with_its_path(tmp_path, upstream, monkeypatch, capsys):
    report, _, checkout = _world(tmp_path, upstream, dirty=True)
    code, _ = _run(monkeypatch, report)
    out = capsys.readouterr().out

    assert code == 1
    assert str(checkout) in out
    assert "task/x" in out
    assert "src/gits/resolver.py" in out
    assert "does NOT describe what runs" in out


def test_unresolved_is_printed_but_never_as_clean(monkeypatch, capsys):
    report = dm.Report(
        findings=[dm.Finding("unknown", "compare-ref-unresolved", "cannot resolve origin/master")]
    )
    code, _ = _run(monkeypatch, report)
    out = capsys.readouterr().out

    assert code == 0
    assert "unresolved (NOT the same as fine)" in out
    assert "verdict: unresolved" in out
    assert "verdict: clean" not in out


def test_json_output_carries_the_shas(tmp_path, upstream, monkeypatch, capsys):
    report, shas, checkout = _world(tmp_path, upstream, stale=True)
    code, _ = _run(monkeypatch, report, as_json=True)
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["compare_sha"] == shas[2]
    cli_dep = next(d for d in payload["deployments"] if d["roles"] == ["cli"])
    assert cli_dep["origin"]["commit_id"] == shas[0]
    assert cli_dep["distance"] == {"ahead": 0, "behind": 2, "error": None}
    hook_dep = next(d for d in payload["deployments"] if d["roles"] == ["hook"])
    assert hook_dep["worktree"]["path"] == str(checkout)
    assert hook_dep["worktree"]["head_sha"] == shas[2]


# ── --preinstall, both sides ─────────────────────────────────────────────


def test_preinstall_clean_tree_exits_zero(tmp_path, upstream, capsys):
    origin, shas = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    code = cli_doctor._run_preinstall(checkout)
    out = capsys.readouterr().out

    assert code == 0
    assert shas[2] in out
    assert "clean" in out
    assert "packs exactly this commit" in out


def test_preinstall_dirty_tree_warns_and_exits_one(tmp_path, upstream, capsys):
    origin, _ = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    (checkout / "src" / "gits" / "mod.py").write_text("edited\n")
    (checkout / "src" / "gits" / "resolver.py").write_text("# other ticket\n")

    code = cli_doctor._run_preinstall(checkout)
    out = capsys.readouterr().out

    assert code == 1
    assert "untracked-sources" in out
    assert "src/gits/resolver.py" in out
    assert "dirty-worktree" in out
    assert str(checkout) in out


def test_preinstall_states_its_own_limit(tmp_path, upstream, capsys):
    """It cannot prevent a dirty install and must not imply that it can."""
    origin, _ = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    cli_doctor._run_preinstall(checkout)
    out = capsys.readouterr().out
    assert "cannot stop a dirty install" in out


def test_preinstall_json(tmp_path, upstream, capsys):
    origin, shas = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    (checkout / "src" / "gits" / "resolver.py").write_text("# x\n")
    code = cli_doctor._run_preinstall(checkout, as_json=True)
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["path"] == str(checkout)
    assert payload["worktree"]["head_sha"] == shas[2]
    assert payload["worktree"]["untracked_sources"] == ["src/gits/resolver.py"]


def test_preinstall_flag_takes_the_path_and_short_circuits(tmp_path, upstream, monkeypatch):
    """``--preinstall PATH`` must not fall through to the full report."""
    origin, _ = upstream
    checkout = clone_at(origin, tmp_path / "checkout")

    def fail(**_kw):
        raise AssertionError("collect_report must not run for --preinstall")

    monkeypatch.setattr(cli_doctor.dep_mod, "collect_report", fail)
    with pytest.raises(SystemExit) as exc:
        cli_doctor.dispatch(_args(preinstall=str(checkout)))
    assert int(exc.value.code or 0) == 0


def test_empty_report_says_so(monkeypatch, capsys):
    code, _ = _run(monkeypatch, dm.Report())
    out = capsys.readouterr().out
    assert code == 0
    assert "no deployments found" in out
