"""Unit tests for ``ghost butler dispatch`` — vault-aware task dispatcher.

Coverage (per task u78tma Acceptance #5):
  - task-ref resolution: id-match, fuzzy-match, no-match, multi-match
  - frontmatter validation: missing personas, wrong status, missing .butler.json
  - atomic writeback (mock fs failure)
  - rollback (mock thread-create success + send failure → expect thread archived)
  - lint pass/fail

Every Discord REST call is mocked via ``api()``. No network.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from gits.butler import butler_cli, dispatch_task


@pytest.fixture(autouse=True)
def _no_real_http(monkeypatch):
    """Belt-and-suspenders: any unmocked api() call MUST fail loudly rather
    than hitting Discord. Each test that exercises REST must patch api()
    on BOTH ``dispatch_task`` and ``butler_cli`` modules (they each did
    ``from .http import api``, so each holds its own binding)."""
    def boom(*a, **kw):
        raise RuntimeError(
            f"unmocked api() call leaked through: {a!r} {kw!r}"
        )
    monkeypatch.setattr(dispatch_task, "api", boom)
    monkeypatch.setattr(butler_cli, "api", boom)


def _install_recorder(monkeypatch, rest):
    """Bind the same recorder onto both modules' ``api`` references."""
    monkeypatch.setattr(dispatch_task, "api", rest)
    monkeypatch.setattr(butler_cli, "api", rest)


# ---------------------------------------------------------------------------
# Vault fixture: a temp dir that *looks* like a vault — has Projects/ tree,
# a project README with local_path, and a .butler.json at the toplevel.
# ---------------------------------------------------------------------------


def _make_task_file(
    tmp_path,
    *,
    fname: str,
    tid: str,
    project: str = "TestProj",
    status: str = "draft",
    personas: str = "[senior engineer]",
    extra: str = "",
) -> str:
    """Create a task .md with the standard frontmatter; return absolute path."""
    proj_tasks = tmp_path / "Projects" / project / "tasks" / "area" / "2026-05"
    proj_tasks.mkdir(parents=True, exist_ok=True)
    path = proj_tasks / fname
    body = f"""---
id: {tid}
project: {project}
status: {status}
personas: {personas}
{extra}---

# Body

stuff
"""
    path.write_text(body)
    return str(path)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A vault-shaped tmp dir. monkeypatches ``identity.run_git`` and
    ``identity.load_binding`` so we don't need an actual git repo / file."""
    (tmp_path / "Projects" / "TestProj").mkdir(parents=True)
    (tmp_path / "Projects" / "TestProj" / "README.md").write_text(
        "local_path: /tmp/test-proj-work-dir\n"
    )

    # Pretend git toplevel resolves to tmp_path.
    # identity.run_git is the shared shim — also called by send_decorated →
    # resolve_user → detect_worktree_user, so the lambda has to handle the
    # branch/show-toplevel calls too. Return a /work-suffixed branch so the
    # worktree-user resolver yields a name (otherwise send_decorated halts).
    def fake_git(*args, cwd=None):
        if "--show-toplevel" in args:
            return str(tmp_path)
        if "--abbrev-ref" in args:
            return "test/work"
        return None

    monkeypatch.setattr(dispatch_task.identity, "run_git", fake_git)
    # Pretend .butler.json says channel/guild
    monkeypatch.setattr(
        dispatch_task.identity, "load_binding",
        lambda cwd=None: {"channel_id": "CHAN123", "guild_id": "GUILD999"},
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Task-ref resolution (Acceptance #5)
# ---------------------------------------------------------------------------


def test_resolve_task_file_by_id_match(vault):
    p = _make_task_file(vault, fname="2026-05-16 abc123 the title.md", tid="abc123")
    assert dispatch_task.resolve_task_file("abc123", str(vault)) == p


def test_resolve_task_file_by_fuzzy_filename_match(vault):
    p = _make_task_file(vault, fname="2026-05-16 xyz789 do the thing.md", tid="xyz789")
    assert dispatch_task.resolve_task_file("do the thing", str(vault)) == p


def test_resolve_task_file_no_match_halts(vault):
    _make_task_file(vault, fname="2026-05-16 abc123 t.md", tid="abc123")
    with pytest.raises(SystemExit) as exc:
        dispatch_task.resolve_task_file("nomatch", str(vault))
    assert "no task file matches" in str(exc.value)


def test_resolve_task_file_multi_match_halts(vault):
    _make_task_file(vault, fname="2026-05-16 aaa111 alpha.md", tid="aaa111")
    _make_task_file(vault, fname="2026-05-16 bbb222 alpha redux.md", tid="bbb222")
    with pytest.raises(SystemExit) as exc:
        dispatch_task.resolve_task_file("alpha", str(vault))
    assert "2 files match" in str(exc.value)


def test_resolve_task_file_id_match_does_not_collide_on_substring(vault):
    """A 6-char id needle requires separator-boundaries (space or dash), so
    a longer filename id containing the substring must NOT match."""
    _make_task_file(vault, fname="2026-05-16 abc123x other.md", tid="abc123x")
    with pytest.raises(SystemExit) as exc:
        dispatch_task.resolve_task_file("abc123", str(vault))
    assert "no task file matches" in str(exc.value)


# -- dash-form filename support (task [[elgcbn]]) ---------------------------


def test_resolve_task_file_by_id_match_dash_form(vault):
    """New dash-separated filename: ``2026-05-17-abc123-foo-bar.md``."""
    p = _make_task_file(
        vault, fname="2026-05-17-abc123-foo-bar.md", tid="abc123"
    )
    assert dispatch_task.resolve_task_file("abc123", str(vault)) == p


def test_resolve_task_file_id_dash_form_does_not_collide_on_substring(vault):
    """Same substring-collision guard must apply on dash-form filenames."""
    _make_task_file(
        vault, fname="2026-05-17-abc123x-other.md", tid="abc123x"
    )
    with pytest.raises(SystemExit) as exc:
        dispatch_task.resolve_task_file("abc123", str(vault))
    assert "no task file matches" in str(exc.value)


def test_resolve_task_file_finds_tid_across_both_forms(vault):
    """Same query resolves either form when only one exists."""
    p_old = _make_task_file(
        vault, fname="2026-05-16 aaa111 alpha.md", tid="aaa111"
    )
    p_new = _make_task_file(
        vault, fname="2026-05-17-bbb222-beta.md", tid="bbb222"
    )
    assert dispatch_task.resolve_task_file("aaa111", str(vault)) == p_old
    assert dispatch_task.resolve_task_file("bbb222", str(vault)) == p_new


def test_resolve_task_file_fuzzy_match_dash_form(vault):
    """Fuzzy (non-id) query treats `-` and ` ` interchangeably — so
    ``dispatch "foo bar"`` finds ``2026-05-17-xyz789-foo-bar.md``."""
    p = _make_task_file(
        vault, fname="2026-05-17-xyz789-foo-bar.md", tid="xyz789"
    )
    assert dispatch_task.resolve_task_file("foo bar", str(vault)) == p


def test_resolve_task_file_fuzzy_match_space_query_against_dash_form(vault):
    """Inverse: dash-form query against old-form filename also works."""
    p = _make_task_file(
        vault, fname="2026-05-16 xyz789 do the thing.md", tid="xyz789"
    )
    assert dispatch_task.resolve_task_file("do-the-thing", str(vault)) == p


# -- thread_title (task [[elgcbn]]) -----------------------------------------


def test_thread_title_old_format():
    """Legacy space-separated filename — behavior unchanged."""
    assert (
        dispatch_task.thread_title("/x/2026-05-17 abc123 Foo Bar Baz.md")
        == "Foo Bar Baz"
    )


def test_thread_title_old_format_preserves_dashes_in_title():
    """Old-form titles can contain literal dashes; they must be preserved
    byte-for-byte (regression: this very task's old-form filename was
    ``2026-05-17 elgcbn butler accepts dash-separated task filenames.md``)."""
    assert (
        dispatch_task.thread_title(
            "/x/2026-05-17 elgcbn butler accepts dash-separated task filenames.md"
        )
        == "butler accepts dash-separated task filenames"
    )


def test_thread_title_new_format_lowercase_dash_to_space():
    """New dash-separated filename — dashes become spaces, no case change."""
    assert (
        dispatch_task.thread_title("/x/2026-05-17-abc123-foo-bar-baz.md")
        == "foo bar baz"
    )


def test_thread_title_new_format_real_filename():
    """This very task's new-form filename (operator-confirmed: lowercase)."""
    assert (
        dispatch_task.thread_title(
            "/v/Projects/Ghost/tasks/discord-bot/2026-05/"
            "2026-05-17-elgcbn-butler-accepts-dash-separated-task-filenames.md"
        )
        == "butler accepts dash separated task filenames"
    )


def test_thread_title_unparseable_falls_back_to_basename():
    """Anything that matches neither form returns the basename sans .md —
    don't lose the filename, even if it's malformed."""
    assert dispatch_task.thread_title("/x/weird-name.md") == "weird-name"
    assert dispatch_task.thread_title("/x/no-extension") == "no-extension"


def test_thread_title_new_format_single_word_slug():
    """New-form with a single-word title still works."""
    assert (
        dispatch_task.thread_title("/x/2026-05-17-abc123-foo.md") == "foo"
    )


# ---------------------------------------------------------------------------
# build_pointer_message — done-notify instruction (task dn0tfy)
# ---------------------------------------------------------------------------


def test_pointer_embeds_done_notice_with_channel_and_task_id():
    """The brief instructs the executor to report back via `ghost butler send`
    into the dispatcher's home channel, with the channel id embedded and a
    `DONE <task-id>` completion shape — so a finished executor pokes the PM
    instead of leaving them to poll (task dn0tfy)."""
    fm = {"id": "dn0tfy", "personas": "[senior engineer in python]"}
    msg = dispatch_task.build_pointer_message(
        fm, "/v/2026-06-01-dn0tfy-executor-done-notify.md", "plan",
        channel_id="1511262347811880981",
    )
    # (a) the literal verb
    assert "ghost butler send" in msg
    # (b) the passed-in channel id, positionally (no --channel flag exists)
    assert "1511262347811880981" in msg
    assert "--channel" not in msg
    # (c) the DONE <task-id> completion-notice shape
    assert 'DONE dn0tfy <artifact-url-or-one-line-summary>' in msg


def test_pointer_done_notice_comes_after_phase_block():
    """Chronological ordering: the phase instruction precedes the
    report-back-when-finished line."""
    fm = {"id": "dn0tfy", "personas": "[senior engineer in python]"}
    msg = dispatch_task.build_pointer_message(
        fm, "/v/2026-06-01-dn0tfy-executor-done-notify.md", "plan",
        channel_id="999",
    )
    assert msg.index("Phase: **plan first**") < msg.index("ghost butler send")


def test_pointer_preserves_plan_phase_instruction():
    """Regression: the done-notice is an addition, not a replacement — the
    existing plan-first phase instruction must still be present."""
    fm = {"id": "dn0tfy", "personas": "[senior engineer in python]"}
    msg = dispatch_task.build_pointer_message(
        fm, "/v/2026-06-01-dn0tfy-executor-done-notify.md", "plan",
        channel_id="999",
    )
    assert "Phase: **plan first**" in msg
    assert "Do not implement yet." in msg


def test_pointer_done_notice_present_in_impl_phase_too():
    """The report-back instruction is phase-independent (impl phase keeps it,
    after the impl greenlight line)."""
    fm = {"id": "dn0tfy", "personas": "[senior engineer in python]"}
    msg = dispatch_task.build_pointer_message(
        fm, "/v/2026-06-01-dn0tfy-executor-done-notify.md", "impl",
        channel_id="777",
    )
    assert "Phase: **impl**" in msg
    assert "ghost butler send 777" in msg
    assert msg.index("Phase: **impl**") < msg.index("ghost butler send")


# ---------------------------------------------------------------------------
# Frontmatter validation (Acceptance #5)
# ---------------------------------------------------------------------------


def test_preflight_rejects_non_draft_status():
    with patch.object(dispatch_task, "api", return_value=(200, {"id": "bot"})):
        with pytest.raises(SystemExit) as exc:
            dispatch_task.preflight({"status": "dispatched", "personas": "[a]"})
    assert "status is" in str(exc.value) and "refusing" in str(exc.value)


def test_preflight_rejects_empty_personas():
    with patch.object(dispatch_task, "api", return_value=(200, {"id": "bot"})):
        with pytest.raises(SystemExit) as exc:
            dispatch_task.preflight({"status": "draft", "personas": ""})
    assert "personas" in str(exc.value)


def test_preflight_rejects_literal_empty_list_personas():
    with patch.object(dispatch_task, "api", return_value=(200, {"id": "bot"})):
        with pytest.raises(SystemExit) as exc:
            dispatch_task.preflight({"status": "draft", "personas": "[]"})
    assert "personas" in str(exc.value)


def test_preflight_passes_with_draft_status_and_personas():
    with patch.object(dispatch_task, "api", return_value=(200, {"id": "bot"})):
        dispatch_task.preflight({"status": "draft", "personas": "[a, b]"})


def test_read_home_channel_halts_when_butler_json_missing(monkeypatch):
    """If .butler.json is missing/empty, ``read_home_channel`` halts pointing
    at ``ghost butler bind``."""
    monkeypatch.setattr(
        dispatch_task.identity, "load_binding", lambda cwd=None: {}
    )
    with pytest.raises(SystemExit) as exc:
        dispatch_task.read_home_channel()
    assert "no home channel bound" in str(exc.value)


def test_read_home_channel_halts_when_guild_id_missing(monkeypatch):
    monkeypatch.setattr(
        dispatch_task.identity, "load_binding",
        lambda cwd=None: {"channel_id": "X"},  # no guild_id
    )
    with pytest.raises(SystemExit):
        dispatch_task.read_home_channel()


# ---------------------------------------------------------------------------
# Atomic writeback (Acceptance #5)
# ---------------------------------------------------------------------------


def test_writeback_updates_existing_fields_and_inserts_missing(tmp_path):
    p = tmp_path / "task.md"
    p.write_text("---\nid: t1\nstatus: draft\n---\n\nbody\n")
    dispatch_task.writeback_frontmatter_atomic(
        str(p), {"status": "dispatched", "dispatched": "2026-05-16"},
    )
    text = p.read_text()
    assert "status: dispatched\n" in text
    assert "dispatched: 2026-05-16\n" in text
    assert "id: t1\n" in text  # existing untouched fields preserved
    assert text.endswith("\nbody\n")  # body preserved


def test_writeback_body_preserved_byte_for_byte(tmp_path):
    p = tmp_path / "task.md"
    body = "---\nx: y\n---\n\n# h\n\n- bullet\n```\ncode\n```\n"
    p.write_text(body)
    dispatch_task.writeback_frontmatter_atomic(str(p), {"x": "z"})
    text = p.read_text()
    assert text.endswith("---\n\n# h\n\n- bullet\n```\ncode\n```\n")


def test_writeback_atomic_failure_leaves_original_untouched(tmp_path, monkeypatch):
    """If os.rename fails, the original file must be unchanged."""
    p = tmp_path / "task.md"
    orig = "---\nstatus: draft\n---\n\nbody\n"
    p.write_text(orig)

    def boom(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(dispatch_task.os, "rename", boom)
    with pytest.raises(OSError):
        dispatch_task.writeback_frontmatter_atomic(
            str(p), {"status": "dispatched"}
        )
    assert p.read_text() == orig


def test_writeback_no_frontmatter_raises(tmp_path):
    p = tmp_path / "task.md"
    p.write_text("no frontmatter here\n")
    with pytest.raises(ValueError, match="no YAML frontmatter"):
        dispatch_task.writeback_frontmatter_atomic(str(p), {"x": "y"})


# ---------------------------------------------------------------------------
# Rollback (Acceptance #5)
# ---------------------------------------------------------------------------


class _RestRecorder:
    """Records (path, method, body) tuples for inspection."""

    def __init__(self, responses):
        self.calls = []
        self._responses = list(responses)

    def __call__(self, path, method="GET", body=None, query=None):
        self.calls.append((path, method, body, query))
        if self._responses:
            return self._responses.pop(0)
        return (200, {})

    def archived_thread_ids(self):
        return [
            c[0].split("/")[-1] for c in self.calls
            if c[1] == "PATCH" and isinstance(c[2], dict) and c[2].get("archived")
        ]


def _full_task(vault) -> str:
    """A task with all required fields, ready to dispatch."""
    return _make_task_file(
        vault,
        fname="2026-05-16 abc123 the title.md",
        tid="abc123",
    )


def test_rollback_archives_thread_when_pointer_send_fails(vault, monkeypatch):
    """Thread-create succeeds; /bind succeeds; pointer send returns no msg id.
    Expect: thread archived; dispatch exits non-zero; frontmatter unchanged."""
    task_path = _full_task(vault)
    orig = open(task_path).read()

    rest = _RestRecorder([
        (200, {"id": "bot"}),                    # preflight /users/@me
        (201, {"id": "123456789012345678", "name": "x"}),   # POST /channels/CHAN/threads
        (201, {"id": "MID-BIND"}),               # POST /channels/123456789012345678/messages (/bind)
        (500, "boom"),                           # POST /channels/123456789012345678/messages (pointer) — FAIL
        (200, {"id": "123456789012345678"}),                # PATCH archive
    ])
    _install_recorder(monkeypatch, rest)

    with pytest.raises(SystemExit) as exc:
        dispatch_task.dispatch_task("abc123", "plan", cwd=str(vault))
    assert "pointer" in str(exc.value).lower()
    # The thread we just created must have been archived
    assert "123456789012345678" in rest.archived_thread_ids()
    # Frontmatter untouched
    assert open(task_path).read() == orig


def test_rollback_archives_thread_when_bind_send_fails(vault, monkeypatch):
    """Thread-create succeeds; /bind send fails. Expect: archived."""
    task_path = _full_task(vault)
    orig = open(task_path).read()

    rest = _RestRecorder([
        (200, {"id": "bot"}),
        (201, {"id": "123456789012345678", "name": "x"}),
        (500, "bind boom"),
        (200, {"id": "123456789012345678"}),                # archive
    ])
    _install_recorder(monkeypatch, rest)

    with pytest.raises(SystemExit):
        dispatch_task.dispatch_task("abc123", "plan", cwd=str(vault))
    assert "123456789012345678" in rest.archived_thread_ids()
    assert open(task_path).read() == orig


def test_rollback_archives_thread_when_writeback_fails(vault, monkeypatch):
    """Thread + /bind + pointer all succeed; writeback raises.
    Expect: thread archived; exit non-zero."""
    task_path = _full_task(vault)
    orig = open(task_path).read()

    rest = _RestRecorder([
        (200, {"id": "bot"}),
        (201, {"id": "123456789012345678", "name": "x"}),
        (201, {"id": "MID-BIND"}),
        (201, {"id": "MID-POINTER"}),
        (200, {"id": "123456789012345678"}),                # archive
    ])
    _install_recorder(monkeypatch, rest)

    def boom(*a, **kw):
        raise OSError("writeback simulated failure")

    monkeypatch.setattr(dispatch_task, "writeback_frontmatter_atomic", boom)
    # Speed up the sleep between bind + pointer
    monkeypatch.setattr(dispatch_task.time, "sleep", lambda *_: None)

    with pytest.raises(SystemExit) as exc:
        dispatch_task.dispatch_task("abc123", "plan", cwd=str(vault))
    assert "writeback" in str(exc.value)
    assert "123456789012345678" in rest.archived_thread_ids()
    assert open(task_path).read() == orig


def test_happy_path_writes_all_four_frontmatter_fields(vault, monkeypatch):
    task_path = _full_task(vault)
    rest = _RestRecorder([
        (200, {"id": "bot"}),
        (201, {"id": "123456789012345678", "name": "x"}),
        (201, {"id": "MID-BIND"}),
        (201, {"id": "MID-POINTER"}),
        # lint: read-back returns 2 messages (passes)
        (200, [{"id": "MID-BIND"}, {"id": "MID-POINTER"}]),
    ])
    _install_recorder(monkeypatch, rest)
    monkeypatch.setattr(dispatch_task.time, "sleep", lambda *_: None)

    dispatch_task.dispatch_task("abc123", "plan", cwd=str(vault))
    fm = dispatch_task.parse_frontmatter(task_path)
    assert fm["thread"].startswith("[the title](https://discord.com/channels/GUILD999/123456789012345678)")
    assert fm["dispatch_msg_id"] == "MID-POINTER"
    assert fm["status"] == "dispatched (plan-phase)"
    assert fm["dispatched"]  # today's date — don't pin
    # owner stamped from worktree identity (vault fixture sets branch
    # "test/work", so identity.resolve_user yields "test").
    assert fm["owner"] == "test"


def test_dispatch_writes_owner_from_explicit_branch(vault, monkeypatch):
    """Same flow but spoof the branch as a multi-segment butler name
    (e.g. `weiliu-algo-data/work`) — the dash-suffixed user must round-trip
    unmangled into the task page's owner field."""
    task_path = _full_task(vault)

    def fake_git(*args, cwd=None):
        if "--show-toplevel" in args:
            return str(vault)
        if "--abbrev-ref" in args:
            return "weiliu-algo-data/work"
        return None
    monkeypatch.setattr(dispatch_task.identity, "run_git", fake_git)

    rest = _RestRecorder([
        (200, {"id": "bot"}),
        (201, {"id": "123456789012345678", "name": "x"}),
        (201, {"id": "MID-BIND"}),
        (201, {"id": "MID-POINTER"}),
        (200, [{"id": "MID-BIND"}, {"id": "MID-POINTER"}]),
    ])
    _install_recorder(monkeypatch, rest)
    monkeypatch.setattr(dispatch_task.time, "sleep", lambda *_: None)

    dispatch_task.dispatch_task("abc123", "plan", cwd=str(vault))
    fm = dispatch_task.parse_frontmatter(task_path)
    assert fm["owner"] == "weiliu-algo-data"


def test_dispatch_refuses_when_owner_unresolvable(vault, monkeypatch):
    """If resolve_user returns (None, None) — e.g. no /work branch, no
    vault-<name> dir, no BUTLER_USER — refuse cleanly BEFORE creating
    the thread (no side effects)."""
    _full_task(vault)

    def fake_git(*args, cwd=None):
        if "--show-toplevel" in args:
            return str(vault)
        # branch with no /work suffix → resolver yields None
        if "--abbrev-ref" in args:
            return "main"
        return None
    monkeypatch.setattr(dispatch_task.identity, "run_git", fake_git)
    # toplevel basename also lacks vault- prefix (it's a pytest tmp dir).
    # Make sure BUTLER_USER env doesn't leak in from the test runner.
    monkeypatch.delenv("BUTLER_USER", raising=False)

    rest = _RestRecorder([
        (200, {"id": "bot"}),  # only preflight should fire
    ])
    _install_recorder(monkeypatch, rest)

    with pytest.raises(SystemExit) as exc:
        dispatch_task.dispatch_task("abc123", "plan", cwd=str(vault))
    assert "owner identity" in str(exc.value)
    # No thread create call should have happened.
    assert not any(
        c[1] == "POST" and "/threads" in c[0] for c in rest.calls
    )


def test_dispatch_owner_rollback_when_writeback_fails(vault, monkeypatch):
    """Acceptance #3: writeback-side rollback also reverts `owner:`.
    Because writeback is atomic (.tmp + rename), the all-or-nothing
    contract gives this for free — verify the original file (which has
    no `owner:` line) is byte-for-byte preserved."""
    task_path = _full_task(vault)
    orig = open(task_path).read()
    assert "owner:" not in orig  # baseline

    rest = _RestRecorder([
        (200, {"id": "bot"}),
        (201, {"id": "123456789012345678", "name": "x"}),
        (201, {"id": "MID-BIND"}),
        (201, {"id": "MID-POINTER"}),
        (200, {"id": "123456789012345678"}),  # archive
    ])
    _install_recorder(monkeypatch, rest)
    monkeypatch.setattr(dispatch_task.time, "sleep", lambda *_: None)

    def boom(*a, **kw):
        raise OSError("simulated writeback failure")
    monkeypatch.setattr(dispatch_task, "writeback_frontmatter_atomic", boom)

    with pytest.raises(SystemExit):
        dispatch_task.dispatch_task("abc123", "plan", cwd=str(vault))
    # File on disk is exactly as before — no `owner:` line snuck in.
    after = open(task_path).read()
    assert after == orig
    assert "owner:" not in after


def test_impl_phase_writes_dispatched_status(vault, monkeypatch):
    task_path = _full_task(vault)
    rest = _RestRecorder([
        (200, {"id": "bot"}),
        (201, {"id": "123456789012345678", "name": "x"}),
        (201, {"id": "MID-BIND"}),
        (201, {"id": "MID-POINTER"}),
        (200, [{"id": "MID-BIND"}, {"id": "MID-POINTER"}]),
    ])
    _install_recorder(monkeypatch, rest)
    monkeypatch.setattr(dispatch_task.time, "sleep", lambda *_: None)

    dispatch_task.dispatch_task("abc123", "impl", cwd=str(vault))
    fm = dispatch_task.parse_frontmatter(task_path)
    assert fm["status"] == "dispatched"  # no "(plan-phase)" suffix


# ---------------------------------------------------------------------------
# Lint pass/fail (Acceptance #5)
# ---------------------------------------------------------------------------


def test_lint_passes_when_all_fields_present_and_thread_readable(tmp_path, monkeypatch):
    p = tmp_path / "task.md"
    p.write_text(
        "---\n"
        "status: dispatched (plan-phase)\n"
        'thread: "[t](https://discord.com/channels/G/123456789012345678)"\n'
        "dispatched: 2026-05-16\n"
        "dispatch_msg_id: MID-POINTER\n"
        "owner: weiliu-algo-data\n"
        "---\n\nbody\n"
    )
    rest = _RestRecorder([(200, [{"id": "a"}, {"id": "b"}])])
    _install_recorder(monkeypatch, rest)
    monkeypatch.setattr(dispatch_task.time, "sleep", lambda *_: None)

    failures = dispatch_task.lint(
        str(p), "123456789012345678", "dispatched (plan-phase)",
    )
    assert failures == []


def test_lint_flags_missing_frontmatter_fields(tmp_path, monkeypatch):
    p = tmp_path / "task.md"
    p.write_text(
        "---\nstatus: dispatched (plan-phase)\n---\n\nbody\n"
    )
    monkeypatch.setattr(
        dispatch_task, "api", lambda *a, **kw: (200, [{"id": "a"}, {"id": "b"}]),
    )
    monkeypatch.setattr(dispatch_task.time, "sleep", lambda *_: None)
    failures = dispatch_task.lint(str(p), "TID", "dispatched (plan-phase)")
    msgs = " | ".join(failures)
    assert "thread" in msgs and "dispatched" in msgs and "dispatch_msg_id" in msgs
    assert "owner" in msgs  # new — dispatch-time lint hard-fails on missing owner


def test_lint_flags_only_missing_owner(tmp_path, monkeypatch):
    """Everything else present, only owner missing — must still fail."""
    p = tmp_path / "task.md"
    p.write_text(
        "---\n"
        "status: dispatched\n"
        'thread: "[t](https://discord.com/channels/G/123456789012345678)"\n'
        "dispatched: 2026-05-18\n"
        "dispatch_msg_id: MID\n"
        "---\n\nbody\n"
    )
    monkeypatch.setattr(
        dispatch_task, "api", lambda *a, **kw: (200, [{"id": "a"}, {"id": "b"}]),
    )
    monkeypatch.setattr(dispatch_task.time, "sleep", lambda *_: None)
    failures = dispatch_task.lint(str(p), "123456789012345678", "dispatched")
    assert any("owner" in f for f in failures)
    assert not any("thread" in f and "owner" not in f for f in failures)


def test_lint_passes_when_owner_is_present(tmp_path, monkeypatch):
    p = tmp_path / "task.md"
    p.write_text(
        "---\n"
        "status: dispatched\n"
        'thread: "[t](https://discord.com/channels/G/123456789012345678)"\n'
        "dispatched: 2026-05-18\n"
        "dispatch_msg_id: MID\n"
        "owner: weiliu-algo-data\n"
        "---\n\nbody\n"
    )
    monkeypatch.setattr(
        dispatch_task, "api", lambda *a, **kw: (200, [{"id": "a"}, {"id": "b"}]),
    )
    monkeypatch.setattr(dispatch_task.time, "sleep", lambda *_: None)
    failures = dispatch_task.lint(str(p), "123456789012345678", "dispatched")
    assert failures == []


def test_lint_flags_wrong_status(tmp_path, monkeypatch):
    p = tmp_path / "task.md"
    p.write_text(
        "---\n"
        "status: draft\n"
        'thread: "[t](https://discord.com/channels/G/111111111111111111)"\n'
        "dispatched: 2026-05-16\n"
        "dispatch_msg_id: MID\n"
        "---\n\nbody\n"
    )
    monkeypatch.setattr(
        dispatch_task, "api", lambda *a, **kw: (200, [{"id": "a"}, {"id": "b"}]),
    )
    monkeypatch.setattr(dispatch_task.time, "sleep", lambda *_: None)
    failures = dispatch_task.lint(str(p), "111111111111111111", "dispatched")
    assert any("status" in f for f in failures)


def test_lint_flags_thread_message_undercount(tmp_path, monkeypatch):
    """If read-back returns <2 messages even after retry, lint fails."""
    p = tmp_path / "task.md"
    p.write_text(
        "---\n"
        "status: dispatched\n"
        'thread: "[t](https://discord.com/channels/G/123456789012345678)"\n'
        "dispatched: 2026-05-16\n"
        "dispatch_msg_id: MID\n"
        "---\n\nbody\n"
    )
    # Always returns 0 messages
    monkeypatch.setattr(dispatch_task, "api", lambda *a, **kw: (200, []))
    monkeypatch.setattr(dispatch_task.time, "sleep", lambda *_: None)
    # Fast-forward time so the deadline expires immediately
    times = iter([0.0, 100.0])
    monkeypatch.setattr(dispatch_task.time, "time", lambda: next(times))

    failures = dispatch_task.lint(str(p), "123456789012345678", "dispatched")
    assert any("read-back" in f for f in failures)


# ---------------------------------------------------------------------------
# Pointer message assembly — preserves the vault-original format
# ---------------------------------------------------------------------------


def test_pointer_message_includes_personas_and_phase_plan():
    msg = dispatch_task.build_pointer_message(
        {"id": "abc123", "personas": "[architect, reviewer]"},
        "/tmp/2026-05-16 abc123 do the thing.md",
        "plan",
        channel_id="123",
    )
    assert "**a architect**" in msg and "**a reviewer**" in msg
    assert "[[abc123]]" in msg
    assert "do the thing" in msg
    assert "plan first" in msg.lower()


def test_pointer_message_impl_phase_uses_impl_tail():
    msg = dispatch_task.build_pointer_message(
        {"id": "abc123", "personas": "[a]"},
        "/tmp/2026-05-16 abc123 t.md",
        "impl",
        channel_id="123",
    )
    assert "Plan approved" in msg
    assert "plan first" not in msg.lower()
