"""Tests for the ``/bos`` command choreography in the Engine (G6, 0002 §5.5/§5.7).

builder-os is mocked via an injected runner (rc, stdout, stderr); tmux + the
Discord adapter are faked. Covers the launch sequence, R11 idempotency, takeover
fencing, session capture, fail-closed-on-every-verb (AC4), and dormancy.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from gits.config import Settings
from gits.core.engine import Engine
from gits.core.tmux import WindowInfo

UID = "builder-os:10"
USER_MAPPED = "555"       # present in builder_humans.json
USER_UNMAPPED = "999"     # absent → fail-closed
ACTOR = "liang"
CHAN = "chan-1"
THREAD = "thread-1"


def _spec(**overrides) -> str:
    data = {
        "display_id": "BOS-10",
        "driver_session_id": "drv-1",
        "epoch": 1,
        "role": "coder",
        "work_dir": "/abs/worktree",
        "cli": "claude",
        "cli_args": ["--permission-mode", "acceptEdits"],
        "initial_prompt": ".builder-os/BRIEF.md",
        "ticket_uid": UID,
        "event_log": "runtime-state/tickets/builder-os/10/events.jsonl",
        "runtime_dir": "runtime-state/tickets/builder-os/10",
    }
    data.update(overrides)
    return json.dumps(data)


_STATUS = {
    "ticket_uid": UID, "state": "REVIEWING", "epoch": 4,
    "driver_session_id": "drv-1", "open_decision_id": None,
    "legal_verbs": ["rerun-review"], "admitted": True,
}


class FakeAdapter:
    def __init__(self):
        self.sent = []      # (channel_id, msg)
        self.threads = []
        self._n = 0

    async def send_message(self, channel_id, msg):
        self._n += 1
        self.sent.append((channel_id, msg))
        return f"m{self._n}"

    async def create_thread(self, channel_id, title, auto_archive_minutes=10080):
        self.threads.append((channel_id, title))
        return THREAD

    async def edit_message(self, channel_id, message_id, msg):
        pass

    async def delete_message(self, channel_id, message_id):
        pass

    async def pin_message(self, channel_id, message_id):
        pass

    async def unpin_message(self, channel_id, message_id):
        pass

    # convenience -----------------------------------------------------------
    def embeds(self):
        return [m.embed for _, m in self.sent if m.embed is not None]

    def texts(self):
        return [m.text for _, m in self.sent if m.text]


class Runner:
    """Verb-dispatching fake runner; records calls. Raises if `forbidden`."""

    def __init__(self, responses=None, forbidden=False):
        self.calls = []
        self._responses = responses or {}
        self._forbidden = forbidden

    async def __call__(self, args):
        if self._forbidden:
            raise AssertionError(f"builder-os must not be invoked (dormant): {args}")
        self.calls.append(list(args))
        key = (args[0], args[1]) if len(args) >= 2 else (args[0],)
        return self._responses.get(key, (0, "", ""))

    def call_for(self, group, verb):
        for c in self.calls:
            if len(c) >= 2 and c[0] == group and c[1] == verb:
                return c
        return None


def _default_responses():
    return {
        ("ticket", "admit"): (0, _spec(), ""),
        ("ticket", "resume"): (0, _spec(replay=True), ""),
        ("ticket", "record-session"): (0, "", ""),
        ("driver", "status"): (0, json.dumps(_STATUS), ""),
        ("driver", "rerun-review"): (0, "ok", ""),
    }


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        gits_dir=tmp_path / ".gits",
        gits_discord_token="test-token",
        tmux_session_name="test-gits",
        coding_cli_command="claude",
        allowed_paths=[],
        bind_root=None,
        gits_default_path=None,
        builder_os_root=tmp_path / "builder-os",
    )


def _mk_engine(settings, *, mapped=True, runner=None):
    e = Engine(settings)
    e.tmux = MagicMock()
    e.tmux.create_window = AsyncMock(
        return_value=WindowInfo(window_id="@5", name="bos", cwd="/abs/worktree"))
    e.tmux.kill_window = AsyncMock(return_value=True)
    e.tmux.window_exists = AsyncMock(return_value=True)
    e.tmux.send_text = AsyncMock()
    e.monitor.start_polling = MagicMock()
    adapter = FakeAdapter()
    e.set_adapter(adapter)
    e._adapter = adapter
    if mapped:
        settings.builder_humans_file.parent.mkdir(parents=True, exist_ok=True)
        settings.builder_humans_file.write_text(json.dumps({USER_MAPPED: ACTOR}))
    if runner is not None:
        e.builder_launcher._runner = runner
    e.builder_launcher._token_factory = lambda: "TOKENFIXED"
    return e, adapter


# ── start choreography ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_full_choreography(settings):
    runner = Runner(_default_responses())
    engine, adapter = _mk_engine(settings, runner=runner)

    await engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo="builder-os")

    # thread created; tmux window launched from the LaunchSpec
    assert adapter.threads and adapter.threads[0][0] == CHAN
    engine.tmux.create_window.assert_awaited_once()
    cw = engine.tmux.create_window.call_args
    assert cw.kwargs["cwd"] == "/abs/worktree"
    assert "--permission-mode acceptEdits" in cw.kwargs["command"]
    assert ".builder-os/BRIEF.md" in cw.kwargs["command"]

    # registry entry: token stored ghost-side, pointers registered
    rec = engine.builder_registry.get(UID)
    assert rec is not None
    assert rec.capability_token == "TOKENFIXED"
    assert rec.driver_session_id == "drv-1"
    assert rec.channel_id == THREAD

    # admit carried the ghost-minted token (handoff)
    admit = runner.call_for("ticket", "admit")
    assert "--capability-token" in admit and "TOKENFIXED" in admit

    # binding marks the thread as this ticket's driver pane
    binding = engine.session_mgr.get_binding(THREAD)
    assert binding is not None and binding.builder_ticket_uid == UID

    # plain ack only — no lifecycle card fabricated synchronously
    assert any("admitted" in t for t in adapter.texts())
    assert adapter.embeds() == []


@pytest.mark.asyncio
async def test_start_arms_session_capture(settings):
    runner = Runner(_default_responses())
    engine, adapter = _mk_engine(settings, runner=runner)
    await engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo="builder-os")

    # jsonl_monitor would stamp this from the SessionStart hook; simulate it
    await engine.session_mgr.update_cli_session_id(THREAD, "cli-xyz")
    await asyncio.sleep(0.7)  # let the capture coroutine observe + record

    rs = runner.call_for("ticket", "record-session")
    assert rs is not None
    assert "--cli-session" in rs and "cli-xyz" in rs
    assert "--window" in rs and "@5" in rs


@pytest.mark.asyncio
async def test_start_idempotent_live_window_is_pointer(settings):
    runner = Runner(_default_responses())
    engine, adapter = _mk_engine(settings, runner=runner)
    # pre-register a live ticket
    await engine.builder_registry.register(
        UID, runtime_dir="/r", event_log="/e", channel_id=THREAD)
    await engine.session_mgr.bind(
        platform="discord", channel_id=THREAD, window_id="@5",
        window_name="bos", work_dir="/w", builder_ticket_uid=UID)
    engine.tmux.window_exists = AsyncMock(return_value=True)

    await engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo="builder-os")

    # no admit, no new window — pure pointer
    assert runner.call_for("ticket", "admit") is None
    engine.tmux.create_window.assert_not_awaited()
    assert any("already running" in t for t in adapter.texts())


@pytest.mark.asyncio
async def test_start_idempotent_dead_window_refuses_toward_resume(settings):
    runner = Runner(_default_responses())
    engine, adapter = _mk_engine(settings, runner=runner)
    await engine.builder_registry.register(
        UID, runtime_dir="/r", event_log="/e", channel_id=THREAD)
    await engine.session_mgr.bind(
        platform="discord", channel_id=THREAD, window_id="@5",
        window_name="bos", work_dir="/w", builder_ticket_uid=UID)
    engine.tmux.window_exists = AsyncMock(return_value=False)

    await engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo="builder-os")

    assert runner.call_for("ticket", "admit") is None
    engine.tmux.create_window.assert_not_awaited()
    descs = [e.description for e in adapter.embeds()]
    assert any("resume" in (d or "") for d in descs)


# ── resume / takeover ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resume_takeover_fences_then_relaunches(settings):
    runner = Runner(_default_responses())
    engine, adapter = _mk_engine(settings, runner=runner)
    await engine.builder_registry.register(
        UID, runtime_dir="/r", event_log="/e", channel_id=THREAD,
        driver_session_id="drv-1")
    await engine.session_mgr.bind(
        platform="discord", channel_id=THREAD, window_id="@old",
        window_name="bos", work_dir="/abs/worktree", builder_ticket_uid=UID)
    # (B5) truthful liveness: the old window is alive until it is killed, then
    # absent — so the post-kill liveness check confirms fencing.
    _dead: set[str] = set()

    async def _kill(win_id):
        _dead.add(win_id)
        return True

    async def _exists(win_id):
        return win_id not in _dead

    engine.tmux.kill_window = AsyncMock(side_effect=_kill)
    engine.tmux.window_exists = AsyncMock(side_effect=_exists)

    await engine.handle_bos_resume(THREAD, USER_MAPPED, ticket=None, takeover=True)

    # old window killed FIRST (fence), then resume --takeover --fenced-confirmed
    engine.tmux.kill_window.assert_any_await("@old")
    call = runner.call_for("ticket", "resume")
    assert "--takeover" in call and "--fenced-confirmed" in call
    # relaunched with a fresh window, binding repointed
    engine.tmux.create_window.assert_awaited_once()
    assert engine.session_mgr.get_binding(THREAD).window_id == "@5"


@pytest.mark.asyncio
async def test_resume_refuses_when_fencing_cannot_be_confirmed(settings):
    """(B5) A kill that leaves the window alive ⇒ ghost refuses the resume rather
    than asserting --fenced-confirmed. Old code killed-and-hoped, then always
    passed --fenced-confirmed; this proves it now fails closed."""
    runner = Runner(_default_responses())
    engine, adapter = _mk_engine(settings, runner=runner)
    await engine.builder_registry.register(
        UID, runtime_dir="/r", event_log="/e", channel_id=THREAD,
        driver_session_id="drv-1")
    await engine.session_mgr.bind(
        platform="discord", channel_id=THREAD, window_id="@old",
        window_name="bos", work_dir="/abs/worktree", builder_ticket_uid=UID)
    # kill "succeeds" but the window stubbornly survives (zombie) — never fenced.
    engine.tmux.kill_window = AsyncMock(return_value=True)
    engine.tmux.window_exists = AsyncMock(return_value=True)

    await engine.handle_bos_resume(THREAD, USER_MAPPED, ticket=None, takeover=False)

    # resume is NOT invoked; a refusal card names the fencing failure.
    assert runner.call_for("ticket", "resume") is None
    engine.tmux.create_window.assert_not_awaited()
    assert any("Fencing failed" in (e.title or "") for e in adapter.embeds())


# ── status / rerun-review / forward ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_renders_projection(settings):
    runner = Runner(_default_responses())
    engine, adapter = _mk_engine(settings, runner=runner)
    await engine.handle_bos_status(CHAN, USER_MAPPED, ticket=UID)
    joined = "\n".join(adapter.texts())
    assert "REVIEWING" in joined and "epoch 4" in joined


@pytest.mark.asyncio
async def test_rerun_review_resolves_epoch_from_status(settings):
    runner = Runner(_default_responses())
    engine, adapter = _mk_engine(settings, runner=runner)
    await engine.handle_bos_rerun_review(CHAN, USER_MAPPED, ticket=UID)
    # epoch/session came from a fresh status read, never cached
    assert runner.call_for("driver", "status") is not None
    rr = runner.call_for("driver", "rerun-review")
    assert "--session" in rr and "drv-1" in rr
    assert "--epoch" in rr and "4" in rr


@pytest.mark.asyncio
async def test_forward_fail_closed_at_command_layer(settings):
    runner = Runner(_default_responses())
    engine, adapter = _mk_engine(settings, runner=runner)
    # bind an open ticket so forced_forward *would* fire for a mapped user
    await engine.session_mgr.bind(
        platform="discord", channel_id=THREAD, window_id="@5",
        window_name="bos", work_dir="/w", builder_ticket_uid=UID)
    await engine.handle_bos_forward(THREAD, USER_UNMAPPED, "do the thing")
    # unmapped ⇒ refused, nothing injected
    engine.tmux.send_text.assert_not_awaited()
    assert any("Unmapped" in (e.title or "") for e in adapter.embeds())


# ── fail-closed on EVERY verb (AC4) ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("call", [
    lambda e: e.handle_bos_start(CHAN, USER_UNMAPPED, 10, "builder-os"),
    lambda e: e.handle_bos_status(CHAN, USER_UNMAPPED, UID),
    lambda e: e.handle_bos_respond(CHAN, USER_UNMAPPED, "opt-a"),
    lambda e: e.handle_bos_resume(CHAN, USER_UNMAPPED, UID, False),
    lambda e: e.handle_bos_forward(CHAN, USER_UNMAPPED, "text"),
    lambda e: e.handle_bos_rerun_review(CHAN, USER_UNMAPPED, UID),
    lambda e: e.handle_bos_rebind_thread(CHAN, USER_UNMAPPED, UID),
])
async def test_unmapped_user_refused_no_side_effect(settings, call):
    runner = Runner(_default_responses())
    engine, adapter = _mk_engine(settings, runner=runner)
    await call(engine)
    # refusal card, and provably zero builder-os invocations / tmux side effects
    assert any("Unmapped" in (e.title or "") for e in adapter.embeds())
    assert runner.calls == []
    engine.tmux.create_window.assert_not_awaited()
    engine.tmux.kill_window.assert_not_awaited()
    assert engine.builder_registry.get(UID) is None


# ── B1: disposition full loop (dispose → cleanup → unregister) ──────────────


@pytest.mark.asyncio
async def test_disposition_full_loop_dispose_cleanup_unregister(settings):
    """A disposition button drives the real terminal loop against the CLI
    contract (faked executor): driver respond → ticket dispose → ticket cleanup
    → registry unregister + pane teardown. Old code injected a consume-input
    nudge (illegal from READY_FOR_HUMAN), so nothing terminal happened."""
    from gits.core.builder_renderer import make_disposition_cb

    responses = _default_responses()
    responses[("driver", "status")] = (
        0, json.dumps({**_STATUS, "state": "READY_FOR_HUMAN"}), "")
    responses[("driver", "respond")] = (0, "", "")
    responses[("ticket", "dispose")] = (0, "driver.disposed DISPOSED", "")
    responses[("ticket", "cleanup")] = (0, "terminated", "")
    runner = Runner(responses)
    engine, adapter = _mk_engine(settings, runner=runner)
    engine.builder_response._runner = runner  # share the fake for driver respond

    await engine.builder_registry.register(
        UID, runtime_dir="/r", event_log="/e", channel_id=THREAD,
        capability_token="cap", driver_session_id="drv-1")
    await engine.session_mgr.bind(
        platform="discord", channel_id=THREAD, window_id="@5",
        window_name="bos", work_dir="/w", builder_ticket_uid=UID)
    engine.builder_renderer._ticket_state(UID)["decisions"]["Dz"] = {
        "status": "open", "kind": "disposition", "channel_id": THREAD,
        "question": "Merge?", "options": [{"id": "merge", "label": "Merge"}]}

    await engine.builder_response.handle_click(
        THREAD, USER_MAPPED, make_disposition_cb(UID, "Dz", "merge"))

    assert runner.call_for("ticket", "dispose") == [
        "ticket", "dispose", "--ticket", UID, "--decision", "Dz"]
    assert runner.call_for("ticket", "cleanup") is not None
    assert engine.builder_registry.get(UID) is None       # unregistered
    engine.tmux.kill_window.assert_any_await("@5")         # pane torn down


def _seed_open_disposition(engine, choice_id="merge", label="Merge"):
    engine.builder_renderer._ticket_state(UID)["decisions"]["Dz"] = {
        "status": "open", "kind": "disposition", "channel_id": THREAD,
        "question": "Disposition?", "options": [{"id": choice_id, "label": label}]}


@pytest.mark.asyncio
async def test_disposition_request_changes_keeps_ticket_and_nudges(settings):
    """CR3 blocker 1: request_changes → CR_REWORK is NOT terminal. Ghost keeps the
    ticket registered + pane alive and nudges the Driver; it must NOT cleanup.
    Old code treated every successful dispose as terminal → cleanup refuses from
    CR_REWORK and the disposition reads as incomplete."""
    from gits.core.builder_renderer import make_disposition_cb

    responses = _default_responses()
    responses[("driver", "status")] = (
        0, json.dumps({**_STATUS, "state": "READY_FOR_HUMAN"}), "")
    responses[("driver", "respond")] = (0, "", "")
    responses[("ticket", "dispose")] = (0, "review.changes_requested CR_REWORK", "")
    runner = Runner(responses)
    engine, adapter = _mk_engine(settings, runner=runner)
    engine.builder_response._runner = runner
    await engine.builder_registry.register(
        UID, runtime_dir="/r", event_log="/e", channel_id=THREAD,
        capability_token="cap", driver_session_id="drv-1")
    await engine.session_mgr.bind(
        platform="discord", channel_id=THREAD, window_id="@5",
        window_name="bos", work_dir="/w", coding_cli="claude", builder_ticket_uid=UID)
    _seed_open_disposition(engine, "request_changes", "Request changes")

    await engine.builder_response.handle_click(
        THREAD, USER_MAPPED, make_disposition_cb(UID, "Dz", "request_changes"))

    assert runner.call_for("ticket", "dispose") is not None
    assert runner.call_for("ticket", "cleanup") is None    # NOT cleaned up
    assert engine.builder_registry.get(UID) is not None    # ticket kept registered
    engine.tmux.kill_window.assert_not_awaited()           # pane kept alive
    engine.tmux.send_text.assert_awaited()                 # Driver nudged to continue
    assert engine.builder_renderer.decision_record(UID, "Dz")["status"] == "changes_requested"


@pytest.mark.asyncio
async def test_duplicate_disposition_resumes_terminal_workflow(settings):
    """CR3 blocker 2: ghost crashed after `driver respond` but before dispose. The
    next click gets RC_DUPLICATE — it must RESUME the state-driven terminal
    workflow (status READY_FOR_HUMAN → dispose → cleanup → teardown), not dead-end
    on 'already decided'. Old code returned DUPLICATE and did nothing terminal."""
    from gits.core.builder_renderer import make_disposition_cb

    responses = _default_responses()
    responses[("driver", "status")] = (
        0, json.dumps({**_STATUS, "state": "READY_FOR_HUMAN"}), "")
    responses[("driver", "respond")] = (9, "", "error: already answered")  # durable already
    responses[("ticket", "dispose")] = (0, "driver.disposed DISPOSED", "")
    responses[("ticket", "cleanup")] = (0, "terminated", "")
    runner = Runner(responses)
    engine, adapter = _mk_engine(settings, runner=runner)
    engine.builder_response._runner = runner
    await engine.builder_registry.register(
        UID, runtime_dir="/r", event_log="/e", channel_id=THREAD,
        capability_token="cap", driver_session_id="drv-1")
    await engine.session_mgr.bind(
        platform="discord", channel_id=THREAD, window_id="@5",
        window_name="bos", work_dir="/w", builder_ticket_uid=UID)
    _seed_open_disposition(engine)

    await engine.builder_response.handle_click(
        THREAD, USER_MAPPED, make_disposition_cb(UID, "Dz", "merge"))

    # the duplicate RESUMED the unfinished terminal workflow.
    assert runner.call_for("ticket", "dispose") is not None
    assert runner.call_for("ticket", "cleanup") is not None
    assert engine.builder_registry.get(UID) is None


@pytest.mark.asyncio
async def test_journal_reconcile_selects_by_capability_hash(tmp_path):
    """CR3 blocker 3 (B2 last window): with builder-os's persisted hash on the
    LaunchSpec, reconcile selects the exact matching token even when NO prior
    attempt recorded a uid binding — closing the post-admit/pre-reconcile crash
    window that uid-earliest selection alone cannot."""
    import hashlib

    from gits.core.builder_start_journal import BuilderStartJournal

    j = BuilderStartJournal(tmp_path / "journal.json")
    # attempt 1 crashed BEFORE stamping the uid: token present, ticket_uid absent.
    await j.get_or_create_token("#10", lambda: "TOK-A")
    # attempt 2 (cross-form) minted a different token.
    assert await j.get_or_create_token("builder-os#10", lambda: "TOK-B") == "TOK-B"

    # builder-os emits sha256(TOK-A) → reconcile picks TOK-A by hash, though the
    # uid-earliest fallback would miss it (no entry was uid-stamped for the uid).
    h = hashlib.sha256(b"TOK-A").hexdigest()
    got = await j.reconcile("builder-os#10", "builder-os:10", "TOK-B", capability_sha256=h)
    assert got == "TOK-A"
    # sanity: without the hash, uid-earliest can't recover it (returns the minted one).
    j2 = BuilderStartJournal(tmp_path / "journal2.json")
    await j2.get_or_create_token("#10", lambda: "TOK-A")
    await j2.get_or_create_token("builder-os#10", lambda: "TOK-B")
    assert await j2.reconcile("builder-os#10", "builder-os:10", "TOK-B") == "TOK-B"


# ── B2: start token durability (crash between admit and register) ────────────


@pytest.mark.asyncio
async def test_start_crash_between_admit_and_register_reuses_token(settings):
    from gits.core.builder_start_journal import request_key

    runner = Runner(_default_responses())
    engine, adapter = _mk_engine(settings, runner=runner)
    # distinct tokens per mint, so reuse-vs-remint is observable.
    tokens = iter(["TOK-A", "TOK-B", "TOK-C"])
    engine.builder_launcher._token_factory = lambda: next(tokens)

    # crash the FIRST registry write (after admit) — the fatal window.
    real_register = engine.builder_registry.register
    n = {"v": 0}

    async def flaky_register(*a, **k):
        n["v"] += 1
        if n["v"] == 1:
            raise RuntimeError("crash after admit, before persist")
        return await real_register(*a, **k)

    engine.builder_registry.register = flaky_register

    with pytest.raises(RuntimeError):
        await engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo="builder-os")
    # journal holds the first token; not cleared (register failed).
    assert engine.builder_start_journal.token_for(request_key("builder-os", 10)) == "TOK-A"

    # retry succeeds and REUSES TOK-A (no re-mint), so the persisted hash matches.
    await engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo="builder-os")
    assert engine.builder_registry.get(UID).capability_token == "TOK-A"
    admits = [c for c in runner.calls if c[:2] == ["ticket", "admit"]]
    assert len(admits) == 2
    assert all(a[a.index("--capability-token") + 1] == "TOK-A" for a in admits)
    # window closed → journal cleared.
    assert engine.builder_start_journal.token_for(request_key("builder-os", 10)) is None


@pytest.mark.asyncio
async def test_cross_form_retry_in_crash_window_keeps_human_authorized(settings, tmp_path):
    """CR-round-1 major: a cross-FORM retry (`issue:10` then `issue:10
    repo:builder-os`) inside the admit→register crash window must NOT strand the
    human. The two forms key the journal differently, so attempt 2 mints a new
    token — but builder-os persists attempt 1's token hash and never overwrites
    it, so registering the new token would be permanent unauthorization. Post-
    admit uid reconciliation reuses attempt 1's token. This asserts against a
    faithful admit stub (writes sha256(token) only-if-absent, like builder-os)."""
    import hashlib

    runtime = tmp_path / "rt"
    cap = runtime / "auth" / "capability.sha256"
    admit_tokens = []

    async def stub(args):
        if args[:2] == ["ticket", "admit"]:
            tok = args[args.index("--capability-token") + 1]
            admit_tokens.append(tok)
            if not cap.exists():  # builder-os writes the hash ONLY if absent
                cap.parent.mkdir(parents=True, exist_ok=True)
                cap.write_text(hashlib.sha256(tok.encode()).hexdigest() + "\n")
            return 0, _spec(runtime_dir=str(runtime)), ""
        return 0, "", ""

    engine, adapter = _mk_engine(settings)
    engine.builder_launcher._runner = stub
    tokens = iter(["TOK-A", "TOK-B", "TOK-C"])
    engine.builder_launcher._token_factory = lambda: next(tokens)

    # attempt 1: repo OMITTED; crash the registry write (admit already done).
    real_register = engine.builder_registry.register
    n = {"v": 0}

    async def flaky(*a, **k):
        n["v"] += 1
        if n["v"] == 1:
            raise RuntimeError("crash after admit, before persist")
        return await real_register(*a, **k)

    engine.builder_registry.register = flaky
    with pytest.raises(RuntimeError):
        await engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo=None)

    # attempt 2: DIFFERENT form (repo now provided) — the reviewer's scenario.
    await engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo="builder-os")

    # builder-os hashed TOK-A (first admit) and never overwrote it …
    assert cap.read_text().strip() == hashlib.sha256(b"TOK-A").hexdigest()
    # … and ghost registered the SAME token, so the human stays authorized.
    assert engine.builder_registry.get(UID).capability_token == "TOK-A"
    # attempt 2 did mint TOK-B (different form) but it was reconciled away.
    assert admit_tokens == ["TOK-A", "TOK-B"]


@pytest.mark.asyncio
async def test_concurrent_cross_form_start_serializes_by_issue(settings, tmp_path):
    """CR-round-2 major: two CONCURRENT cross-form starts for one issue must not
    race into the same permanent-unauthorization. reconcile() assumes insertion
    order == admit order, which only holds if same-ticket starts can't admit
    concurrently. The start lock is now keyed by issue number, so they serialize;
    this proves admit never overlaps (max in-flight == 1) and the registered
    token matches the hash builder-os actually persisted. Old code (lock by
    request form) let both admit concurrently → max == 2 → this fails."""
    import hashlib

    runtime = tmp_path / "rt"
    cap = runtime / "auth" / "capability.sha256"
    inflight = {"cur": 0, "max": 0}

    async def stub(args):
        if args[:2] == ["ticket", "admit"]:
            await asyncio.sleep(0.01)
            tok = args[args.index("--capability-token") + 1]
            if not cap.exists():  # builder-os writes the hash only-if-absent
                cap.parent.mkdir(parents=True, exist_ok=True)
                cap.write_text(hashlib.sha256(tok.encode()).hexdigest() + "\n")
            return 0, _spec(runtime_dir=str(runtime)), ""
        return 0, "", ""

    engine, adapter = _mk_engine(settings)
    engine.builder_launcher._runner = stub
    toks = iter(["TOK-A", "TOK-B", "TOK-C", "TOK-D"])
    engine.builder_launcher._token_factory = lambda: next(toks)

    # Track concurrency of the whole start CRITICAL SECTION (everything under the
    # start lock). Keyed by issue, the two cross-form starts must never overlap;
    # keyed by request form (old code) they hold different locks and both enter.
    orig = engine._handle_bos_start_locked

    async def tracked(*a, **k):
        inflight["cur"] += 1
        inflight["max"] = max(inflight["max"], inflight["cur"])
        try:
            await asyncio.sleep(0)  # let any non-serialized peer enter here
            return await orig(*a, **k)
        finally:
            inflight["cur"] -= 1

    engine._handle_bos_start_locked = tracked

    # concurrent cross-form starts for the SAME issue (omitted vs explicit repo).
    await asyncio.gather(
        engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo=None),
        engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo="builder-os"),
    )

    # serialized by issue → the start critical section never overlapped, so the
    # journal's insertion-order == admit-order assumption holds.
    assert inflight["max"] == 1
    # the registered token matches the hash builder-os actually persisted.
    registered = engine.builder_registry.get(UID).capability_token
    assert cap.read_text().strip() == hashlib.sha256(registered.encode()).hexdigest()


@pytest.mark.asyncio
async def test_definitive_admit_failure_clears_journal(settings):
    """CR-round-1 minor: a definitive admit refusal persists no capability hash,
    so its journalled token is worthless — clear it, no stale-token leak."""
    from gits.core.builder_start_journal import request_key

    responses = _default_responses()
    responses[("ticket", "admit")] = (2, "", "error: gate refused admission")
    runner = Runner(responses)
    engine, adapter = _mk_engine(settings, runner=runner)
    await engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo="builder-os")
    assert any("failed" in (e.title or "").lower() for e in adapter.embeds())
    assert engine.builder_start_journal.token_for(request_key("builder-os", 10)) is None


@pytest.mark.asyncio
async def test_start_locks_reaped_after_completion(settings):
    """CR-round-1 minor: the per-request start-lock map is bounded (reaped)."""
    runner = Runner(_default_responses())
    engine, adapter = _mk_engine(settings, runner=runner)
    await engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo="builder-os")
    assert engine._bos_start_locks == {}


# ── B3: replay binds (no double-launch) + start race ─────────────────────────


@pytest.mark.asyncio
async def test_start_replay_binds_without_launch(settings):
    responses = _default_responses()
    responses[("ticket", "admit")] = (0, _spec(replay=True), "")
    runner = Runner(responses)
    engine, adapter = _mk_engine(settings, runner=runner)

    await engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo="builder-os")

    engine.tmux.create_window.assert_not_awaited()        # no second CLI
    rec = engine.builder_registry.get(UID)                # pointers registered
    assert rec is not None and rec.capability_token == "TOKENFIXED"


@pytest.mark.asyncio
async def test_concurrent_start_same_request_admits_once(settings):
    runner = Runner(_default_responses())
    engine, adapter = _mk_engine(settings, runner=runner)
    await asyncio.gather(
        engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo="builder-os"),
        engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo="builder-os"),
    )
    admits = [c for c in runner.calls if c[:2] == ["ticket", "admit"]]
    assert len(admits) == 1                                # start lock + R11
    engine.tmux.create_window.assert_awaited_once()


# ── B4: /bos start admits remote with the resolved requester ─────────────────


@pytest.mark.asyncio
async def test_start_admits_remote_with_requester(settings):
    runner = Runner(_default_responses())
    engine, adapter = _mk_engine(settings, runner=runner)
    await engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo="builder-os")
    admit = runner.call_for("ticket", "admit")
    assert admit[admit.index("--requester") + 1] == ACTOR
    assert "--remote" in admit


# ── B6: resume is a dumb executor (--resume, not a fresh brief) ──────────────


@pytest.mark.asyncio
async def test_resume_launches_with_resume_arg_not_fresh_brief(settings):
    responses = _default_responses()
    # builder-os emits the executable profile: --resume <sid>, initial_prompt null.
    responses[("ticket", "resume")] = (0, _spec(
        cli_args=["--permission-mode", "acceptEdits", "--resume", "cli-777"],
        initial_prompt=None), "")
    runner = Runner(responses)
    engine, adapter = _mk_engine(settings, runner=runner)
    await engine.builder_registry.register(
        UID, runtime_dir="/r", event_log="/e", channel_id=THREAD,
        driver_session_id="drv-1")
    await engine.session_mgr.bind(
        platform="discord", channel_id=THREAD, window_id="@old",
        window_name="bos", work_dir="/abs/worktree", builder_ticket_uid=UID)
    dead: set[str] = set()
    engine.tmux.kill_window = AsyncMock(side_effect=lambda w: dead.add(w) or True)
    engine.tmux.window_exists = AsyncMock(side_effect=lambda w: w not in dead)

    await engine.handle_bos_resume(THREAD, USER_MAPPED, ticket=None, takeover=False)

    cmd = engine.tmux.create_window.call_args.kwargs["command"]
    assert "--resume cli-777" in cmd            # transcript carried
    assert "BRIEF.md" not in cmd                # dumb executor: no fresh brief


# ── minor: /bos respond reports the real outcome ─────────────────────────────


def test_bos_respond_reply_is_truthful():
    from gits.core.builder_response import RespondOutcome
    reply = Engine._bos_respond_reply
    assert "already decided" in reply("D1", RespondOutcome.DUPLICATE)
    assert "unauthorized" in reply("D1", RespondOutcome.UNAUTHORIZED).lower()
    assert "Recorded" in reply("D1", RespondOutcome.RECORDED)
    assert "terminated" in reply("D1", RespondOutcome.DISPOSED)
    # none of the failure states should read as a bare success.
    for oc in (RespondOutcome.DUPLICATE, RespondOutcome.UNAUTHORIZED,
               RespondOutcome.FAILED, RespondOutcome.UNMAPPED):
        assert "Submitted" not in reply("D1", oc)


# ── dormancy (no config invoked → inert, no subprocess, byte-identical) ──────


def test_bos_group_registers_unconditionally():
    """The /bos group + all R11 verbs register at setup (visible surface).

    Dormancy = fail-closed verbs, not a hidden group (PM ruling #4). This also
    proves discord.py accepts the hyphenated ``rerun-review`` / ``rebind-thread``
    subcommand names at decoration time.
    """
    from gits.adapters.discord.bot import DiscordAdapter

    class FakeTree:
        def __init__(self):
            self.added = []

        def add_command(self, cmd):
            self.added.append(cmd)

    a = DiscordAdapter.__new__(DiscordAdapter)
    a._engine = None
    a.allowed_users = set()
    a.allowed_guilds = set()
    tree = FakeTree()
    a._setup_bos_commands(tree)
    grp = tree.added[0]
    assert grp.name == "bos"
    assert sorted(c.name for c in grp.commands) == [
        "forward", "rebind-thread", "rerun-review", "respond",
        "resume", "start", "status",
    ]


@pytest.mark.asyncio
async def test_dormant_without_config_is_inert(tmp_path):
    # No builder_os_root, no builder_humans.json.
    settings = Settings(
        _env_file=None, gits_dir=tmp_path / ".gits",
        gits_discord_token="t", tmux_session_name="t",
        coding_cli_command="claude", allowed_paths=[],
        bind_root=None, gits_default_path=None,
    )
    forbidden = Runner(forbidden=True)
    engine, adapter = _mk_engine(settings, mapped=False, runner=forbidden)

    # registry file never created just by constructing the engine (dormant)
    assert engine.builder_registry.exists() is False
    assert engine.builder_launcher.configured is False

    # even a would-be verb never spawns a subprocess or writes state
    await engine.handle_bos_start(CHAN, USER_MAPPED, issue=10, repo="builder-os")
    assert forbidden.calls == []
    engine.tmux.create_window.assert_not_awaited()
    assert engine.builder_registry.exists() is False
