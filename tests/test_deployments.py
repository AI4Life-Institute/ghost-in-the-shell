"""Tests for deployment provenance — ``ghost doctor`` (Ghost task whlive).

Every state here is **constructed**: real temporary git repositories (a bare
origin plus clones) and hand-written :pep:`610` ``dist-info`` records. Nothing
reads this machine's actual deployments, because "run it once here and eyeball
the output" is exactly the reasoning this task exists to abolish.

Every sha and path that appears in a report is bound by an assertion against
the fixture that produced it — never merely "it did not raise".
"""

from __future__ import annotations

import json
from pathlib import Path

from gits.core import deployments as dm

from .conftest import (
    clone_at,
    codes,
    editable_direct_url,
    git_direct_url,
    make_env,
    refs_for,
)
from .conftest import run_git as _git

# ── PEP 610 reading ──────────────────────────────────────────────────────


def test_git_provenance_is_read_not_inferred(tmp_path, upstream):
    """The exact commit_id comes out of direct_url.json verbatim."""
    _, shas = upstream
    env = tmp_path / "uvtool"
    make_env(env, git_direct_url(shas[2]))
    dist = dm.find_dist_info(env)

    assert dist is not None
    assert dist.name == "ghost_in_the_shell-0.2.69.dist-info"  # not the builder-os decoy

    origin, error = dm.read_direct_url(dist)
    assert error is None
    assert isinstance(origin, dm.GitOrigin)
    assert origin.commit_id == shas[2]
    assert len(origin.commit_id) == 40
    assert origin.requested_revision == "master"


def test_editable_provenance_yields_the_source_path(tmp_path):
    checkout = tmp_path / "src" / "ghost-in-the-shell"
    checkout.mkdir(parents=True)
    env = tmp_path / "venv"
    make_env(env, editable_direct_url(checkout))

    origin, error = dm.read_direct_url(dm.find_dist_info(env))
    assert error is None
    assert isinstance(origin, dm.LocalOrigin)
    assert origin.editable is True
    assert origin.path == checkout


def test_missing_direct_url_is_unknown_not_silence(tmp_path):
    env = tmp_path / "venv"
    make_env(env, None)
    origin, error = dm.read_direct_url(dm.find_dist_info(env))
    assert origin is None
    assert error is not None and "direct_url.json" in error


def test_env_root_for_follows_symlinks(tmp_path):
    env = tmp_path / "uvtool"
    exe = make_env(env, git_direct_url("0" * 40))
    link = tmp_path / "bin" / "ghost"
    link.parent.mkdir()
    link.symlink_to(exe)
    assert dm.env_root_for(link) == env.resolve()


# ── the three constructed states ─────────────────────────────────────────


def test_state1_both_deployments_on_master_is_clean(tmp_path, upstream):
    """State 1: uv-tool CLI at master's tip, hook editable on a clean master."""
    origin, shas = upstream
    tip = shas[2]
    checkout = clone_at(origin, tmp_path / "checkout")

    cli = make_env(tmp_path / "uvtool", git_direct_url(tip))
    hook = make_env(tmp_path / "venv", editable_direct_url(checkout))

    report = dm.build_report(
        refs_for(cli, hook),
        compare_repo=checkout,
        config_env=tmp_path / "no-config.env",
        probe_config=False,
    )

    assert report.compare_sha == tip
    cli_dep, hook_dep = report.deployments

    assert cli_dep.roles == ["cli"]
    assert cli_dep.sha == tip
    assert cli_dep.sha_is_complete is True
    assert cli_dep.distance == dm.Distance(ahead=0, behind=0)

    assert hook_dep.roles == ["hook"]
    assert hook_dep.worktree is not None
    assert hook_dep.worktree.path == checkout
    assert hook_dep.worktree.head_sha == tip
    assert hook_dep.worktree.branch == "master"
    assert hook_dep.worktree.dirty is False
    assert hook_dep.sha == tip
    assert hook_dep.distance == dm.Distance(ahead=0, behind=0)

    assert report.all_findings() == []
    assert report.verdict == "clean"
    assert report.exit_code == 0


def test_state2_cli_behind_master_is_reported(tmp_path, upstream):
    """State 2: the CLI snapshot is two commits behind master — by exact sha."""
    origin, shas = upstream
    stale, tip = shas[0], shas[2]
    checkout = clone_at(origin, tmp_path / "checkout")

    cli = make_env(tmp_path / "uvtool", git_direct_url(stale))
    hook = make_env(tmp_path / "venv", editable_direct_url(checkout))

    report = dm.build_report(
        refs_for(cli, hook), compare_repo=checkout, probe_config=False
    )
    cli_dep, hook_dep = report.deployments

    assert cli_dep.sha == stale
    assert cli_dep.sha != tip
    assert cli_dep.distance == dm.Distance(ahead=0, behind=2)
    assert codes(cli_dep.findings) == {"behind-master"}
    (finding,) = cli_dep.findings
    assert "2 commit(s) behind origin/master" in finding.message
    assert str(cli) in finding.message

    # The hook deployment is untouched by the CLI's staleness.
    assert hook_dep.findings == []
    assert hook_dep.sha == tip

    assert report.verdict == "drift"
    assert report.exit_code == 1


def test_state3_hook_checkout_off_master_and_dirty(tmp_path, upstream):
    """State 3: the refusing hooks run an editable checkout on a dirty branch."""
    origin, shas = upstream
    tip = shas[2]
    checkout = clone_at(origin, tmp_path / "checkout")
    _git(["checkout", "-b", "task/utrref-relay-message-reference"], checkout)
    (checkout / "src" / "gits" / "mod.py").write_text("VERSION = 99\n")  # modified
    (checkout / "src" / "gits" / "resolver.py").write_text("# another ticket\n")  # untracked
    (checkout / "notes.txt").write_text("scratch\n")  # untracked, not source
    branch_head = _git(["rev-parse", "HEAD"], checkout)

    cli = make_env(tmp_path / "uvtool", git_direct_url(tip))
    hook = make_env(tmp_path / "venv", editable_direct_url(checkout))

    report = dm.build_report(
        refs_for(cli, hook, settings="/Users/sharon/.claude-x/settings.json"),
        compare_repo=checkout,
        probe_config=False,
    )
    cli_dep, hook_dep = report.deployments

    assert cli_dep.findings == []  # the CLI is fine; only the hooks drifted

    wt = hook_dep.worktree
    assert wt is not None
    assert wt.path == checkout
    assert wt.head_sha == branch_head == tip  # branched, not yet committed
    assert wt.branch == "task/utrref-relay-message-reference"
    assert wt.dirty is True
    assert wt.modified == ("src/gits/mod.py",)
    assert set(wt.untracked) == {"src/gits/resolver.py", "notes.txt"}
    assert wt.untracked_sources == ("src/gits/resolver.py",)

    assert codes(hook_dep.findings) == {
        "not-on-master",
        "dirty-worktree",
        "untracked-sources",
    }
    messages = {f.code: f.message for f in hook_dep.findings}
    assert "task/utrref-relay-message-reference" in messages["not-on-master"]
    assert str(checkout) in messages["not-on-master"]
    assert "src/gits/resolver.py" in messages["untracked-sources"]
    assert "notes.txt" not in messages["untracked-sources"]

    # The sha exists but does not describe what is running.
    assert hook_dep.sha == branch_head
    assert hook_dep.sha_is_complete is False
    assert hook_dep.labels == ["/Users/sharon/.claude-x/settings.json (guard)"]

    assert report.verdict == "drift"
    assert report.exit_code == 1


def test_deployment_ahead_of_master_is_a_note_not_drift(tmp_path, upstream):
    origin, shas = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    (checkout / "src" / "gits" / "mod.py").write_text("VERSION = 3\n")
    _git(["add", "-A"], checkout)
    _git(["commit", "-m", "local commit"], checkout)
    local_head = _git(["rev-parse", "HEAD"], checkout)

    hook = make_env(tmp_path / "venv", editable_direct_url(checkout))
    report = dm.build_report(
        [dm.DeploymentRef(role="hook", label="s", executable=hook)],
        compare_repo=checkout,
        probe_config=False,
    )
    (dep,) = report.deployments
    assert dep.worktree is not None and dep.worktree.head_sha == local_head
    assert dep.distance == dm.Distance(ahead=1, behind=0)
    assert codes(dep.findings) == {"ahead-of-master"}
    assert report.verdict == "clean"  # ahead-only is a warn, not drift
    assert report.exit_code == 0


def test_one_environment_serving_several_roles_is_reported_once(tmp_path, upstream):
    origin, shas = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    venv = tmp_path / "venv"
    exe = make_env(venv, editable_direct_url(checkout))
    refs = [
        dm.DeploymentRef(role="hook", label="settings-a", executable=exe),
        dm.DeploymentRef(role="hook", label="settings-b", executable=exe),
        dm.DeploymentRef(role="bot", label="pid 1233", executable=exe),
    ]
    report = dm.build_report(refs, compare_repo=checkout, probe_config=False)
    assert len(report.deployments) == 1
    (dep,) = report.deployments
    assert dep.roles == ["hook", "bot"]
    assert dep.labels == ["settings-a", "settings-b", "pid 1233"]


# ── distance edge cases ──────────────────────────────────────────────────


def test_unknown_commit_is_unresolved_never_clean(tmp_path, upstream):
    """A sha the comparison repo has never seen must not read as 'up to date'."""
    origin, _ = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    ghost_sha = "1" * 40
    cli = make_env(tmp_path / "uvtool", git_direct_url(ghost_sha))

    report = dm.build_report(
        [dm.DeploymentRef(role="cli", label="PATH:ghost", executable=cli)],
        compare_repo=checkout,
        probe_config=False,
    )
    (dep,) = report.deployments
    assert dep.distance is not None and dep.distance.error is not None
    assert ghost_sha in dep.distance.error
    assert codes(dep.findings) == {"distance-unknown"}
    assert report.verdict == "unresolved"
    assert report.exit_code == 0  # unresolved is not provable drift...
    assert report.verdict != "clean"  # ...but it is never reported as fine


def test_missing_compare_ref_is_reported(tmp_path, upstream):
    origin, shas = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    cli = make_env(tmp_path / "uvtool", git_direct_url(shas[2]))
    report = dm.build_report(
        [dm.DeploymentRef(role="cli", label="PATH:ghost", executable=cli)],
        compare_repo=checkout,
        compare_ref="origin/does-not-exist",
        probe_config=False,
    )
    assert report.compare_sha is None
    assert "compare-ref-unresolved" in codes(f for _, f in report.all_findings())
    assert report.verdict == "unresolved"


def test_last_fetch_time_is_none_before_any_fetch(tmp_path, upstream):
    origin, _ = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    assert dm.last_fetch_time(checkout) is None
    _git(["fetch", "origin"], checkout)
    stamp = dm.last_fetch_time(checkout)
    assert stamp is not None and stamp.startswith("20")


# ── uv receipts ──────────────────────────────────────────────────────────


def test_uv_receipt_local_dir_requirement_is_drift(tmp_path, upstream):
    """The ② shape: a receipt pointing at a working tree, not at git."""
    origin, shas = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    tools = tmp_path / "uvtools"
    env = tools / "ghost-in-the-shell"
    make_env(env, git_direct_url(shas[2]))
    entry = tmp_path / "localbin" / "ghost"
    entry.parent.mkdir()
    entry.symlink_to(env / "bin" / "gits")
    (env / "uv-receipt.toml").write_text(
        "[tool]\n"
        'requirements = [{ name = "ghost-in-the-shell", '
        'directory = "/Users/sharon/src/ghost-in-the-shell" }]\n'
        "entrypoints = [\n"
        f'    {{ name = "ghost", install-path = "{entry}", from = "ghost-in-the-shell" }},\n'
        "]\n"
    )

    report = dm.build_report(
        [dm.DeploymentRef(role="cli", label="PATH:ghost", executable=entry)],
        compare_repo=checkout,
        uv_tools_dir=tools,
        probe_config=False,
    )
    (dep,) = report.deployments
    assert dep.receipt_requirement == "dir:/Users/sharon/src/ghost-in-the-shell"
    assert "local-requirement" in codes(dep.findings)
    (finding,) = [f for f in dep.findings if f.code == "local-requirement"]
    assert "/Users/sharon/src/ghost-in-the-shell" in finding.message


def test_uv_receipt_git_requirement_is_not_drift(tmp_path, upstream):
    origin, shas = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    tools = tmp_path / "uvtools"
    env = tools / "ghost-in-the-shell"
    make_env(env, git_direct_url(shas[2]))
    entry = tmp_path / "localbin" / "ghost"
    entry.parent.mkdir()
    entry.symlink_to(env / "bin" / "gits")
    (env / "uv-receipt.toml").write_text(
        "[tool]\n"
        'requirements = [{ name = "ghost-in-the-shell", '
        'git = "https://github.com/AI4Life-Institute/ghost-in-the-shell?rev=master" }]\n'
        "entrypoints = [\n"
        f'    {{ name = "ghost", install-path = "{entry}", from = "ghost-in-the-shell" }},\n'
        "]\n"
    )
    report = dm.build_report(
        [dm.DeploymentRef(role="cli", label="PATH:ghost", executable=entry)],
        compare_repo=checkout,
        uv_tools_dir=tools,
        probe_config=False,
    )
    (dep,) = report.deployments
    assert dep.receipt_requirement.startswith("git+https://github.com/")
    assert dep.findings == []


# ── config.env / Settings compatibility ──────────────────────────────────


def test_config_env_keys_parsing(tmp_path):
    env = tmp_path / "config.env"
    env.write_text(
        "# a comment\n"
        "\n"
        "GITS_DISCORD_TOKEN=abc\n"
        "export GHOST_CORE_OS_MANDATE=liang\n"
        "  ALLOWED_PATHS=/a,/b  \n"
        "not-an-assignment\n"
        "GITS_DISCORD_TOKEN=dup\n"
    )
    assert dm.config_env_keys(env) == (
        "gits_discord_token",
        "ghost_core_os_mandate",
        "allowed_paths",
    )


def _probe_env(tmp_path: Path, name: str, body: str) -> Path:
    root = tmp_path / name
    make_env(root, git_direct_url("0" * 40), python_body=body)
    return root / "bin" / "python"


def test_config_compat_ok(tmp_path):
    py = _probe_env(
        tmp_path,
        "ok",
        '#!/bin/sh\necho \'["gits_dir", "ghost_core_os_mandate"]\'\n',
    )
    compat = dm.check_config_compat(py, ["ghost_core_os_mandate", "gits_dir"])
    assert compat.status == "ok"
    assert compat.missing == ()
    assert "ghost_core_os_mandate" in compat.declared


def test_config_compat_missing_key_is_drift(tmp_path, upstream):
    """The ① shape: a key on disk that a stale deployment does not declare."""
    origin, shas = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    env = tmp_path / "stale"
    exe = make_env(
        env,
        git_direct_url(shas[2]),
        python_body='#!/bin/sh\necho \'["gits_dir", "allowed_paths"]\'\n',
    )
    config_env = tmp_path / "config.env"
    config_env.write_text("GHOST_CORE_OS_MANDATE=liang\nGITS_DIR=~/.gits\n")

    report = dm.build_report(
        [dm.DeploymentRef(role="cli", label="PATH:ghost", executable=exe)],
        compare_repo=checkout,
        config_env=config_env,
    )
    (dep,) = report.deployments
    assert dep.config is not None
    assert dep.config.status == "missing"
    assert dep.config.missing == ("ghost_core_os_mandate",)
    assert "config-key-unknown" in codes(dep.findings)
    (finding,) = [f for f in dep.findings if f.code == "config-key-unknown"]
    assert "ghost_core_os_mandate" in finding.message
    assert "extra='forbid'" in finding.message
    assert report.exit_code == 1


def test_config_probe_crash_is_unknown_with_a_reason(tmp_path):
    py = _probe_env(tmp_path, "boom", "#!/bin/sh\necho 'ImportError: nope' >&2\nexit 1\n")
    compat = dm.check_config_compat(py, ["gits_dir"])
    assert compat.status == "unknown"
    assert compat.status != "ok"
    assert compat.reason is not None and "exited 1" in compat.reason
    assert "ImportError: nope" in compat.reason


def test_config_probe_hang_is_bounded_by_the_timeout(tmp_path):
    """A diagnostic that can hang is worse than none — the probe must not."""
    py = _probe_env(tmp_path, "hang", "#!/bin/sh\nsleep 30\n")
    compat = dm.check_config_compat(py, ["gits_dir"], timeout=0.5)
    assert compat.status == "unknown"
    assert compat.reason is not None and "timed out" in compat.reason


def test_config_probe_missing_interpreter_is_unknown(tmp_path):
    compat = dm.check_config_compat(tmp_path / "nope" / "python", ["gits_dir"])
    assert compat.status == "unknown"
    assert "not found" in (compat.reason or "")


def test_config_probe_garbage_output_is_unknown(tmp_path):
    py = _probe_env(tmp_path, "garbage", "#!/bin/sh\necho 'not json'\n")
    compat = dm.check_config_compat(py, ["gits_dir"])
    assert compat.status == "unknown"
    assert "not JSON" in (compat.reason or "")


def test_unknown_config_probe_never_reads_as_clean(tmp_path, upstream):
    origin, shas = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    env = tmp_path / "broken"
    exe = make_env(env, git_direct_url(shas[2]), python_body="#!/bin/sh\nexit 3\n")
    config_env = tmp_path / "config.env"
    config_env.write_text("GITS_DIR=~/.gits\n")

    report = dm.build_report(
        [dm.DeploymentRef(role="cli", label="PATH:ghost", executable=exe)],
        compare_repo=checkout,
        config_env=config_env,
    )
    (dep,) = report.deployments
    assert "config-compat-unknown" in codes(dep.findings)
    assert report.verdict == "unresolved"
    assert report.exit_code == 0


def test_no_config_keys_means_no_probe(tmp_path, upstream):
    """Nothing on disk to conflict with ⇒ don't execute other deployments' code."""
    origin, shas = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    env = tmp_path / "e"
    exe = make_env(env, git_direct_url(shas[2]), python_body="#!/bin/sh\nexit 3\n")
    report = dm.build_report(
        [dm.DeploymentRef(role="cli", label="PATH:ghost", executable=exe)],
        compare_repo=checkout,
        config_env=tmp_path / "absent.env",
    )
    (dep,) = report.deployments
    assert dep.config is None
    assert dep.findings == []


# ── discovery ────────────────────────────────────────────────────────────


def test_hook_refs_come_from_settings_files(tmp_path):
    settings = tmp_path / ".claude-x" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Edit",
                            "hooks": [
                                {"type": "command", "command": "/opt/venv/bin/gits guard"},
                                {"type": "command", "command": "echo unrelated"},
                            ],
                        }
                    ],
                    "SessionStart": [
                        {"hooks": [{"command": "/opt/venv/bin/ghost hook"}]}
                    ],
                }
            }
        )
    )
    refs = dm.discover_hook_refs([settings, tmp_path / "absent.json"])
    assert [str(r.executable) for r in refs] == ["/opt/venv/bin/gits", "/opt/venv/bin/ghost"]
    assert all(r.role == "hook" for r in refs)
    assert str(settings) in refs[0].label
    assert "guard" in refs[0].label


def test_default_settings_files_covers_every_account_dir(tmp_path):
    for name in (".claude", ".claude-sharon", ".claude-sharongoogle"):
        d = tmp_path / name
        d.mkdir()
        (d / "settings.json").write_text("{}")
    (tmp_path / ".claude" / "settings.local.json").write_text("{}")
    found = {p.relative_to(tmp_path).as_posix() for p in dm.default_settings_files(tmp_path)}
    assert found == {
        ".claude/settings.json",
        ".claude-sharon/settings.json",
        ".claude-sharongoogle/settings.json",
        ".claude/settings.local.json",
    }


def test_discover_refs_uses_injected_lookups(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"hooks": [{"command": "/venv/bin/gits guard"}]}]}}))
    refs = dm.discover_refs(
        home=tmp_path,
        path_lookup=lambda name: "/usr/local/bin/ghost" if name == "ghost" else None,
        settings_files=[settings],
        bot_lookup=lambda: (Path("/venv/bin/gits"), "pid 1233"),
    )
    assert [(r.role, str(r.executable)) for r in refs] == [
        ("cli", "/usr/local/bin/ghost"),
        ("hook", "/venv/bin/gits"),
        ("bot", "/venv/bin/gits"),
    ]


def test_unresolvable_executable_is_unknown(tmp_path, upstream):
    origin, _ = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    report = dm.build_report(
        [dm.DeploymentRef(role="cli", label="PATH:ghost", executable=Path("/nope/ghost"))],
        compare_repo=checkout,
        probe_config=False,
    )
    (dep,) = report.deployments
    assert "env-unresolved" in codes(dep.findings)
    assert report.verdict == "unresolved"


def test_editable_path_that_is_not_a_checkout_is_unknown(tmp_path, upstream):
    origin, _ = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    loose = tmp_path / "loose-copy"
    loose.mkdir()
    env = tmp_path / "venv"
    exe = make_env(env, editable_direct_url(loose))
    report = dm.build_report(
        [dm.DeploymentRef(role="hook", label="s", executable=exe)],
        compare_repo=checkout,
        probe_config=False,
    )
    (dep,) = report.deployments
    assert "not-a-checkout" in codes(dep.findings)
    assert str(loose) in [f.message for f in dep.findings][0]


# ── preinstall (both sides) ──────────────────────────────────────────────


def test_preinstall_clean_tree_warns_about_nothing(tmp_path, upstream):
    origin, shas = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    check = dm.check_preinstall(checkout)
    assert check.worktree is not None
    assert check.worktree.head_sha == shas[2]
    assert check.worktree.branch == "master"
    assert check.findings == []
    assert check.ok is True
    assert check.exit_code == 0


def test_preinstall_dirty_tree_warns(tmp_path, upstream):
    origin, _ = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    (checkout / "src" / "gits" / "mod.py").write_text("edited\n")
    (checkout / "src" / "gits" / "resolver.py").write_text("# other ticket\n")

    check = dm.check_preinstall(checkout)
    assert check.ok is False
    assert check.exit_code == 1
    assert codes(check.findings) >= {"dirty-worktree", "untracked-sources"}
    untracked = next(f for f in check.findings if f.code == "untracked-sources")
    assert "src/gits/resolver.py" in untracked.message
    assert str(checkout) in untracked.message


def test_preinstall_untracked_non_source_is_not_an_error(tmp_path, upstream):
    origin, _ = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    (checkout / "scratch.md").write_text("notes\n")
    check = dm.check_preinstall(checkout)
    assert codes(check.findings) == set()
    assert check.ok is True


def test_preinstall_branch_is_a_warning_not_a_failure(tmp_path, upstream):
    origin, _ = upstream
    checkout = clone_at(origin, tmp_path / "checkout")
    _git(["checkout", "-b", "task/x"], checkout)
    check = dm.check_preinstall(checkout)
    assert codes(check.findings) == {"not-on-master"}
    assert check.ok is True  # installing a clean feature branch is a choice, not a bug
    assert check.exit_code == 0


def test_preinstall_on_a_non_checkout_is_unknown(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    check = dm.check_preinstall(d)
    assert check.worktree is None
    assert codes(check.findings) == {"not-a-checkout"}
    assert check.exit_code == 0
