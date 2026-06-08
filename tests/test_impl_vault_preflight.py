"""Tests for the impl-preflight PreToolUse guard (Ghost task j5pn2w).

Covers the four acceptance cases:
  1. source-repo executor (project root != vault)  -> allow
  2. impl edit inside a vault session               -> refuse
  3. vault-internal / read-only work                -> allow (no false block)
  4. installer wires the PreToolUse guard idempotently
"""

import json
import subprocess
import sys

import pytest

from gits.hooks import impl_vault_preflight as pf


# A vault dir per its basename pattern; a source repo otherwise. Avoids the
# filesystem so the core logic is tested in isolation.
def _vault_by_name(path: str) -> bool:
    return path.rstrip("/").rsplit("/", 1)[-1].startswith("vault")


VAULT = "/Users/sharon/src/vault-harry-er-ai-analyst"
SRC = "/Users/sharon/src/ai4stock"


# --- evaluate(): the decision core -----------------------------------------

def test_source_repo_executor_is_never_blocked():
    """Case 1 + carve-out precision: project root is a source repo, so the
    guard is a no-op even for a clear source-repo mutation."""
    allow, _ = pf.evaluate(
        "Edit", {"file_path": f"{SRC}/algo/x.py"}, SRC, vault_check=_vault_by_name
    )
    assert allow is True
    allow, _ = pf.evaluate(
        "Bash", {"command": "git commit -am wip && git push"}, SRC,
        vault_check=_vault_by_name,
    )
    assert allow is True


def test_vault_session_impl_edit_is_refused():
    """Case 2: editing a source-repo file from a vault session is refused."""
    allow, target = pf.evaluate(
        "Edit", {"file_path": f"{SRC}/algo/x.py"}, VAULT, vault_check=_vault_by_name
    )
    assert allow is False
    assert target == f"{SRC}/algo/x.py"


def test_vault_session_external_git_commit_is_refused():
    allow, target = pf.evaluate(
        "Bash",
        {"command": f"cd {SRC} && git commit -am wip"},
        VAULT,
        vault_check=_vault_by_name,
    )
    assert allow is False
    assert target == SRC


def test_vault_session_git_dash_C_external_refused():
    allow, target = pf.evaluate(
        "Bash",
        {"command": f"git -C {SRC} worktree add ../ai4stock-v09"},
        VAULT,
        vault_check=_vault_by_name,
    )
    assert allow is False
    assert target == SRC


def test_vault_internal_edit_allowed():
    """Case 3: editing a task page inside the vault is legitimate PM work."""
    allow, _ = pf.evaluate(
        "Edit",
        {"file_path": f"{VAULT}/Projects/Ghost/tasks/foo.md"},
        VAULT,
        vault_check=_vault_by_name,
    )
    assert allow is True


def test_vault_internal_git_commit_allowed():
    """A plain commit in the vault (no external path) is PM bookkeeping."""
    allow, _ = pf.evaluate(
        "Bash", {"command": "git commit -am 'task update'"}, VAULT,
        vault_check=_vault_by_name,
    )
    assert allow is True


def test_vault_readonly_bash_allowed():
    for cmd in ("git status", f"git -C {SRC} log --oneline", f"cat {SRC}/README.md", f"ls {SRC}"):
        allow, _ = pf.evaluate("Bash", {"command": cmd}, VAULT, vault_check=_vault_by_name)
        assert allow is True, cmd


def test_vault_session_external_install_refused():
    allow, target = pf.evaluate(
        "Bash", {"command": f"uv add requests --directory {SRC}"}, VAULT,
        vault_check=_vault_by_name,
    )
    assert allow is False
    assert target == SRC


def test_unknown_tool_allowed():
    allow, _ = pf.evaluate("Read", {"file_path": f"{SRC}/x.py"}, VAULT, vault_check=_vault_by_name)
    assert allow is True


def test_missing_project_dir_fails_open():
    allow, _ = pf.evaluate("Edit", {"file_path": f"{SRC}/x.py"}, None, vault_check=_vault_by_name)
    assert allow is True


# --- is_vault_root(): real filesystem signals -------------------------------

def test_is_vault_root_by_name(tmp_path):
    d = tmp_path / "vault-someone"
    d.mkdir()
    assert pf.is_vault_root(str(d)) is True


def test_is_vault_root_by_structure(tmp_path):
    d = tmp_path / "renamed-checkout"
    (d / "Projects").mkdir(parents=True)
    (d / "MACHINES.md").write_text("# machines\n")
    assert pf.is_vault_root(str(d)) is True


def test_is_vault_root_source_repo_is_false(tmp_path):
    d = tmp_path / "ai4stock"
    d.mkdir()
    assert pf.is_vault_root(str(d)) is False


# --- main(): end-to-end stdin/exit-code via the installed `gits guard` ------

def _run_guard(payload: dict, env_project_dir: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "gits", "guard"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"CLAUDE_PROJECT_DIR": env_project_dir, "PATH": __import__("os").environ["PATH"]},
    )


def test_main_refuses_impl_edit_in_vault(tmp_path):
    """Integration: vault project root + external edit -> exit 2 + message."""
    vault = tmp_path / "vault-test"
    (vault / "Projects").mkdir(parents=True)
    (vault / "MACHINES.md").write_text("x")
    r = _run_guard(
        {"tool_name": "Edit", "tool_input": {"file_path": f"{SRC}/x.py"}},
        str(vault),
    )
    assert r.returncode == 2
    assert "Re-dispatch" in r.stderr
    assert "ghost butler dispatch" in r.stderr


def test_main_allows_source_executor(tmp_path):
    src = tmp_path / "ai4stock"
    src.mkdir()
    r = _run_guard(
        {"tool_name": "Edit", "tool_input": {"file_path": str(src / "x.py")}},
        str(src),
    )
    assert r.returncode == 0
    assert r.stderr.strip() == ""


# --- installer idempotency --------------------------------------------------

def test_installer_adds_guard_and_is_idempotent(tmp_path):
    from gits import __main__ as m

    cfg = tmp_path / "claudecfg"
    rc = m._install_hook(config_dir=str(cfg), quiet=True)
    assert rc == 0
    settings = json.loads((cfg / "settings.json").read_text())
    assert m._is_hook_installed(settings)
    assert m._is_guard_installed(settings)
    pre = settings["hooks"]["PreToolUse"]
    assert any(
        h.get("command", "").endswith("gits guard")
        for e in pre for h in e.get("hooks", [])
    )

    # Second install must not duplicate.
    rc2 = m._install_hook(config_dir=str(cfg), quiet=True)
    assert rc2 == 0
    settings2 = json.loads((cfg / "settings.json").read_text())
    guard_count = sum(
        1
        for e in settings2["hooks"]["PreToolUse"]
        for h in e.get("hooks", [])
        if h.get("command", "").endswith("gits guard")
    )
    assert guard_count == 1
