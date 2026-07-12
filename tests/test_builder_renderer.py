"""Tests for BuilderRenderer (G3) — cards, pins, mirrors, coalescing, dedup.

Discord is mocked (FakeAdapter records send/edit/pin calls). Events are built as
raw envelopes wrapped in BuilderEvent — the renderer trusts the monitor's
validation, so envelopes need only the fields the renderer reads.
"""

import json

import pytest

from gits.core.builder_event_monitor import BuilderEvent
from gits.core.builder_registry import BuilderRegistry
from gits.core.builder_renderer import BuilderRenderer, parse_cb

UID = "builder-os:17"
CHAN = "1000"          # ticket thread
ASSIST = "2000"        # persistent Assistant channel


class FakeAdapter:
    def __init__(self):
        self.sent: list = []      # (channel_id, OutgoingMessage, msg_id)
        self.edited: list = []     # (channel_id, message_id, OutgoingMessage)
        self.pinned: list = []     # (channel_id, message_id)
        self.deleted: list = []
        self._n = 0

    async def send_message(self, channel_id, msg):
        self._n += 1
        mid = f"m{self._n}"
        self.sent.append((channel_id, msg, mid))
        return mid

    async def edit_message(self, channel_id, message_id, msg):
        self.edited.append((channel_id, message_id, msg))

    async def delete_message(self, channel_id, message_id):
        self.deleted.append((channel_id, message_id))

    async def pin_message(self, channel_id, message_id):
        self.pinned.append((channel_id, message_id))

    async def unpin_message(self, channel_id, message_id):
        pass


def _registry(tmp_path, **overrides):
    rec = {
        "runtime_dir": "/rt", "event_log": "/rt/events.jsonl",
        "channel_id": CHAN, "assistant_channel_id": ASSIST,
        "driver_session_id": "drv-1", "capability_token": "cap-secret",
    }
    rec.update(overrides)
    f = tmp_path / "builder_tickets.json"
    f.write_text(json.dumps({UID: rec}))
    return BuilderRegistry(registry_file=f)


def _renderer(tmp_path, clock=None, **reg_overrides):
    reg = _registry(tmp_path, **reg_overrides)
    r = BuilderRenderer(
        reg, state_file=tmp_path / "renderer.json",
        coalesce_seconds=60.0, clock=clock or (lambda: 0.0),
    )
    fake = FakeAdapter()
    r.set_adapter(fake)
    return r, fake


def bev(etype, state, *, seq=1, payload=None, refs=None, summary="", event_id=None):
    env = {
        "protocol": 1, "event_id": event_id or f"E{seq}", "sequence": seq,
        "occurred_at": "2026-07-11T00:00:00Z", "ticket_uid": UID,
        "driver_session_id": "drv-1", "epoch": 1, "role": "coder",
        "type": etype, "state": state, "summary": summary, "payload": payload or {},
    }
    if refs is not None:
        env["refs"] = refs
    return BuilderEvent(UID, env)


def _texts(msg):
    """Flatten a card/line to searchable text (content + embed fields)."""
    parts = [msg.text or ""]
    if msg.embed:
        parts += [msg.embed.title or "", msg.embed.description or ""]
        for name, value, _ in msg.embed.fields:
            parts += [name, value]
        parts.append(msg.embed.footer or "")
    return "\n".join(parts)


# --- completion card (AC1, §6.2/D4) ----------------------------------------

async def test_ready_for_human_renders_distinct_groups_pinned_and_mirrored(tmp_path):
    r, fake = _renderer(tmp_path)
    ev = bev(
        "driver.ready_for_human", "READY_FOR_HUMAN", seq=5,
        payload={
            "title": "T17 ready", "candidate_ref": "cand-1",
            "summary_ref": "evidence/completion.md",
            "observations": [{"id": "inspect", "label": "Inspect"},
                             {"id": "open_evidence", "label": "Open evidence"}],
            "decisions": [{"id": "merge", "label": "Merge"},
                          {"id": "close_without_merge", "label": "Close"}],
        },
        refs={"decision": "D-disp"},
    )
    msg_id = await r.on_event(UID, ev)

    # Thread card + Assistant mirror.
    assert len(fake.sent) == 2
    assert fake.sent[0][0] == CHAN
    assert fake.sent[1][0] == ASSIST
    card = fake.sent[0][1]
    body = _texts(card)
    # Observations and decisions are two distinct, labelled groups (D4).
    assert "Observations" in body and "Decisions" in body
    assert "Merge" in body and "Inspect" in body
    # Both thread + mirror are pinned.
    assert (CHAN, msg_id) in fake.pinned
    assert any(ch == ASSIST for ch, _ in fake.pinned)
    # Disposition decision recorded (open) so buttons can resolve.
    assert r.decision_record(UID, "D-disp")["status"] == "open"

    # Observation buttons never carry a decision "choice" kind.
    obs_cbs = [b.callback_data for row in card.buttons for b in row
               if parse_cb(b.callback_data)[0] == "o"]
    assert obs_cbs, "expected observation buttons"


async def test_low_salience_event_not_pinned_or_mirrored(tmp_path):
    # AC1: cards come only from lifecycle events. A driver.started is a thin
    # thread line — no pin, no Assistant mirror, no embed.
    r, fake = _renderer(tmp_path)
    await r.on_event(UID, bev("driver.started", "ACTIVE", summary="started"))
    assert len(fake.sent) == 1
    assert fake.sent[0][0] == CHAN
    assert fake.sent[0][1].embed is None
    assert fake.pinned == []


# --- decision cards (AC3 render side) --------------------------------------

async def test_human_input_required_decision_card(tmp_path):
    r, fake = _renderer(tmp_path)
    ev = bev(
        "driver.human_input_required", "BLOCKED", seq=3,
        payload={"decision_id": "D1", "question": "Pick a colour",
                 "options": [{"id": "red", "label": "Red"}, {"id": "blue", "label": "Blue"}]},
    )
    await r.on_event(UID, ev)
    card = fake.sent[0][1]
    assert "Human input required" in _texts(card)
    assert "Pick a colour" in _texts(card)
    # One decision button per option, addressed to this decision.
    cbs = [parse_cb(b.callback_data) for row in card.buttons for b in row]
    assert ("d", UID, "D1", "red") in cbs
    assert ("d", UID, "D1", "blue") in cbs
    rec = r.decision_record(UID, "D1")
    assert rec["status"] == "open" and rec["kind"] == "human_input"


async def test_escalation_card_carries_why_and_resume_token(tmp_path):
    r, fake = _renderer(tmp_path)
    ev = bev(
        "driver.escalation_requested", "BLOCKED", seq=4,
        payload={"decision_id": "E1", "question": "Approve scope change?",
                 "why_human_owned": "changes the contract",
                 "options": [{"id": "yes", "label": "Yes"}],
                 "resume_token": "RT-9", "evidence_refs": ["evidence/x.md"]},
    )
    await r.on_event(UID, ev)
    body = _texts(fake.sent[0][1])
    assert "Escalation" in body and "changes the contract" in body
    assert r.decision_record(UID, "E1")["resume_token"] == "RT-9"


# --- progress coalescing (AC5, F7) -----------------------------------------

async def test_progress_coalesced_to_at_most_one_line_per_window(tmp_path):
    now = {"t": 0.0}
    r, fake = _renderer(tmp_path, clock=lambda: now["t"])
    # 20 rapid progress events inside one 60s window.
    for i in range(20):
        now["t"] = i * 0.1  # all within the window
        await r.on_event(UID, bev("driver.progress", "ACTIVE", seq=100 + i,
                                   event_id=f"P{i}", payload={"note": f"step {i}"}))
    progress_sends = [m for (_, m, _) in fake.sent]
    assert len(progress_sends) == 1  # only the first rendered (≤1 per window)

    # A later window renders one more line, carrying the elision note.
    now["t"] = 200.0
    await r.on_event(UID, bev("driver.progress", "ACTIVE", seq=200,
                              event_id="Plast", payload={"note": "final"}))
    assert len(fake.sent) == 2
    assert "elided" in _texts(fake.sent[1][1])


async def test_elided_progress_summarised_on_next_card(tmp_path):
    now = {"t": 0.0}
    r, fake = _renderer(tmp_path, clock=lambda: now["t"])
    await r.on_event(UID, bev("driver.progress", "ACTIVE", seq=1, event_id="P0",
                              payload={"note": "a"}))
    for i in range(1, 4):  # coalesced
        now["t"] = i
        await r.on_event(UID, bev("driver.progress", "ACTIVE", seq=1 + i,
                                  event_id=f"P{i}", payload={"note": "b"}))
    # A lifecycle card now surfaces the elision note as a field.
    await r.on_event(UID, bev("driver.failed", "FAILED", seq=10, payload={"reason": "boom"}))
    failed_card = fake.sent[-2][1]  # thread card (last is the mirror)
    assert "elided" in _texts(failed_card)


# --- review mirror ---------------------------------------------------------

async def test_review_verdict_mirrored_to_assistant(tmp_path):
    r, fake = _renderer(tmp_path)
    await r.on_event(UID, bev("review.approved", "DISPOSITION_CHECK", seq=6,
                              payload={"round": 2, "findings_summary": "LGTM"}))
    channels = [ch for (ch, _, _) in fake.sent]
    assert CHAN in channels and ASSIST in channels  # thread line + assistant one-liner


# --- fault card ------------------------------------------------------------

async def test_fault_renders_pinned_mirrored_card(tmp_path):
    r, fake = _renderer(tmp_path)
    await r.on_fault(UID, "unknown_protocol", "unknown protocol: 2 (expected 1)")
    assert "update ghost" in _texts(fake.sent[0][1]).lower()
    assert fake.pinned  # pinned


async def test_fault_replay_renders_nothing_new(tmp_path):
    # CR round 1 (major): on_fault must share on_event's replay/dedup guard —
    # a re-fold / crash-replay that re-freezes a ticket must NOT re-post or
    # re-pin the freeze card (§4.3). Old code (fault path with no guard) posted
    # + pinned twice; this asserts exactly one.
    r, fake = _renderer(tmp_path)
    await r.on_fault(UID, "sequence_gap", "sequence 5 != expected 4")
    n_sent, n_pinned = len(fake.sent), len(fake.pinned)
    assert n_sent >= 1 and n_pinned >= 1

    # The monitor re-emits the same freeze on the next poll after a re-fold.
    await r.on_fault(UID, "sequence_gap", "sequence 5 != expected 4")
    assert len(fake.sent) == n_sent      # no extra card (thread or mirror)
    assert len(fake.pinned) == n_pinned  # not re-pinned


async def test_fault_dedup_survives_reload(tmp_path):
    # The fault dedup key is persisted, so a fresh renderer (restart) still
    # dedups a replayed freeze — the crash-replay scenario, fault edition.
    r, fake = _renderer(tmp_path)
    await r.on_fault(UID, "schema_invalid", "unparseable line at byte 40")
    n_sent = len(fake.sent)

    reg = _registry(tmp_path)
    r2 = BuilderRenderer(reg, state_file=tmp_path / "renderer.json",
                         coalesce_seconds=60.0, clock=lambda: 0.0)
    fake2 = FakeAdapter()
    r2.set_adapter(fake2)
    await r2.on_fault(UID, "schema_invalid", "unparseable line at byte 40")
    assert n_sent >= 1
    assert fake2.sent == []      # nothing re-posted after restart
    assert fake2.pinned == []    # nothing re-pinned


# --- dedup / crash-window repair (§4.3) ------------------------------------

async def test_replayed_event_renders_nothing_new(tmp_path):
    r, fake = _renderer(tmp_path)
    ev = bev("driver.failed", "FAILED", seq=7, event_id="EDUP", payload={"reason": "x"})
    first = await r.on_event(UID, ev)
    n_after_first = len(fake.sent)
    # T6 replays the same event (its receipt wasn't persisted before a crash).
    second = await r.on_event(UID, ev)
    assert second == first
    assert len(fake.sent) == n_after_first  # no extra card


async def test_dedup_survives_reload(tmp_path):
    # The renderer index is persisted synchronously; a fresh instance (restart)
    # still dedups a replayed event.
    r, fake = _renderer(tmp_path)
    ev = bev("driver.failed", "FAILED", seq=8, event_id="EPERSIST", payload={"reason": "x"})
    first = await r.on_event(UID, ev)

    reg = _registry(tmp_path)
    r2 = BuilderRenderer(reg, state_file=tmp_path / "renderer.json",
                         coalesce_seconds=60.0, clock=lambda: 0.0)
    fake2 = FakeAdapter()
    r2.set_adapter(fake2)
    again = await r2.on_event(UID, ev)
    assert again == first
    assert fake2.sent == []  # nothing re-posted after restart


# --- two-phase flip: recorded → delivered (AC3) ----------------------------

async def test_consumed_event_flips_card_to_delivered(tmp_path):
    r, fake = _renderer(tmp_path)
    await r.on_event(UID, bev("driver.human_input_required", "BLOCKED", seq=3,
                              payload={"decision_id": "D1", "question": "Q",
                                       "options": [{"id": "yes", "label": "Yes"}]}))
    # response adapter records the answer …
    await r.mark_recorded(UID, "D1", "liangchen", "yes")
    assert r.decision_record(UID, "D1")["status"] == "recorded"
    # … then the monitor observes the driver consuming it.
    await r.on_event(UID, bev("driver.human_input_consumed", "ACTIVE", seq=4,
                              payload={"decision_id": "D1", "choice": "yes"}))
    assert r.decision_record(UID, "D1")["status"] == "delivered"
    # The card was edited in place (thread + mirror), not re-posted.
    assert any("Delivered" in _texts(m) for (_, _, m) in fake.edited)


async def test_render_guided_reply_names_open_decision(tmp_path):
    r, fake = _renderer(tmp_path)
    await r.on_event(UID, bev("driver.human_input_required", "BLOCKED", seq=3,
                              payload={"decision_id": "D1", "question": "Pick colour",
                                       "options": [{"id": "red", "label": "Red"}]}))
    fake.sent.clear()
    await r.render_guided_reply(UID, CHAN)
    assert len(fake.sent) == 1
    body = _texts(fake.sent[0][1])
    assert "not" in body.lower() and "forward" in body.lower()


@pytest.mark.parametrize("cb,expected", [
    ("bos|d|builder-os:17|D1|red", ("d", "builder-os:17", "D1", "red")),
    ("bos|o|builder-os:17|D1|inspect", ("o", "builder-os:17", "D1", "inspect")),
    ("prompt_opt:@1:2", None),
    ("bind_new:123", None),
    ("bos|malformed", None),
])
def test_parse_cb(cb, expected):
    assert parse_cb(cb) == expected
