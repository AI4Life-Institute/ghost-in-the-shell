"""Tests for ``ghost butler dispatch --account`` precedence + /bind plumbing."""

from __future__ import annotations

import pytest

from gits.butler import dispatch_task as dt


@pytest.fixture
def fake_dispatch(monkeypatch):
    """Stub out every external side-effect; record bind_msg and writeback updates."""
    captured: dict = {"sent": [], "updates": None}

    def _stub_send(_target, content, *, cwd=None):
        captured["sent"].append(content)
        return f"mid-{len(captured['sent'])}"

    monkeypatch.setattr(dt, "_vault_root", lambda cwd=None: "/vault")
    monkeypatch.setattr(dt, "resolve_task_file", lambda *a, **k: "/tmp/task.md")
    monkeypatch.setattr(dt, "resolve_work_dir", lambda *a, **k: "/work")
    monkeypatch.setattr(dt, "read_home_channel",
                        lambda cwd=None: ("cid", "gid"))
    monkeypatch.setattr(dt, "preflight", lambda *a, **k: None)
    monkeypatch.setattr(dt, "_create_thread", lambda *a, **k: "tid")
    monkeypatch.setattr(dt, "writeback_frontmatter_atomic",
                        lambda _p, u: captured.update(updates=dict(u)))
    monkeypatch.setattr(dt, "lint", lambda *a, **k: [])
    monkeypatch.setattr(dt.identity, "resolve_user",
                        lambda **kw: ("owner-x", None))
    monkeypatch.setattr(dt.time, "sleep", lambda _s: None)

    # Sentinel: pass fm_account=ABSENT to omit the `account:` key entirely
    # (vs. a present-but-empty value like "null").
    ABSENT = object()

    def run(fm_account, flag, picker):
        captured["sent"].clear()
        captured["updates"] = None

        def _fake_fm(_p):
            fm = {
                "id": "gbraq8",
                "project": "Ghost",
                "status": "draft",
                "personas": "[senior engineer]",
                "cli": "claude",
            }
            if fm_account is not ABSENT:
                fm["account"] = fm_account
            return fm
        monkeypatch.setattr(dt, "parse_frontmatter", _fake_fm)
        monkeypatch.setattr(dt, "_pick_account_or_none", lambda: picker)

        dt.dispatch_task(
            "gbraq8", "plan",
            cwd="/tmp", send_decorated=_stub_send, account=flag,
        )
        return captured

    run.ABSENT = ABSENT
    return run


def test_frontmatter_pin_ignored_when_flag_absent(fake_dispatch):
    """Headline regression guard: a task page with `account: someacct` and NO
    --account flag MUST be ignored — the dispatcher auto-balances and the
    writeback OVERWRITES the stale pin with the account actually used."""
    c = fake_dispatch(fm_account="someacct", flag=None, picker="auto-x")
    # frontmatter pin is NOT read → picker wins → /bind uses the picked account
    assert c["sent"][0] == "/bind /work claude --account=auto-x"
    # writeback overwrites the stale pin with the account actually used
    assert (c["updates"] or {}).get("account") == "auto-x"


def test_flag_overrides_frontmatter_and_picker(fake_dispatch):
    c = fake_dispatch(fm_account="foo", flag="bar", picker="auto-x")
    assert c["sent"][0] == "/bind /work claude --account=bar"
    # account is now a write-only record: the used account is always stamped
    assert (c["updates"] or {}).get("account") == "bar"


def test_flag_wins_over_frontmatter_absent(fake_dispatch):
    c = fake_dispatch(fm_account=fake_dispatch.ABSENT, flag="bar", picker="auto-x")
    assert c["sent"][0] == "/bind /work claude --account=bar"
    assert (c["updates"] or {}).get("account") == "bar"


def test_explicit_auto_flag_runs_picker(fake_dispatch):
    c = fake_dispatch(fm_account="null", flag="auto", picker="auto-x")
    assert c["sent"][0] == "/bind /work claude --account=auto-x"
    assert (c["updates"] or {}).get("account") == "auto-x"


def test_picker_used_when_account_field_absent(fake_dispatch):
    """No `account:` key at all + no flag ⇒ auto-picker, writeback inserts."""
    c = fake_dispatch(fm_account=fake_dispatch.ABSENT, flag=None, picker="auto-x")
    assert c["sent"][0] == "/bind /work claude --account=auto-x"
    assert (c["updates"] or {}).get("account") == "auto-x"


def test_picker_none_falls_back_to_legacy_no_flag(fake_dispatch):
    c = fake_dispatch(fm_account=fake_dispatch.ABSENT, flag=None, picker=None)
    # Byte-identical to today's /bind when no balancing is possible
    assert c["sent"][0] == "/bind /work claude"
    # nothing picked → page stays clean (no account stamped)
    assert "account" not in (c["updates"] or {})


def test_invalid_account_name_aborts(fake_dispatch):
    with pytest.raises(SystemExit):
        fake_dispatch(fm_account=fake_dispatch.ABSENT, flag="Invalid Name!!",
                      picker=None)


def test_real_writeback_inserts_account_for_absent_field(tmp_path, monkeypatch):
    """End-to-end with the REAL writeback (not stubbed): a task page with no
    `account:` line, no flag, picker→fixed name ⇒ the file gains an
    `account: <name>` line. Locks the insert path the docs now promise."""
    p = tmp_path / "task.md"
    p.write_text(
        "---\n"
        "id: gbraq8\n"
        "project: Ghost\n"
        "status: draft\n"
        "personas: [senior engineer]\n"
        "cli: claude\n"
        "---\n\nbody\n"
    )

    monkeypatch.setattr(dt, "_vault_root", lambda cwd=None: "/vault")
    monkeypatch.setattr(dt, "resolve_task_file", lambda *a, **k: str(p))
    monkeypatch.setattr(dt, "resolve_work_dir", lambda *a, **k: "/work")
    monkeypatch.setattr(dt, "read_home_channel", lambda cwd=None: ("cid", "gid"))
    monkeypatch.setattr(dt, "preflight", lambda *a, **k: None)
    monkeypatch.setattr(dt, "_create_thread", lambda *a, **k: "tid")
    monkeypatch.setattr(dt, "lint", lambda *a, **k: [])
    monkeypatch.setattr(dt.identity, "resolve_user", lambda **kw: ("owner-x", None))
    monkeypatch.setattr(dt.time, "sleep", lambda _s: None)
    monkeypatch.setattr(dt, "_pick_account_or_none", lambda: "picked-acct")

    def _stub_send(_target, content, *, cwd=None):
        return "mid-1"

    dt.dispatch_task("gbraq8", "plan", cwd="/tmp",
                     send_decorated=_stub_send, account=None)

    text = p.read_text()
    assert "account: picked-acct\n" in text
    assert text.endswith("\nbody\n")  # body preserved
