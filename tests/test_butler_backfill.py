"""Unit tests for ``ghost butler backfill-owners``.

Coverage (per task t6k4j1 Acceptance #5/#6):
  - walks Projects/*/{tasks,archive}/**/*.md only
  - skips tasks already with owner set
  - skips tasks still in draft
  - extracts owner from first [butler:USER] message in the thread
  - dry-run does NOT write; --apply does
  - thread with no decorated message → reported as unresolved, not aborted
  - thread fetch failure → reported as unresolved, not aborted

Every Discord REST call is mocked via ``api()``. No network.
"""

from __future__ import annotations

import pytest

from gits.butler import backfill, dispatch_task


@pytest.fixture(autouse=True)
def _no_real_http(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError(f"unmocked api(): {a!r} {kw!r}")
    monkeypatch.setattr(backfill, "api", boom)


def _task(tmp_path, *, fname, body):
    """Write a task .md inside Projects/P/tasks/area/2026-05/."""
    d = tmp_path / "Projects" / "P" / "tasks" / "area" / "2026-05"
    d.mkdir(parents=True, exist_ok=True)
    p = d / fname
    p.write_text(body)
    return p


def _archive_task(tmp_path, *, fname, body):
    """Write an archived task .md under Projects/P/archive/."""
    d = tmp_path / "Projects" / "P" / "archive" / "area" / "2026-04"
    d.mkdir(parents=True, exist_ok=True)
    p = d / fname
    p.write_text(body)
    return p


def _draft_fm(tid):
    return (
        f"---\nid: {tid}\nstatus: draft\npersonas: [a]\n---\n\nbody\n"
    )


def _dispatched_no_owner_fm(tid, *, thread_tid="111111111111111111"):
    return (
        f"---\nid: {tid}\n"
        f"status: dispatched\n"
        f'thread: "[t](https://discord.com/channels/G/{thread_tid})"\n'
        f"dispatched: 2026-05-16\n"
        f"dispatch_msg_id: MID\n"
        f"personas: [a]\n"
        f"---\n\nbody\n"
    )


def _dispatched_with_owner_fm(tid, *, owner, thread_tid="222222222222222222"):
    return (
        f"---\nid: {tid}\n"
        f"status: dispatched\n"
        f'thread: "[t](https://discord.com/channels/G/{thread_tid})"\n'
        f"dispatched: 2026-05-16\n"
        f"dispatch_msg_id: MID\n"
        f"owner: {owner}\n"
        f"personas: [a]\n"
        f"---\n\nbody\n"
    )


# ---------------------------------------------------------------------------
# _iter_task_files: walks tasks/ + archive/, skips READMEs
# ---------------------------------------------------------------------------


def test_iter_task_files_walks_tasks_and_archive(tmp_path):
    a = _task(tmp_path, fname="2026-05-18-aaa111-active.md", body="x")
    b = _archive_task(tmp_path, fname="2026-04-01-bbb222-archived.md", body="x")
    # Project README should NOT appear
    (tmp_path / "Projects" / "P" / "README.md").write_text("readme")
    found = set(backfill._iter_task_files(str(tmp_path)))
    assert found == {str(a), str(b)}


# ---------------------------------------------------------------------------
# _extract_thread_id
# ---------------------------------------------------------------------------


def test_extract_thread_id_from_markdown_link():
    val = '"[task title](https://discord.com/channels/GUILD/123456789012345678)"'
    assert backfill._extract_thread_id(val) == "123456789012345678"


def test_extract_thread_id_returns_none_for_empty_or_garbage():
    assert backfill._extract_thread_id("") is None
    assert backfill._extract_thread_id("no link here") is None


# ---------------------------------------------------------------------------
# _first_butler_user: pulls the right USER, skips chatter
# ---------------------------------------------------------------------------


def _msg(mid, content):
    return {"id": str(mid), "content": content}


def test_first_butler_user_finds_oldest_decorated_message(monkeypatch):
    # Discord returns newest-first. The dispatch pointer is the OLDEST
    # decorated message (typically message #2 in the thread).
    messages = [
        _msg(500, "some later reply"),
        _msg(400, "📨 **[butler:other-name]** later forwarded message"),
        _msg(300, "plain text"),
        _msg(200, "📨 **[butler:weiliu-algo-data]** Plan approved..."),
        _msg(100, "/bind /work claude"),  # not decorated
    ]
    monkeypatch.setattr(backfill, "api", lambda *a, **kw: (200, messages))
    assert backfill._first_butler_user("THREAD") == "weiliu-algo-data"


def test_first_butler_user_returns_none_when_no_decorated_message(monkeypatch):
    messages = [_msg(1, "/bind /work claude"), _msg(2, "plain reply")]
    monkeypatch.setattr(backfill, "api", lambda *a, **kw: (200, messages))
    assert backfill._first_butler_user("THREAD") is None


def test_first_butler_user_returns_none_on_fetch_failure(monkeypatch):
    monkeypatch.setattr(backfill, "api", lambda *a, **kw: (404, "Not Found"))
    assert backfill._first_butler_user("THREAD") is None


def test_first_butler_user_pages_with_before_cursor(monkeypatch):
    """If page1 fills the limit (100) with no [butler:] match, the
    backfiller advances ``before=<min-id-seen>`` and tries page 2.
    Returning fewer than 100 messages signals end-of-history and stops
    pagination."""
    # Full page1 of 100 non-matching messages with ids 901..1000 (newest-first).
    page1 = [_msg(1000 - i, f"chatter {i}") for i in range(100)]
    page2 = [_msg(700, "no"), _msg(200, "📨 **[butler:kathy]** old dispatch")]
    calls: list[dict] = []

    def fake_api(path, method="GET", body=None, query=None):
        calls.append(query or {})
        return (200, page1 if len(calls) == 1 else page2)

    monkeypatch.setattr(backfill, "api", fake_api)
    assert backfill._first_butler_user("THREAD") == "kathy"
    # Second call must have used before=<min of page1> = 901
    assert calls[1].get("before") == "901"


# ---------------------------------------------------------------------------
# backfill_owners: end-to-end (dry-run + apply)
# ---------------------------------------------------------------------------


def test_backfill_dry_run_does_not_modify(tmp_path, monkeypatch, capsys):
    p = _task(
        tmp_path, fname="2026-05-18-aaa111-x.md",
        body=_dispatched_no_owner_fm("aaa111"),
    )
    orig = p.read_text()
    monkeypatch.setattr(
        backfill, "api",
        lambda *a, **kw: (200, [_msg(1, "📨 **[butler:weiliu]** dispatch")]),
    )
    rc = backfill.backfill_owners(str(tmp_path), apply=False)
    assert rc == 0
    assert p.read_text() == orig  # no change
    out = capsys.readouterr().out
    assert "would:" in out and "weiliu" in out


def test_backfill_apply_writes_owner(tmp_path, monkeypatch, capsys):
    p = _task(
        tmp_path, fname="2026-05-18-aaa111-x.md",
        body=_dispatched_no_owner_fm("aaa111"),
    )
    monkeypatch.setattr(
        backfill, "api",
        lambda *a, **kw: (200, [_msg(1, "📨 **[butler:weiliu-algo-data]** dispatch")]),
    )
    rc = backfill.backfill_owners(str(tmp_path), apply=True)
    assert rc == 0
    fm = dispatch_task.parse_frontmatter(str(p))
    assert fm["owner"] == "weiliu-algo-data"
    # Idempotency: a second pass must skip (and not call api again, but
    # the test wouldn't detect that — what we DO assert is no breakage)
    rc2 = backfill.backfill_owners(str(tmp_path), apply=True)
    assert rc2 == 0


def test_backfill_skips_drafts_and_already_owned(tmp_path, monkeypatch, capsys):
    _task(tmp_path, fname="2026-05-18-aaa111-draft.md", body=_draft_fm("aaa111"))
    _task(
        tmp_path, fname="2026-05-18-bbb222-owned.md",
        body=_dispatched_with_owner_fm("bbb222", owner="kathy"),
    )
    target = _task(
        tmp_path, fname="2026-05-18-ccc333-needs.md",
        body=_dispatched_no_owner_fm("ccc333"),
    )

    def fake_api(*a, **kw):
        return (200, [_msg(1, "📨 **[butler:weiliu]** dispatch")])
    monkeypatch.setattr(backfill, "api", fake_api)

    backfill.backfill_owners(str(tmp_path), apply=True)
    # Only the third task touched.
    fm_target = dispatch_task.parse_frontmatter(str(target))
    assert fm_target["owner"] == "weiliu"
    out = capsys.readouterr().out
    assert "skipped (draft):     1" in out
    assert "skipped (owner set): 1" in out


def test_backfill_reports_unresolved_but_does_not_abort(tmp_path, monkeypatch, capsys):
    """Thread with no [butler:USER] message → row reported as unresolved,
    other rows still processed, exit code 0."""
    _task(
        tmp_path, fname="2026-05-18-aaa111-no-butler.md",
        body=_dispatched_no_owner_fm("aaa111", thread_tid="111111111111111111"),
    )
    target = _task(
        tmp_path, fname="2026-05-18-bbb222-good.md",
        body=_dispatched_no_owner_fm("bbb222", thread_tid="222222222222222222"),
    )

    def fake_api(path, method="GET", body=None, query=None):
        if "111111111111111111" in path:
            return (200, [_msg(1, "no butler decoration anywhere")])
        return (200, [_msg(1, "📨 **[butler:weiliu]** dispatch")])
    monkeypatch.setattr(backfill, "api", fake_api)

    rc = backfill.backfill_owners(str(tmp_path), apply=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "unresolved:     1" in out
    fm = dispatch_task.parse_frontmatter(str(target))
    assert fm["owner"] == "weiliu"
