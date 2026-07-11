"""Tests for BuilderResponseAdapter (G4) — actor resolution, respond, rendering.

builder-os is mocked via an injected runner returning (rc, stdout, stderr); the
stable exit codes (0 ok / 8 unauthorized / 9 duplicate) are the contract.
"""

import json

from gits.core.builder_humans import BuilderHumans
from gits.core.builder_registry import BuilderRegistry
from gits.core.builder_renderer import (
    BuilderRenderer,
    make_decision_cb,
    make_observation_cb,
)
from gits.core.builder_response import BuilderResponseAdapter

UID = "builder-os:17"
CHAN = "1000"
ASSIST = "2000"


class FakeAdapter:
    def __init__(self):
        self.sent = []
        self.edited = []
        self.pinned = []
        self._n = 0

    async def send_message(self, channel_id, msg):
        self._n += 1
        mid = f"m{self._n}"
        self.sent.append((channel_id, msg, mid))
        return mid

    async def edit_message(self, channel_id, message_id, msg):
        self.edited.append((channel_id, message_id, msg))

    async def delete_message(self, channel_id, message_id):
        pass

    async def pin_message(self, channel_id, message_id):
        pass

    async def unpin_message(self, channel_id, message_id):
        pass


class FakeRunner:
    def __init__(self, rc=0, out="input-123", err=""):
        self.rc, self.out, self.err = rc, out, err
        self.calls = []

    async def __call__(self, args):
        self.calls.append(args)
        return self.rc, self.out, self.err


def _text(msg):
    parts = [msg.text or ""]
    if msg.embed:
        parts += [msg.embed.title or "", msg.embed.description or ""]
    return "\n".join(parts)


def _setup(tmp_path, *, mapping=None, rc=0, out="input-123", err=""):
    reg_file = tmp_path / "builder_tickets.json"
    reg_file.write_text(json.dumps({UID: {
        "runtime_dir": "/rt", "event_log": "/rt/events.jsonl",
        "channel_id": CHAN, "assistant_channel_id": ASSIST,
        "driver_session_id": "drv-1", "capability_token": "cap-secret",
    }}))
    reg = BuilderRegistry(registry_file=reg_file)

    humans_file = tmp_path / "builder_humans.json"
    humans_file.write_text(json.dumps(mapping or {}))
    humans = BuilderHumans(humans_file)

    renderer = BuilderRenderer(reg, state_file=tmp_path / "renderer.json",
                               coalesce_seconds=60.0, clock=lambda: 0.0)
    fake = FakeAdapter()
    renderer.set_adapter(fake)

    runner = FakeRunner(rc=rc, out=out, err=err)
    nudges = []

    async def nudge(uid, line):
        nudges.append((uid, line))

    adapter = BuilderResponseAdapter(
        humans, reg, renderer,
        builder_os_cmd="builder-os", forced_forward_log=tmp_path / "ff.jsonl",
        runner=runner, nudge=nudge,
    )
    adapter.set_adapter(fake)
    return adapter, renderer, fake, runner, nudges


def _seed_decision(renderer, decision_id="D1", *, kind="human_input", resume_token=None):
    renderer._ticket_state(UID)["decisions"][decision_id] = {
        "thread_msg_id": "mth", "mirror_msg_id": "mmir",
        "channel_id": CHAN, "assistant_channel_id": ASSIST,
        "status": "open", "kind": kind, "question": "Q",
        "options": [{"id": "red", "label": "Red"}], "resume_token": resume_token,
    }


# --- AC3: fail-closed actor resolution -------------------------------------

async def test_unmapped_user_refused_no_input_written(tmp_path):
    adapter, renderer, fake, runner, nudges = _setup(tmp_path, mapping={})  # empty map
    _seed_decision(renderer)
    handled = await adapter.handle_click(CHAN, "unknown-user", make_decision_cb(UID, "D1", "red"))
    assert handled is True
    # Refusal card shown …
    assert any("Unmapped identity" in _text(m) for (_, m, _) in fake.sent)
    # … respond never invoked (no input written) …
    assert runner.calls == []
    # … and the decision is untouched.
    assert renderer.decision_record(UID, "D1")["status"] == "open"


async def test_mapped_user_records_and_flips_to_recorded(tmp_path):
    adapter, renderer, fake, runner, nudges = _setup(
        tmp_path, mapping={"u1": "liangchen"}, rc=0)
    _seed_decision(renderer, resume_token=None)
    await adapter.handle_click(CHAN, "u1", make_decision_cb(UID, "D1", "red"))

    # respond invoked with the resolved actor + capability token pass-through.
    assert len(runner.calls) == 1
    args = runner.calls[0]
    assert args[:2] == ["driver", "respond"]
    assert "--ticket" in args and args[args.index("--ticket") + 1] == UID
    assert args[args.index("--actor") + 1] == "liangchen"
    assert args[args.index("--choice") + 1] == "red"
    assert args[args.index("--token") + 1] == "cap-secret"
    assert args[args.index("--source") + 1] == "ghost-discord"

    # Card flips open → recorded; nudge injected.
    assert renderer.decision_record(UID, "D1")["status"] == "recorded"
    assert any("Answer recorded" in _text(m) for (_, _, m) in fake.edited)
    assert nudges and "consume-input --decision D1" in nudges[0][1]


async def test_escalation_nudge_carries_resume_token(tmp_path):
    adapter, renderer, fake, runner, nudges = _setup(tmp_path, mapping={"u1": "liangchen"})
    _seed_decision(renderer, kind="escalation", resume_token="RT-9")
    await adapter.handle_click(CHAN, "u1", make_decision_cb(UID, "D1", "red"))
    assert nudges and "--token RT-9" in nudges[0][1]


# --- AC4: duplicate --------------------------------------------------------

async def test_second_responder_sees_already_decided(tmp_path):
    err = ("error: decision D1 already answered by alice at 2026-07-11T00:00:00Z "
           "— first-write-wins (recorded as rejected_duplicate).")
    adapter, renderer, fake, runner, nudges = _setup(
        tmp_path, mapping={"u2": "bob"}, rc=9, err=err)
    _seed_decision(renderer)
    await adapter.handle_click(CHAN, "u2", make_decision_cb(UID, "D1", "red"))
    # The duplicate card names who actually decided and when (R8).
    assert any("already answered by alice" in _text(m) for (_, _, m) in fake.edited)


# --- unauthorized (tamper) -------------------------------------------------

async def test_unauthorized_renders_tamper_card(tmp_path):
    adapter, renderer, fake, runner, nudges = _setup(
        tmp_path, mapping={"u1": "liangchen"}, rc=8, err="error: invalid capability token")
    _seed_decision(renderer)
    await adapter.handle_click(CHAN, "u1", make_decision_cb(UID, "D1", "red"))
    assert any("unauthorized" in _text(m).lower() for (_, m, _) in fake.sent)
    assert renderer.decision_record(UID, "D1")["status"] == "open"  # not recorded


# --- observations are non-consuming (§6.2/D4) ------------------------------

async def test_observation_click_is_non_consuming(tmp_path):
    adapter, renderer, fake, runner, nudges = _setup(tmp_path, mapping={"u1": "liangchen"})
    renderer._ticket_state(UID)["decisions"]["D-disp"] = {
        "status": "open", "kind": "disposition", "candidate_ref": "cand-1",
        "summary_ref": "evidence/completion.md", "channel_id": CHAN,
    }
    await adapter.handle_click(CHAN, "u1", make_observation_cb(UID, "D-disp", "inspect"))
    # No respond call; the decision record is untouched.
    assert runner.calls == []
    assert renderer.decision_record(UID, "D-disp")["status"] == "open"
    # The referenced material is re-surfaced.
    assert any("cand-1" in _text(m) for (_, m, _) in fake.sent)


# --- non-builder callbacks are ignored -------------------------------------

async def test_non_builder_callback_not_owned(tmp_path):
    adapter, *_ = _setup(tmp_path, mapping={"u1": "liangchen"})
    assert adapter.owns("prompt_opt:@1:2") is False
    assert await adapter.handle_click(CHAN, "u1", "prompt_opt:@1:2") is False


# --- forced-forward audit (§5.3) -------------------------------------------

async def test_forced_forward_writes_ghost_side_audit(tmp_path):
    adapter, renderer, fake, runner, nudges = _setup(tmp_path, mapping={"u1": "liangchen"})
    _seed_decision(renderer, "D1")
    actor = await adapter.record_forced_forward(UID, "u1", "just do it")
    assert actor == "liangchen"
    log = tmp_path / "ff.jsonl"
    assert log.exists()
    rec = json.loads(log.read_text().strip())
    assert rec["source"] == "forced-forward"
    assert rec["ticket_uid"] == UID
    assert rec["actor"] == "liangchen"
    assert rec["open_decision_id"] == "D1"
    assert rec["text"] == "just do it"


async def test_forced_forward_unmapped_still_audited(tmp_path):
    adapter, renderer, fake, runner, nudges = _setup(tmp_path, mapping={})
    actor = await adapter.record_forced_forward(UID, "u9", "hi")
    # forward is an operator escape hatch, not a decision — audited, not refused.
    assert actor == "discord:u9"
