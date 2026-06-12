"""Tests for ``ghost butler dispatch --model`` (openspec add-dispatch-model-pin).

Covers: validate_model_name, the flag > task-page `model:` > none precedence
matrix, fail-fast before thread creation, frontmatter stamping, and the
butler /bind ``--model=`` parse path.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from gits.butler import dispatch_task as dt
from gits.core.account import validate_model_name

# ─── validate_model_name ─────────────────────────────────────────────────


@pytest.mark.parametrize("name", [
    "sonnet", "haiku", "opus", "fable",
    "claude-sonnet-4-6", "claude-fable-5",
    "gpt-5.2-codex",  # charset check, not a claude allowlist
    "a", "A1", "x" * 128,
])
def test_valid_model_names(name):
    validate_model_name(name)  # must not raise


@pytest.mark.parametrize("name", [
    "", " ", "sonnet; rm -rf /", "bad$name", "two words",
    "-leading-dash", ".leading-dot", "back`tick", "x" * 129,
    None, 42,
])
def test_invalid_model_names(name):
    with pytest.raises(ValueError):
        validate_model_name(name)


# ─── dispatch precedence matrix ──────────────────────────────────────────


@pytest.fixture
def fake_dispatch(monkeypatch):
    """Stub every external side-effect; record bind_msg, writeback, threads.

    Same shape as the fixture in test_dispatch_account.py, extended with a
    thread-creation counter so fail-fast tests can assert no thread was made.
    """
    captured: dict = {"sent": [], "updates": None, "threads": 0}

    def _stub_send(_target, content, *, cwd=None):
        captured["sent"].append(content)
        return f"mid-{len(captured['sent'])}"

    def _stub_create_thread(*_a, **_k):
        captured["threads"] += 1
        return "tid"

    monkeypatch.setattr(dt, "_vault_root", lambda cwd=None: "/vault")
    monkeypatch.setattr(dt, "resolve_task_file", lambda *a, **k: "/tmp/task.md")
    monkeypatch.setattr(dt, "resolve_work_dir", lambda *a, **k: "/work")
    monkeypatch.setattr(dt, "read_home_channel",
                        lambda cwd=None: ("cid", "gid"))
    monkeypatch.setattr(dt, "preflight", lambda *a, **k: None)
    monkeypatch.setattr(dt, "_create_thread", _stub_create_thread)
    monkeypatch.setattr(dt, "writeback_frontmatter_atomic",
                        lambda _p, u: captured.update(updates=dict(u)))
    monkeypatch.setattr(dt, "lint", lambda *a, **k: [])
    monkeypatch.setattr(dt.identity, "resolve_user",
                        lambda **kw: ("owner-x", None))
    monkeypatch.setattr(dt.time, "sleep", lambda _s: None)
    # No account in play: keep /bind down to the model token only.
    monkeypatch.setattr(dt, "_pick_account_or_none", lambda: None)

    ABSENT = object()

    def run(fm_model, flag):
        captured["sent"].clear()
        captured["updates"] = None
        captured["threads"] = 0

        def _fake_fm(_p):
            fm = {
                "id": "mdlpin",
                "project": "Ghost",
                "status": "draft",
                "personas": "[senior engineer]",
                "cli": "claude",
            }
            if fm_model is not ABSENT:
                fm["model"] = fm_model
            return fm
        monkeypatch.setattr(dt, "parse_frontmatter", _fake_fm)

        dt.dispatch_task(
            "mdlpin", "plan",
            cwd="/tmp", send_decorated=_stub_send, model=flag,
        )
        return captured

    run.ABSENT = ABSENT
    run.captured = captured
    return run


def test_flag_pins_model(fake_dispatch):
    c = fake_dispatch(fm_model=fake_dispatch.ABSENT, flag="sonnet")
    assert c["sent"][0] == "/bind /work claude --model=sonnet"
    assert (c["updates"] or {}).get("model") == "sonnet"


def test_page_field_used_when_flag_absent(fake_dispatch):
    """Unlike `account:`, the page `model:` field IS read on input."""
    c = fake_dispatch(fm_model="sonnet", flag=None)
    assert c["sent"][0] == "/bind /work claude --model=sonnet"
    assert (c["updates"] or {}).get("model") == "sonnet"


def test_flag_overrides_page_field(fake_dispatch):
    c = fake_dispatch(fm_model="haiku", flag="opus")
    assert c["sent"][0] == "/bind /work claude --model=opus"
    # stamp records what was actually used, overwriting the stale page value
    assert (c["updates"] or {}).get("model") == "opus"


def test_no_model_anywhere_keeps_legacy_bind(fake_dispatch):
    c = fake_dispatch(fm_model=fake_dispatch.ABSENT, flag=None)
    assert c["sent"][0] == "/bind /work claude"
    assert "model" not in (c["updates"] or {})


def test_empty_page_field_treated_as_absent(fake_dispatch):
    c = fake_dispatch(fm_model="", flag=None)
    assert c["sent"][0] == "/bind /work claude"
    assert "model" not in (c["updates"] or {})


def test_invalid_model_fails_before_thread_creation(fake_dispatch):
    with pytest.raises(SystemExit):
        fake_dispatch(fm_model=fake_dispatch.ABSENT, flag="sonnet; rm -rf /")
    assert fake_dispatch.captured["threads"] == 0
    assert fake_dispatch.captured["updates"] is None


def test_invalid_page_model_also_fails_fast(fake_dispatch):
    """A bad value sneaked into the page field must not reach /bind either."""
    with pytest.raises(SystemExit):
        fake_dispatch(fm_model="bad$name", flag=None)
    assert fake_dispatch.captured["threads"] == 0


def test_real_writeback_inserts_model_line(tmp_path, monkeypatch):
    """End-to-end with the REAL writeback: page gains a `model:` line."""
    p = tmp_path / "task.md"
    p.write_text(
        "---\n"
        "id: mdlpin\n"
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
    monkeypatch.setattr(dt, "_pick_account_or_none", lambda: None)

    dt.dispatch_task("mdlpin", "plan", cwd="/tmp",
                     send_decorated=lambda *_a, **_k: "mid-1", model="sonnet")

    text = p.read_text()
    assert "model: sonnet\n" in text
    assert text.endswith("\nbody\n")  # body preserved


# ─── butler /bind --model= parse path ────────────────────────────────────


def _make_adapter():
    """Bare DiscordAdapter with a mocked engine, enough for /bind parsing."""
    from gits.adapters.discord.bot import DiscordAdapter

    adapter = DiscordAdapter.__new__(DiscordAdapter)
    engine = MagicMock()
    engine.handle_bind = AsyncMock()
    engine.settings.coding_cli_command = "claude"
    engine.launcher.discover_all_sessions = MagicMock(return_value=[])
    adapter._engine = engine
    return adapter, engine


def _make_message():
    msg = MagicMock()
    msg.reply = AsyncMock()
    msg.add_reaction = AsyncMock()
    return msg


def test_butler_bind_passes_model_to_engine(tmp_path):
    async def _test():
        adapter, engine = _make_adapter()
        msg = _make_message()
        handled = await adapter._handle_butler_command(
            msg, "ch-1", f"/bind {tmp_path} claude --model=sonnet"
        )
        assert handled is True
        engine.handle_bind.assert_awaited_once()
        assert engine.handle_bind.call_args.kwargs["model"] == "sonnet"

    asyncio.run(_test())


def test_butler_bind_rejects_invalid_model(tmp_path):
    async def _test():
        adapter, engine = _make_adapter()
        msg = _make_message()
        handled = await adapter._handle_butler_command(
            msg, "ch-1", f"/bind {tmp_path} claude --model=bad$name"
        )
        assert handled is True
        engine.handle_bind.assert_not_awaited()
        msg.reply.assert_awaited_once()
        assert "--model" in msg.reply.call_args[0][0]

    asyncio.run(_test())


def test_butler_bind_without_model_passes_none(tmp_path):
    async def _test():
        adapter, engine = _make_adapter()
        msg = _make_message()
        await adapter._handle_butler_command(msg, "ch-1", f"/bind {tmp_path} claude")
        assert engine.handle_bind.call_args.kwargs["model"] is None

    asyncio.run(_test())
