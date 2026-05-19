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

    def run(fm_account, flag, picker):
        captured["sent"].clear()
        captured["updates"] = None

        def _fake_fm(_p):
            return {
                "id": "gbraq8",
                "project": "Ghost",
                "status": "draft",
                "personas": "[senior engineer]",
                "cli": "claude",
                "account": fm_account,
            }
        monkeypatch.setattr(dt, "parse_frontmatter", _fake_fm)
        monkeypatch.setattr(dt, "_pick_account_or_none", lambda: picker)

        dt.dispatch_task(
            "gbraq8", "plan",
            cwd="/tmp", send_decorated=_stub_send, account=flag,
        )
        return captured

    return run


def test_flag_overrides_frontmatter_and_picker(fake_dispatch):
    c = fake_dispatch(fm_account="foo", flag="bar", picker="auto-x")
    assert c["sent"][0] == "/bind /work claude --account=bar"
    # frontmatter already had a value → never overwritten
    assert "account" not in (c["updates"] or {})


def test_frontmatter_used_when_flag_absent(fake_dispatch):
    c = fake_dispatch(fm_account="foo", flag=None, picker="auto-x")
    assert c["sent"][0] == "/bind /work claude --account=foo"
    assert "account" not in (c["updates"] or {})


def test_explicit_auto_flag_runs_picker(fake_dispatch):
    c = fake_dispatch(fm_account="null", flag="auto", picker="auto-x")
    assert c["sent"][0] == "/bind /work claude --account=auto-x"
    assert (c["updates"] or {}).get("account") == "auto-x"


def test_picker_used_when_flag_and_frontmatter_absent(fake_dispatch):
    c = fake_dispatch(fm_account="null", flag=None, picker="auto-x")
    assert c["sent"][0] == "/bind /work claude --account=auto-x"
    assert (c["updates"] or {}).get("account") == "auto-x"


def test_picker_none_falls_back_to_legacy_no_flag(fake_dispatch):
    c = fake_dispatch(fm_account="null", flag=None, picker=None)
    # Byte-identical to today's /bind when no balancing is possible
    assert c["sent"][0] == "/bind /work claude"
    assert "account" not in (c["updates"] or {})


def test_invalid_account_name_aborts(fake_dispatch):
    with pytest.raises(SystemExit):
        fake_dispatch(fm_account="null", flag="Invalid Name!!", picker=None)


def test_normalize_fm_account():
    assert dt._normalize_fm_account(None) is None
    assert dt._normalize_fm_account("null") is None
    assert dt._normalize_fm_account("None") is None
    assert dt._normalize_fm_account("[]") is None
    assert dt._normalize_fm_account('"foo"') == "foo"
    assert dt._normalize_fm_account("  bar  ") == "bar"
