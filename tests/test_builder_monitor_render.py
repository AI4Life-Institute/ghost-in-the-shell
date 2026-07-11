"""Integration: the real BuilderEventMonitor driving the real BuilderRenderer.

Proves the T6→T7 seam end to end: only structured events produce cards (AC1),
and a crash-window replay (monitor receipts lost) renders nothing new because
the renderer's synchronous index dedups it (AC: at-least-once, repaired
renderer-side).
"""

import json

from gits.core.builder_event_monitor import BuilderEventMonitor
from gits.core.builder_registry import BuilderRegistry
from gits.core.builder_renderer import BuilderRenderer

UID = "builder-os:17"
CHAN = "1000"
ASSIST = "2000"


class FakeAdapter:
    def __init__(self):
        self.sent = []
        self.pinned = []
        self._n = 0

    async def send_message(self, channel_id, msg):
        self._n += 1
        self.sent.append((channel_id, msg))
        return f"m{self._n}"

    async def edit_message(self, channel_id, message_id, msg):
        pass

    async def delete_message(self, channel_id, message_id):
        pass

    async def pin_message(self, channel_id, message_id):
        self.pinned.append((channel_id, message_id))

    async def unpin_message(self, channel_id, message_id):
        pass


def _event(seq, etype, state, payload=None):
    return {
        "protocol": 1, "event_id": f"E{seq}", "sequence": seq,
        "occurred_at": "2026-07-11T00:00:00Z", "ticket_uid": UID,
        "driver_session_id": "drv-1", "epoch": 1, "role": "coder",
        "type": etype, "state": state, "summary": f"{etype} {seq}",
        "payload": payload or {},
    }


def _setup(tmp_path):
    log = tmp_path / "events.jsonl"
    lines = [
        _event(1, "driver.started", "ACTIVE"),
        _event(2, "driver.human_input_required", "BLOCKED",
               {"decision_id": "D1", "question": "Pick", "options": [{"id": "a", "label": "A"}]}),
    ]
    log.write_text("".join(json.dumps(e) + "\n" for e in lines))

    reg_file = tmp_path / "builder_tickets.json"
    reg_file.write_text(json.dumps({UID: {
        "runtime_dir": str(tmp_path), "event_log": str(log),
        "channel_id": CHAN, "assistant_channel_id": ASSIST,
        "driver_session_id": "drv-1", "capability_token": "cap",
    }}))
    reg = BuilderRegistry(registry_file=reg_file)

    renderer = BuilderRenderer(reg, state_file=tmp_path / "renderer.json",
                               coalesce_seconds=60.0, clock=lambda: 0.0)
    fake = FakeAdapter()
    renderer.set_adapter(fake)

    monitor = BuilderEventMonitor(reg, offsets_file=tmp_path / "offsets.json")
    monitor.on_event(renderer.on_event)
    monitor.on_fault(renderer.on_fault)
    return monitor, renderer, fake


async def test_structured_events_drive_cards_and_suppression(tmp_path):
    monitor, renderer, fake = _setup(tmp_path)
    await monitor._poll_once()

    # A decision card was rendered from the structured event (AC1) and pinned;
    # the projection is suppressed because a decision is open (§5.3).
    assert any(m.embed and "Human input required" in (m.embed.title or "")
               for (_, m) in fake.sent)
    assert monitor.is_suppressed(UID) is True
    assert renderer.decision_record(UID, "D1")["status"] == "open"


async def test_crash_window_replay_renders_nothing_new(tmp_path):
    monitor, renderer, fake = _setup(tmp_path)
    await monitor._poll_once()
    n = len(fake.sent)
    assert n >= 2  # thin line + decision card (+ mirror)

    # Simulate a crash before receipts/offsets were persisted: wipe the
    # monitor's durable state so it re-reads from byte 0 and re-dispatches.
    monitor._offsets.clear()
    monitor._mtimes.clear()
    monitor._receipts.clear()
    monitor._folded.clear()
    monitor._proj.clear()
    monitor._fold_eof.clear()

    await monitor._poll_once()
    # The renderer's own persisted index dedups every replayed event.
    assert len(fake.sent) == n
