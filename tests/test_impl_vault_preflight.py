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


# --- precision: red side must stay red (Ghost task gdprec, ghost#34) --------
#
# Every one of these is a *real* out-of-bounds change attempted from a PM
# session. The precision fix must not make a single one pass.

@pytest.mark.parametrize(
    "cmd, expected",
    [
        # cwd moved into the source repo, then a cwd-shaped git verb
        (f"cd {SRC} && git add -A && git commit -m wip", SRC),
        # explicit repo relocation
        (f"git -C {SRC} commit -am wip", SRC),
        (f"git --git-dir={SRC}/.git commit -am wip", f"{SRC}/.git"),
        # explicit path operand
        (f"git add {SRC}/algo/x.py", f"{SRC}/algo/x.py"),
        (f"git add -- {SRC}/algo/x.py", f"{SRC}/algo/x.py"),
        # relative operand that escapes the vault
        ("git add ../ai4stock/algo/x.py", "/Users/sharon/src/ai4stock/algo/x.py"),
        # shell redirection writes a source file with no git verb at all
        (f"echo 'print(1)' > {SRC}/algo/x.py", f"{SRC}/algo/x.py"),
        (f"cat tpl >> {SRC}/algo/x.py", f"{SRC}/algo/x.py"),
        # package mutation in the source repo
        (f"cd {SRC} && npm install", SRC),
        (f"uv add requests --directory {SRC}", SRC),
        # a repo cloned to a temp dir is still impl work: /tmp is NOT exempt,
        # only a per-session scratchpad is
        ("git -C /tmp/ai4stock-clone commit -am wip", "/tmp/ai4stock-clone"),
        # the mutating verb sits after a wrapper / env assignment
        (f"sudo git -C {SRC} clean -fd", SRC),
        (f"FOO=1 git -C {SRC} rebase -i main", SRC),
        # buried in the middle of a chain, after a harmless first segment
        (f"git status && cd {SRC} && git commit -am wip", SRC),
        # a backgrounding `&` still separates commands; only the `&` of a
        # redirection (2>&1, &>log) is part of its token
        (f"tail -f log & git -C {SRC} commit -am wip", SRC),
        (f"echo 'print(1)' &> {SRC}/algo/x.py", f"{SRC}/algo/x.py"),
        (f"pytest -q 2>/dev/null && git -C {SRC} commit -am wip", SRC),
        (f"echo x > {SRC}/algo/x.py 2>/dev/null", f"{SRC}/algo/x.py"),
    ],
)
def test_real_out_of_vault_mutations_still_refused(cmd, expected):
    allow, target = pf.evaluate(
        "Bash", {"command": cmd}, VAULT, vault_check=_vault_by_name, cwd=VAULT
    )
    assert allow is False, cmd
    assert target == expected


def test_source_repo_write_from_vault_session_still_refused():
    """The file-tool side of the red calibration."""
    allow, target = pf.evaluate(
        "Write", {"file_path": f"{SRC}/algo/x.py"}, VAULT, vault_check=_vault_by_name
    )
    assert allow is False
    assert target == f"{SRC}/algo/x.py"


# --- precision: the four recorded false positives must pass ------------------

def test_case1_path_inside_a_message_body_is_not_a_target():
    """ghost#34 case 1: a butler send whose *text* names a source repo, in the
    same command line as a commit of the vault's own files."""
    cmd = (
        'ghost butler send 1526003837012414506 '
        f'"共享 checkout 在 {SRC}，跑 pytest 前先 cd 过去" '
        '&& git add -A && git commit -m "task: dispatch"'
    )
    allow, target = pf.evaluate(
        "Bash", {"command": cmd}, VAULT, vault_check=_vault_by_name, cwd=VAULT
    )
    assert allow is True, target


def test_case2_url_fragment_in_written_text_is_not_a_path():
    """ghost#34 case 2: `/issues` — a URL fragment in documentation text
    written to a vault task page — was harvested as an absolute path."""
    cmd = (
        "python3 -c \"open('Projects/Ghost/tasks/t.md','a')"
        ".write('POST /issues via gh api; see repos/{owner}/{repo}/issues')\""
        " && git add -A && git commit -m 'task page'"
    )
    allow, target = pf.evaluate(
        "Bash", {"command": cmd}, VAULT, vault_check=_vault_by_name, cwd=VAULT
    )
    assert allow is True, target


def test_case3_session_memory_write_allowed(monkeypatch, tmp_path):
    """ghost#34 case 3: the PM session writing its own cross-session memory.
    The path extraction was right; the *conclusion* was wrong."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    memory = tmp_path / ".claude/projects/-Users-sharon-src-vault/memory/lesson.md"
    allow, target = pf.evaluate(
        "Write", {"file_path": str(memory)}, VAULT, vault_check=_vault_by_name
    )
    assert allow is True, target


def test_session_scratchpad_write_allowed(monkeypatch):
    monkeypatch.delenv("TMPDIR", raising=False)
    scratch = "/private/tmp/claude-501/-Users-sharon-src-vault/9df16154/scratchpad/notes.md"
    allow, target = pf.evaluate(
        "Write", {"file_path": scratch}, VAULT, vault_check=_vault_by_name
    )
    assert allow is True, target


def test_case4_task_page_naming_a_config_path_allowed():
    """ghost#34 case 4: the guard blocked the very ticket that describes it,
    because the page's text named a source-repo config file."""
    cmd = (
        'ghost butler send 1533015649587171381 '
        '"ghost 的用户级配置由 pydantic extra=\'forbid\' 校验："'
        f'"{SRC}/src/gits/config.py 里必须先声明字段" '
        '&& git add -A && git commit -m "task: gdprec"'
    )
    allow, target = pf.evaluate(
        "Bash", {"command": cmd}, VAULT, vault_check=_vault_by_name, cwd=VAULT
    )
    assert allow is True, target


# --- precision: supporting behaviours ---------------------------------------

def test_prose_verb_in_a_message_body_is_not_a_build():
    """`make` matched anywhere in the line used to count as a mutation."""
    allow, target = pf.evaluate(
        "Bash",
        {"command": f'ghost butler send 1 "make sure {SRC} is clean first"'},
        VAULT, vault_check=_vault_by_name, cwd=VAULT,
    )
    assert allow is True, target


def test_quoted_separator_does_not_split_a_message_body():
    allow, target = pf.evaluate(
        "Bash",
        {"command": f'ghost butler send 1 "step 1; then cd {SRC} && git commit"'},
        VAULT, vault_check=_vault_by_name, cwd=VAULT,
    )
    assert allow is True, target


def test_commit_message_is_not_a_pathspec():
    for cmd in (
        f'git commit -m "wire {SRC}/src/gits/config.py into the loader"',
        f'git commit -am "see {SRC}/README.md"',
        f'git commit --message="{SRC}/x.py"',
    ):
        allow, target = pf.evaluate(
            "Bash", {"command": cmd}, VAULT, vault_check=_vault_by_name, cwd=VAULT
        )
        assert allow is True, f"{cmd} -> {target}"


def test_cwd_shaped_verbs_target_cwd_not_a_mentioned_path():
    allow, target = pf.evaluate(
        "Bash",
        {"command": f"echo {SRC} && git add -A && git stash"},
        VAULT, vault_check=_vault_by_name, cwd=VAULT,
    )
    assert allow is True, target


def test_bash_cwd_defaults_to_project_dir_when_absent():
    allow, _ = pf.evaluate(
        "Bash", {"command": "git add -A && git commit -m x"}, VAULT,
        vault_check=_vault_by_name,
    )
    assert allow is True


def test_vault_subdirectory_cwd_is_still_inside():
    allow, target = pf.evaluate(
        "Bash", {"command": "git add -A && git commit -m x"}, VAULT,
        vault_check=_vault_by_name, cwd=f"{VAULT}/Projects/Ghost",
    )
    assert allow is True, target


def test_redirect_to_temp_scratch_allowed(monkeypatch):
    monkeypatch.delenv("TMPDIR", raising=False)
    allow, target = pf.evaluate(
        "Bash", {"command": "git diff --stat > /tmp/d.txt && git commit -am x"},
        VAULT, vault_check=_vault_by_name, cwd=VAULT,
    )
    assert allow is True, target


@pytest.mark.parametrize(
    "cmd",
    [
        "uv run pytest -q 2>/dev/null && git commit -am x",
        "git add -A > /dev/null 2>&1 && git commit -am 'task page'",
        "make -s 1>/dev/null",
        "ghost butler send 1 hi 2> /dev/null && git add -A && git commit -m x",
        "git status >/dev/tty",
    ],
)
def test_discarding_redirect_is_not_a_mutation(cmd):
    """Counting redirects as mutations must not swallow the everyday silencing
    idiom: /dev/null is not a file being written."""
    allow, target = pf.evaluate(
        "Bash", {"command": cmd}, VAULT, vault_check=_vault_by_name, cwd=VAULT
    )
    assert allow is True, f"{cmd} -> {target}"


def test_is_session_state_rejects_plain_temp_and_repos(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    assert pf.is_session_state("/tmp/scratch/x.py") is False
    assert pf.is_session_state(f"{SRC}/algo/x.py") is False
    assert pf.is_session_state(str(tmp_path / ".claude/settings.json")) is False


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
    # GHOST_GUARD_DRIFT_TTL=0 silences the whlive drift banner. These tests
    # assert on the *refusal* contract (exit code + a clean stderr on allow),
    # and the banner is a rate-limited notice about the checkout this test run
    # itself lives in — leaving it on would make them depend on the branch and
    # dirtiness of the developer's tree. The banner has its own tests, against
    # constructed checkouts, in tests/test_drift_banner.py.
    return subprocess.run(
        [sys.executable, "-m", "gits", "guard"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={
            "CLAUDE_PROJECT_DIR": env_project_dir,
            "PATH": __import__("os").environ["PATH"],
            "GHOST_GUARD_DRIFT_TTL": "0",
        },
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


def test_main_allows_message_body_path_plus_vault_commit(tmp_path):
    """Integration, ghost#34 case 1: the exact shape that used to be refused —
    a message naming a source repo, in the same line as a vault commit."""
    vault = tmp_path / "vault-test"
    (vault / "Projects").mkdir(parents=True)
    (vault / "MACHINES.md").write_text("x")
    cmd = (
        f'ghost butler send 1526 "共享 checkout 在 {SRC}" '
        "&& git add -A && git commit -m 'task page'"
    )
    r = _run_guard(
        {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(vault)},
        str(vault),
    )
    assert r.returncode == 0, r.stderr
    assert r.stderr.strip() == ""


def test_main_refuses_source_repo_commit_from_vault(tmp_path):
    """Integration, red side: same session, a genuine source-repo mutation."""
    vault = tmp_path / "vault-test"
    (vault / "Projects").mkdir(parents=True)
    (vault / "MACHINES.md").write_text("x")
    r = _run_guard(
        {
            "tool_name": "Bash",
            "tool_input": {"command": f"cd {SRC} && git add -A && git commit -m wip"},
            "cwd": str(vault),
        },
        str(vault),
    )
    assert r.returncode == 2
    assert SRC in r.stderr


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
