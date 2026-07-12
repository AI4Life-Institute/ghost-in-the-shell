"""Engine-level tests: builder suppression gate, non-builder regression, routing.

The suppression gate is the ONE existing-path touch (0002 §5.3). These tests
prove (a) it holds a builder-bound message while a decision is open, (b) it is
provably inert for non-builder bindings (byte-identical forwarding), and (c)
builder button clicks route to the response adapter without disturbing the
existing button handling.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from gits.adapters.base import IncomingMessage
from gits.config import Settings
from gits.core.builder_event_monitor import Projection
from gits.core.engine import Engine
from gits.core.session import SessionBinding

UID = "builder-os:17"
CHAN = "chan-1"


class FakeAdapter:
    def __init__(self):
        self.sent = []
        self.edited = []

    async def send_message(self, channel_id, msg):
        self.sent.append((channel_id, msg))
        return "m1"

    async def edit_message(self, channel_id, message_id, msg):
        self.edited.append((channel_id, message_id, msg))

    async def delete_message(self, channel_id, message_id):
        pass

    async def pin_message(self, channel_id, message_id):
        pass

    async def unpin_message(self, channel_id, message_id):
        pass


@pytest.fixture
def engine(tmp_path):
    settings = Settings(
        _env_file=None, gits_dir=tmp_path / ".gits", gits_discord_token="t",
        tmux_session_name="test", coding_cli_command="claude",
        allowed_paths=[], bind_root=None, gits_default_path=None,
    )
    e = Engine(settings)
    e.tmux = MagicMock()
    e.tmux.send_text = AsyncMock()
    e.tmux.send_keys = AsyncMock()
    e.tmux.window_exists = AsyncMock(return_value=True)
    e.tmux.pane_current_command = AsyncMock(return_value="claude")
    e.set_adapter(FakeAdapter())
    return e


def _bind(engine, *, builder_uid=None):
    b = SessionBinding(
        platform="discord", channel_id=CHAN, window_id="@1", window_name="w",
        work_dir="/tmp/proj", coding_cli="claude", builder_ticket_uid=builder_uid,
    )
    b.first_interaction_at = time.time()  # skip the first-interaction gate noise
    engine.session_mgr._bindings[CHAN] = b
    return b


def _msg(text="hello"):
    return IncomingMessage(platform="discord", channel_id=CHAN, user_id="u1", text=text)


# --- AC2: suppression while BLOCKED ----------------------------------------

async def test_builder_message_held_while_decision_open(engine):
    _bind(engine, builder_uid=UID)
    engine.builder_event_monitor._proj[UID] = Projection(
        last_state="BLOCKED", open_decision_id="D1", suppressed=True)

    await engine.handle_message(_msg("what colour?"))

    # Not forwarded to the pane …
    engine.tmux.send_text.assert_not_called()
    # … and a guided reply was posted instead.
    assert engine._adapter.sent, "expected a guided-reply message"
    body = engine._adapter.sent[-1][1].text or ""
    assert "forward" in body.lower()


async def test_suppression_released_after_consume(engine):
    _bind(engine, builder_uid=UID)
    # Decision consumed ⇒ projection no longer suppressed.
    engine.builder_event_monitor._proj[UID] = Projection(
        last_state="ACTIVE", open_decision_id=None, suppressed=False)

    await engine.handle_message(_msg("carry on"))

    engine.tmux.send_text.assert_called_once()
    assert engine.tmux.send_text.call_args.args[1] == "carry on"


# --- regression: non-builder bindings are provably unaffected --------------

async def test_non_builder_binding_forwarded_byte_identical(engine):
    _bind(engine, builder_uid=None)  # ordinary binding
    # Spy: the monitor must never even be consulted for a non-builder channel.
    engine.builder_event_monitor.is_suppressed = MagicMock(
        side_effect=AssertionError("is_suppressed must not be called for non-builder bindings"))

    await engine.handle_message(_msg("normal message"))

    engine.tmux.send_text.assert_called_once()
    assert engine.tmux.send_text.call_args.args[1] == "normal message"


async def test_non_builder_unaffected_even_if_some_ticket_suppressed(engine):
    # A different ticket is suppressed, but this binding isn't builder-bound.
    _bind(engine, builder_uid=None)
    engine.builder_event_monitor._proj[UID] = Projection(suppressed=True, open_decision_id="D1")

    await engine.handle_message(_msg("still forwarded"))

    engine.tmux.send_text.assert_called_once()


# --- button routing --------------------------------------------------------

async def test_builder_button_click_routed_to_response_adapter(engine):
    _bind(engine, builder_uid=UID)
    # Unmapped user + no humans file ⇒ the response adapter refuses (returns
    # True = handled), and the existing prompt handling is never reached.
    await engine.handle_button_click(CHAN, "u-unknown", f"bos|d|{UID}|D1|red")
    engine.tmux.send_keys.assert_not_called()
    assert any("Unmapped" in (m.embed.title if m.embed else "")
               for (_, m) in engine._adapter.sent)


async def test_non_builder_button_click_falls_through(engine):
    _bind(engine, builder_uid=UID)
    await engine.handle_button_click(CHAN, "u1", "prompt_opt:@1:2")
    # Existing behavior: option key sent to the pane.
    engine.tmux.send_keys.assert_called_once()
    assert engine.tmux.send_keys.call_args.args[1] == "2"


# --- forced-forward mechanics (§5.3) ---------------------------------------

async def test_forced_forward_bypasses_suppression_and_audits(engine, tmp_path):
    _bind(engine, builder_uid=UID)
    engine.builder_event_monitor._proj[UID] = Projection(suppressed=True, open_decision_id="D1")

    ok = await engine.forced_forward(CHAN, "u1", "force this through")
    assert ok is True
    # Bypasses the gate: text reaches the pane despite suppression.
    engine.tmux.send_text.assert_called_once()
    assert engine.tmux.send_text.call_args.args[1] == "force this through"
    # Ghost-side audit written (never builder-os runtime-state, §5.5).
    log = engine.settings.builder_forced_forward_log
    assert log.exists() and "forced-forward" in log.read_text()


async def test_forced_forward_noop_for_non_builder(engine):
    _bind(engine, builder_uid=None)
    assert await engine.forced_forward(CHAN, "u1", "x") is False


# --- dormancy --------------------------------------------------------------

async def test_dormant_no_registry_zero_new_behavior(engine):
    _bind(engine, builder_uid=None)
    # No builder_tickets.json ⇒ registry dormant, monitor a no-op.
    assert engine.builder_registry.exists() is False
    await engine.handle_message(_msg("ordinary"))
    engine.tmux.send_text.assert_called_once()
    # Nothing builder-side was rendered/persisted.
    assert not engine.settings.builder_renderer_state_file.exists()
