"""Shared fixtures for the deployment-provenance tests (Ghost task whlive).

Both ``test_deployments`` and ``test_doctor_cli`` build their states out of
**real** temporary git repositories and hand-written :pep:`610` ``dist-info``
records, so the interesting deployment states are constructed rather than
observed on whichever machine happens to run the suite.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from gits.core import deployments as dm


def run_git(args: list[str], cwd: Path) -> str:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_TERMINAL_PROMPT": "0",
    }
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env, check=False
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> tuple[Path, tuple[str, ...]]:
    """A bare origin with three commits on master. Returns (origin, shas)."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    run_git(["init", "--bare", "-b", "master", "."], origin)

    work = tmp_path / "_seed"
    work.mkdir()
    run_git(["clone", str(origin), "."], work)
    (work / "src" / "gits").mkdir(parents=True)
    shas: list[str] = []
    for i in range(3):
        (work / "src" / "gits" / "mod.py").write_text(f"VERSION = {i}\n")
        run_git(["add", "-A"], work)
        run_git(["commit", "-m", f"commit {i}"], work)
        shas.append(run_git(["rev-parse", "HEAD"], work))
    run_git(["push", "origin", "master"], work)
    return origin, tuple(shas)


def clone_at(origin: Path, dest: Path) -> Path:
    dest.mkdir(parents=True)
    run_git(["clone", str(origin), "."], dest)
    return dest


def make_env(
    root: Path, direct_url: dict | None, *, python_body: str | None = None
) -> Path:
    """A fake installed environment. Returns the ``bin/gits`` path.

    Includes a decoy second dist-info (this machine's real ``.venv`` carries an
    editable builder-os alongside ghost) so dist selection is actually tested.
    """
    (root / "bin").mkdir(parents=True, exist_ok=True)
    exe = root / "bin" / "gits"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)

    site = root / "lib" / "python3.12" / "site-packages"
    decoy = site / "builder_os-0.1.0.dist-info"
    decoy.mkdir(parents=True)
    (decoy / "direct_url.json").write_text(
        json.dumps({"url": "file:///nowhere/builder-os", "dir_info": {"editable": True}})
    )

    dist = site / "ghost_in_the_shell-0.2.69.dist-info"
    dist.mkdir(parents=True)
    if direct_url is not None:
        (dist / "direct_url.json").write_text(json.dumps(direct_url))

    if python_body is not None:
        py = root / "bin" / "python"
        py.write_text(python_body)
        py.chmod(0o755)
    return exe


def git_direct_url(commit: str, *, requested: str = "master") -> dict:
    return {
        "url": "https://github.com/AI4Life-Institute/ghost-in-the-shell",
        "vcs_info": {"vcs": "git", "commit_id": commit, "requested_revision": requested},
    }


def editable_direct_url(path: Path) -> dict:
    return {"url": f"file://{path}", "dir_info": {"editable": True}}


def refs_for(cli_exe: Path, hook_exe: Path, settings: str = "/fake/settings.json"):
    return [
        dm.DeploymentRef(role="cli", label="PATH:ghost", executable=cli_exe),
        dm.DeploymentRef(role="hook", label=f"{settings} (guard)", executable=hook_exe),
    ]


def codes(findings) -> set[str]:
    return {f.code for f in findings}


