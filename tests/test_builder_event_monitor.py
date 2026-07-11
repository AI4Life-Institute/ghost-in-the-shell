"""Tests for BuilderEventMonitor (G2), mapped to builder-os#8's ACs.

Hermetic: no Discord, no builder-os checkout — a fake sink captures dispatch +
fault calls, event logs are written into tmp_path.

AC map (issue #8, restated per 0002 §4.3 which supersedes "exactly-once"):
  AC1  events written while ghost down → delivered once; receipts dedup replay;
       deliver-then-advance on failure; residual is at-least-once (≤1 duplicate).
  AC2  fresh ticket, events precede first discovery → all render (byte-0 rule).
  AC3  restart between a blocking event and its answer → suppressed after fold.
  AC4  gapped/out-of-order sequence → that ticket frozen; others unaffected.
  AC5  unknown protocol → one fault, that ticket frozen.
  + dormancy (no registry ⇒ no-op) and per-driver_session_id sequencing.
"""

import json

from gits.core.builder_event_monitor import (
    FREEZE_PROTOCOL,
    FREEZE_SCHEMA,
    FREEZE_SEQUENCE,
    BuilderEventMonitor,
)
from gits.core.builder_registry import BuilderRegistry

# -- fixtures / helpers ------------------------------------------------------


class FakeSink:
    """Captures dispatched events and faults; can be told to fail on event ids."""

    def __init__(self):
        self.events: list[tuple[str, str, str]] = []  # (uid, event_id, type)
        self.faults: list[tuple[str, str, str]] = []  # (uid, kind, detail)
        self.fail_on: set[str] = set()
        self._n = 0

    async def on_event(self, uid, event):
        if event.event_id in self.fail_on:
            raise RuntimeError("simulated dispatch failure")
        self._n += 1
        self.events.append((uid, event.event_id, event.type))
        return f"msg-{self._n}"

    async def on_fault(self, uid, kind, detail):
        self.faults.append((uid, kind, detail))

    def ids(self, uid=None):
        return [e[1] for e in self.events if uid is None or e[0] == uid]


def make_event(seq, etype, state, *, eid=None, sid="drv-1", decision_id=None,
               protocol=1, uid="builder-os:17"):
    ev = {
        "protocol": protocol,
        "event_id": eid or f"evt-{sid}-{seq}",
        "sequence": seq,
        "occurred_at": "2026-07-11T10:20:00Z",
        "ticket_uid": uid,
        "driver_session_id": sid,
        "epoch": 1,
        "role": "coder",
        "type": etype,
        "state": state,
        "summary": f"{etype} #{seq}",
        "payload": {"decision_id": decision_id} if decision_id else {},
    }
    return ev


def write_log(path, events, *, trailing_partial=None):
    lines = [json.dumps(e) for e in events]
    text = "".join(line + "\n" for line in lines)
    if trailing_partial is not None:
        text += trailing_partial  # no newline — simulates a mid-write tail
    path.write_text(text)


def setup(tmp_path, logs: dict[str, list[dict]]):
    """Write a registry + one event log per uid. Returns (monitor, sink, paths)."""
    reg_file = tmp_path / "builder_tickets.json"
    registry = {}
    paths = {}
    for uid, events in logs.items():
        log_path = tmp_path / f"{uid.replace(':', '_')}.jsonl"
        write_log(log_path, events)
        registry[uid] = {"runtime_dir": str(tmp_path), "event_log": str(log_path)}
        paths[uid] = log_path
    reg_file.write_text(json.dumps(registry))
    reg = BuilderRegistry(reg_file)
    mon = BuilderEventMonitor(reg, offsets_file=tmp_path / "builder_event_offsets.json")
    sink = FakeSink()
    mon.on_event(sink.on_event)
    mon.on_fault(sink.on_fault)
    return mon, sink, paths


def reload(tmp_path):
    """Fresh monitor over the same tmp registry + persisted offsets (a 'restart')."""
    reg = BuilderRegistry(tmp_path / "builder_tickets.json")
    mon = BuilderEventMonitor(reg, offsets_file=tmp_path / "builder_event_offsets.json")
    sink = FakeSink()
    mon.on_event(sink.on_event)
    mon.on_fault(sink.on_fault)
    return mon, sink


# -- dormancy ----------------------------------------------------------------


class TestDormancy:
    async def test_no_registry_is_noop(self, tmp_path):
        reg = BuilderRegistry(tmp_path / "builder_tickets.json")
        mon = BuilderEventMonitor(reg, offsets_file=tmp_path / "builder_event_offsets.json")
        sink = FakeSink()
        mon.on_event(sink.on_event)
        await mon._poll_once()
        assert sink.events == []
        # No store file created — truly zero footprint when absent.
        assert not (tmp_path / "builder_event_offsets.json").exists()


# -- AC2: byte-0 -------------------------------------------------------------


class TestByteZero:
    async def test_events_before_discovery_all_render(self, tmp_path):
        events = [
            make_event(1, "driver.started", "ACTIVE"),
            make_event(2, "driver.progress", "ACTIVE"),
            make_event(3, "driver.checkpointed", "ACTIVE"),
        ]
        mon, sink, _ = setup(tmp_path, {"builder-os:17": events})
        await mon._poll_once()
        assert sink.ids() == ["evt-drv-1-1", "evt-drv-1-2", "evt-drv-1-3"]

    async def test_no_re_dispatch_after_offset_advances(self, tmp_path):
        events = [make_event(1, "driver.started", "ACTIVE")]
        mon, sink, _ = setup(tmp_path, {"builder-os:17": events})
        await mon._poll_once()
        await mon._poll_once()  # nothing new
        assert sink.ids() == ["evt-drv-1-1"]


# -- AC1: delivery / dedup / at-least-once -----------------------------------


class TestDelivery:
    async def test_replay_after_restart_no_duplicate(self, tmp_path):
        events = [make_event(i, "driver.progress", "ACTIVE") for i in (1, 2, 3)]
        events[0] = make_event(1, "driver.started", "ACTIVE")
        mon, sink, _ = setup(tmp_path, {"builder-os:17": events})
        await mon._poll_once()
        mon.stop()  # force-persists offsets + receipts
        mon2, sink2 = reload(tmp_path)
        await mon2._poll_once()
        assert sink2.events == []  # offset at EOF, nothing re-delivered

    async def test_receipts_dedup_on_full_replay(self, tmp_path):
        # Simulate a replay that re-reads from byte 0 but with receipts present:
        # receipts must prevent duplicate dispatch (§4.3).
        events = [make_event(1, "driver.started", "ACTIVE"),
                  make_event(2, "driver.progress", "ACTIVE")]
        mon, sink, _ = setup(tmp_path, {"builder-os:17": events})
        await mon._poll_once()
        mon.stop()
        # Rewind the persisted offset to 0 while keeping receipts.
        store = json.loads((tmp_path / "builder_event_offsets.json").read_text())
        store["builder-os:17"]["offset"] = 0
        (tmp_path / "builder_event_offsets.json").write_text(json.dumps(store))
        mon2, sink2 = reload(tmp_path)
        await mon2._poll_once()
        assert sink2.events == []  # every event already receipted → skipped

    async def test_deliver_then_advance_pins_on_failure(self, tmp_path):
        events = [make_event(i, "driver.progress", "ACTIVE") for i in (1, 2, 3)]
        events[0] = make_event(1, "driver.started", "ACTIVE")
        mon, sink, _ = setup(tmp_path, {"builder-os:17": events})
        sink.fail_on = {"evt-drv-1-2"}
        await mon._poll_once()
        # event1 delivered; event2 failed → offset pinned; event3 not reached.
        assert sink.ids() == ["evt-drv-1-1"]
        # Recover and re-poll: event2, event3 delivered; event1 NOT repeated.
        sink.fail_on = set()
        await mon._poll_once()
        assert sink.ids() == ["evt-drv-1-1", "evt-drv-1-2", "evt-drv-1-3"]

    async def test_live_append_dispatch_failure_does_not_freeze(self, tmp_path):
        # Regression (CR round 1 blocker): a live-appended event (is_new, so the
        # sequence path runs) whose dispatch fails once must retry cleanly — the
        # sequence cursor must NOT advance past a non-delivered event, else the
        # retry re-reads the same line, sees a spurious gap, and freezes forever.
        mon, sink, paths = setup(tmp_path, {
            "builder-os:17": [make_event(1, "driver.started", "ACTIVE")],
        })
        await mon._poll_once()  # seq 1 folded + delivered; fold_eof set here
        assert sink.ids() == ["evt-drv-1-1"]
        # Append seq 2 AFTER the fold (is_new=True) and fail its first dispatch.
        with open(paths["builder-os:17"], "a") as f:
            f.write(json.dumps(make_event(2, "driver.progress", "ACTIVE")) + "\n")
        sink.fail_on = {"evt-drv-1-2"}
        await mon._poll_once()
        assert sink.ids() == ["evt-drv-1-1"]  # seq 2 pinned, not delivered
        assert not mon.is_frozen("builder-os:17")  # must NOT freeze on retry
        # Recover: seq 2 delivered exactly once, still not frozen.
        sink.fail_on = set()
        await mon._poll_once()
        assert sink.ids() == ["evt-drv-1-1", "evt-drv-1-2"]
        assert not mon.is_frozen("builder-os:17")
        # And a subsequent in-sequence event still flows (cursor is correct).
        with open(paths["builder-os:17"], "a") as f:
            f.write(json.dumps(make_event(3, "driver.checkpointed", "ACTIVE")) + "\n")
        await mon._poll_once()
        assert sink.ids() == ["evt-drv-1-1", "evt-drv-1-2", "evt-drv-1-3"]

    async def test_at_least_once_residual_is_one_duplicate(self, tmp_path):
        # Honest at-least-once (supersedes issue #8 "exactly-once"): if a crash
        # lands after a successful post but before the receipt+offset persist,
        # the event is re-delivered exactly once on recovery.
        events = [make_event(1, "driver.started", "ACTIVE"),
                  make_event(2, "driver.progress", "ACTIVE")]
        mon, sink, _ = setup(tmp_path, {"builder-os:17": events})
        await mon._poll_once()
        mon.stop()
        # Emulate the crash window: drop event2's receipt and rewind the offset
        # to just before event2 (as if the post happened but persist didn't).
        store = json.loads((tmp_path / "builder_event_offsets.json").read_text())
        recs = store["builder-os:17"]["receipts"]
        recs.pop("evt-drv-1-2", None)
        line1_len = len(json.dumps(events[0])) + 1
        store["builder-os:17"]["offset"] = line1_len
        (tmp_path / "builder_event_offsets.json").write_text(json.dumps(store))
        mon2, sink2 = reload(tmp_path)
        await mon2._poll_once()
        # Exactly one re-delivery (event2); event1 stays deduped by its receipt.
        assert sink2.ids() == ["evt-drv-1-2"]


# -- AC3: startup fold / suppression -----------------------------------------


class TestStartupFold:
    async def test_open_decision_suppresses_after_fold(self, tmp_path):
        events = [
            make_event(1, "driver.started", "ACTIVE"),
            make_event(2, "driver.blocked", "BLOCKED", decision_id="dec-1"),
            make_event(3, "driver.human_input_required", "BLOCKED", decision_id="dec-1"),
        ]
        mon, sink, _ = setup(tmp_path, {"builder-os:17": events})
        await mon._poll_once()
        mon.stop()
        # Restart: fold must rebuild suppression from the full log before forwarding.
        mon2, _ = reload(tmp_path)
        await mon2._poll_once()
        assert mon2.is_suppressed("builder-os:17") is True
        assert mon2.open_decision_id("builder-os:17") == "dec-1"

    async def test_consume_clears_suppression(self, tmp_path):
        events = [
            make_event(1, "driver.started", "ACTIVE"),
            make_event(2, "driver.human_input_required", "BLOCKED", decision_id="dec-1"),
        ]
        mon, sink, paths = setup(tmp_path, {"builder-os:17": events})
        await mon._poll_once()
        assert mon.is_suppressed("builder-os:17") is True
        # The human answered — driver consumed the input.
        consume = make_event(3, "driver.human_input_consumed", "ACTIVE", decision_id="dec-1")
        with open(paths["builder-os:17"], "a") as f:
            f.write(json.dumps(consume) + "\n")
        await mon._poll_once()
        assert mon.is_suppressed("builder-os:17") is False
        assert mon.open_decision_id("builder-os:17") is None


# -- AC4: sequence freeze (per-ticket isolation) -----------------------------


class TestSequenceFreeze:
    async def test_gap_freezes_only_that_ticket(self, tmp_path):
        good = [make_event(i, "driver.progress", "ACTIVE") for i in (1, 2, 3)]
        good[0] = make_event(1, "driver.started", "ACTIVE")
        mon, sink, paths = setup(tmp_path, {
            "builder-os:17": [make_event(1, "driver.started", "ACTIVE")],
            "builder-os:99": good,
        })
        # First poll: both fine (ticket A has just seq 1).
        await mon._poll_once()
        # Append a gapped event (2 missing → jump to 3) on ticket A.
        gapped = make_event(3, "driver.progress", "ACTIVE")
        with open(paths["builder-os:17"], "a") as f:
            f.write(json.dumps(gapped) + "\n")
        await mon._poll_once()
        assert mon.is_frozen("builder-os:17") is True
        assert mon.freeze_reason("builder-os:17") == FREEZE_SEQUENCE
        assert any(k == FREEZE_SEQUENCE for _, k, _ in sink.faults)
        # Ticket B unaffected: all delivered, not frozen.
        assert mon.is_frozen("builder-os:99") is False
        assert sink.ids("builder-os:99") == ["evt-drv-1-1", "evt-drv-1-2", "evt-drv-1-3"]
        # The gapped event on A was NOT dispatched.
        assert "evt-drv-1-3" not in sink.ids("builder-os:17")

    async def test_frozen_persists_across_restart_and_skips(self, tmp_path):
        mon, sink, paths = setup(tmp_path, {
            "builder-os:17": [make_event(1, "driver.started", "ACTIVE"),
                              make_event(3, "driver.progress", "ACTIVE")],  # gap at fold
        })
        await mon._poll_once()
        assert mon.is_frozen("builder-os:17")
        mon.stop()
        mon2, sink2 = reload(tmp_path)
        assert mon2.is_frozen("builder-os:17") is True  # freeze survived restart
        await mon2._poll_once()
        assert sink2.events == []  # frozen ticket is skipped
        assert sink2.faults == []  # not re-faulted


# -- AC5: unknown protocol ---------------------------------------------------


class TestUnknownProtocol:
    async def test_unknown_protocol_freezes_ticket(self, tmp_path):
        mon, sink, paths = setup(tmp_path, {
            "builder-os:17": [make_event(1, "driver.started", "ACTIVE")],
        })
        await mon._poll_once()
        assert sink.ids() == ["evt-drv-1-1"]
        # A future protocol appears.
        future = make_event(2, "driver.progress", "ACTIVE", protocol=2)
        with open(paths["builder-os:17"], "a") as f:
            f.write(json.dumps(future) + "\n")
        await mon._poll_once()
        assert mon.is_frozen("builder-os:17")
        assert mon.freeze_reason("builder-os:17") == FREEZE_PROTOCOL
        assert [k for _, k, _ in sink.faults] == [FREEZE_PROTOCOL]


# -- schema-invalid (tamper) --------------------------------------------------


class TestSchemaInvalidFreeze:
    async def test_unparseable_complete_line_freezes(self, tmp_path):
        log = tmp_path / "builder-os_17.jsonl"
        write_log(log, [make_event(1, "driver.started", "ACTIVE")])
        with open(log, "a") as f:
            f.write("{ not valid json }\n")  # complete but corrupt line
        (tmp_path / "builder_tickets.json").write_text(json.dumps(
            {"builder-os:17": {"runtime_dir": str(tmp_path), "event_log": str(log)}}
        ))
        reg = BuilderRegistry(tmp_path / "builder_tickets.json")
        mon = BuilderEventMonitor(reg, offsets_file=tmp_path / "off.json")
        sink = FakeSink()
        mon.on_event(sink.on_event)
        mon.on_fault(sink.on_fault)
        await mon._poll_once()
        assert mon.freeze_reason("builder-os:17") == FREEZE_SCHEMA


# -- per-driver_session_id sequencing ----------------------------------------


class TestPerSessionSequence:
    async def test_sequence_resets_per_session(self, tmp_path):
        # A resume produces a NEW driver_session_id whose sequence restarts at 1.
        # A per-ticket global counter would wrongly freeze at drv-2 seq 1.
        events = [
            make_event(1, "driver.started", "ACTIVE", sid="drv-1"),
            make_event(2, "driver.progress", "ACTIVE", sid="drv-1"),
            make_event(1, "driver.resumed", "ACTIVE", sid="drv-2", eid="evt-r1"),
            make_event(2, "driver.progress", "ACTIVE", sid="drv-2", eid="evt-r2"),
        ]
        mon, sink, _ = setup(tmp_path, {"builder-os:17": events})
        await mon._poll_once()
        assert mon.is_frozen("builder-os:17") is False
        assert len(sink.ids()) == 4


# -- truncated tail ----------------------------------------------------------


class TestTruncatedTail:
    async def test_partial_tail_left_unconsumed(self, tmp_path):
        e1 = make_event(1, "driver.started", "ACTIVE")
        e2 = make_event(2, "driver.progress", "ACTIVE")
        log = tmp_path / "builder-os_17.jsonl"
        # e1 complete, e2 as a torn tail (no newline yet).
        write_log(log, [e1], trailing_partial=json.dumps(e2))
        (tmp_path / "builder_tickets.json").write_text(json.dumps(
            {"builder-os:17": {"runtime_dir": str(tmp_path), "event_log": str(log)}}
        ))
        reg = BuilderRegistry(tmp_path / "builder_tickets.json")
        mon = BuilderEventMonitor(reg, offsets_file=tmp_path / "off.json")
        sink = FakeSink()
        mon.on_event(sink.on_event)
        mon.on_fault(sink.on_fault)
        await mon._poll_once()
        assert sink.ids() == ["evt-drv-1-1"]  # torn tail not delivered
        assert not mon.is_frozen("builder-os:17")  # torn tail is not tamper
        # The append completes (newline lands).
        with open(log, "a") as f:
            f.write("\n")
        await mon._poll_once()
        assert sink.ids() == ["evt-drv-1-1", "evt-drv-1-2"]
