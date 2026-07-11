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

    await engine.handle_bos_resume(THREAD, USER_MAPPED, ticket=None, takeover=True)

    # old window killed FIRST (fence), then resume --takeover --fenced-confirmed
    engine.tmux.kill_window.assert_awaited_once_with("@old")
    call = runner.call_for("ticket", "resume")
    assert "--takeover" in call and "--fenced-confirmed" in call
    # relaunched with a fresh window, binding repointed
    engine.tmux.create_window.assert_awaited_once()
    assert engine.session_mgr.get_binding(THREAD).window_id == "@5"


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
