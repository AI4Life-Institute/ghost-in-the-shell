"""Tests for BuilderLauncher (G5/G6) — LaunchSpec parse, verb args, token handoff.

builder-os is mocked via an injected runner returning ``(rc, stdout, stderr)``;
the LaunchSpec JSON shape (0002 §3.4) and the capability-token handoff (T3
ruling) are the contract. The token-handoff test is a *faithful stub* of
builder-os's documented sink (``admit`` writes ``sha256(token)`` to
``runtime_dir/auth/capability.sha256`` — driver/runtime/admit.py) per T4 case law
(DI/test-only stubs; prod fail-closes on an env leak).
"""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from gits.core.builder_launch import (
    BuilderLauncher,
    BuilderLaunchError,
    LaunchSpec,
)

UID = "builder-os:10"


def _spec_json(**overrides) -> str:
    data = {
        "display_id": "BOS-10",
        "driver_session_id": "drv-01J8Z",
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


class Runner:
    """Verb-dispatching fake runner; records every args list it is called with."""

    def __init__(self, responses=None):
        self.calls: list[list[str]] = []
        self._responses = responses or {}

    async def __call__(self, args):
        self.calls.append(list(args))
        key = (args[0], args[1]) if len(args) >= 2 else (args[0],)
        rc, out, err = self._responses.get(key, (0, "", ""))
        return rc, out, err

    def call_for(self, group, verb):
        for c in self.calls:
            if len(c) >= 2 and c[0] == group and c[1] == verb:
                return c
        return None


# ── LaunchSpec parsing ─────────────────────────────────────────────────────


def test_launchspec_parses_full():
    spec = LaunchSpec.from_json(_spec_json())
    assert spec.ticket_uid == UID
    assert spec.driver_session_id == "drv-01J8Z"
    assert spec.epoch == 1
    assert spec.cli == "claude"
    assert spec.cli_args == ["--permission-mode", "acceptEdits"]
    assert spec.initial_prompt == ".builder-os/BRIEF.md"
    assert spec.display_id == "BOS-10"
    assert spec.replay is False


def test_launchspec_replay_flag():
    spec = LaunchSpec.from_json(_spec_json(replay=True))
    assert spec.replay is True


@pytest.mark.parametrize("field", [
    "driver_session_id", "epoch", "work_dir", "cli", "cli_args",
    "ticket_uid", "event_log", "runtime_dir",
])
def test_launchspec_missing_required_field_fails_closed(field):
    data = json.loads(_spec_json())
    data.pop(field)
    with pytest.raises(BuilderLaunchError):
        LaunchSpec.from_json(json.dumps(data))


def test_launchspec_bad_json_fails_closed():
    with pytest.raises(BuilderLaunchError):
        LaunchSpec.from_json("not json {")


def test_launchspec_non_object_fails_closed():
    with pytest.raises(BuilderLaunchError):
        LaunchSpec.from_json("[1, 2, 3]")


def test_launchspec_cli_args_must_be_list_of_strings():
    with pytest.raises(BuilderLaunchError):
        LaunchSpec.from_json(_spec_json(cli_args="oops"))
    with pytest.raises(BuilderLaunchError):
        LaunchSpec.from_json(_spec_json(cli_args=[1, 2]))


# ── build_command (dumb executor) ───────────────────────────────────────────


def test_build_command_appends_args_and_prompt():
    spec = LaunchSpec.from_json(_spec_json())
    cmd = BuilderLauncher.build_command(spec, "clpy")
    assert cmd == "clpy --permission-mode acceptEdits .builder-os/BRIEF.md"


def test_build_command_omits_absent_prompt():
    spec = LaunchSpec.from_json(_spec_json(initial_prompt=None,
                                           cli_args=["--resume", "cli-9"]))
    cmd = BuilderLauncher.build_command(spec, "clpy")
    assert cmd == "clpy --resume cli-9"


def test_build_command_quotes_args_with_spaces():
    spec = LaunchSpec.from_json(_spec_json(
        cli_args=["--model", "opus"], initial_prompt="a prompt with spaces"))
    cmd = BuilderLauncher.build_command(spec, "clpy")
    assert "'a prompt with spaces'" in cmd


# ── verb arg construction ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admit_args_and_token_passthrough():
    runner = Runner({("ticket", "admit"): (0, _spec_json(), "")})
    bl = BuilderLauncher(builder_os_root=Path("/tmp"), runner=runner)
    spec = await bl.admit(issue=10, repo="builder-os", token="TOK123")
    assert spec.ticket_uid == UID
    call = runner.call_for("ticket", "admit")
    assert "--issue" in call and "10" in call
    assert "--repo" in call and "builder-os" in call
    assert "--capability-token" in call and "TOK123" in call


@pytest.mark.asyncio
async def test_admit_without_repo_omits_flag():
    runner = Runner({("ticket", "admit"): (0, _spec_json(), "")})
    bl = BuilderLauncher(builder_os_root=Path("/tmp"), runner=runner)
    await bl.admit(issue=10, repo=None, token="T")
    assert "--repo" not in runner.call_for("ticket", "admit")


# ── B4: remote requester auth ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admit_passes_requester_and_remote():
    """B4: a requester ⇒ `--requester <hid> --remote` so admission evaluates
    local_operator=False and validates the requester against the pinned
    contract. Old code never forwarded the resolved human id."""
    runner = Runner({("ticket", "admit"): (0, _spec_json(), "")})
    bl = BuilderLauncher(builder_os_root=Path("/tmp"), runner=runner)
    await bl.admit(issue=10, repo="builder-os", token="T", requester="liangchen")
    call = runner.call_for("ticket", "admit")
    assert call[call.index("--requester") + 1] == "liangchen"
    assert "--remote" in call


@pytest.mark.asyncio
async def test_admit_local_when_no_requester():
    runner = Runner({("ticket", "admit"): (0, _spec_json(), "")})
    bl = BuilderLauncher(builder_os_root=Path("/tmp"), runner=runner)
    await bl.admit(issue=10, repo="builder-os", token="T")
    call = runner.call_for("ticket", "admit")
    assert "--requester" not in call and "--remote" not in call


# ── B1: dispose / cleanup verbs ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispose_args_and_fail_closed():
    runner = Runner({("ticket", "dispose"): (0, "disposition DISPOSED", "")})
    bl = BuilderLauncher(builder_os_root=Path("/tmp"), runner=runner)
    await bl.dispose(UID, "D1")
    assert runner.call_for("ticket", "dispose") == [
        "ticket", "dispose", "--ticket", UID, "--decision", "D1"]

    runner = Runner({("ticket", "dispose"): (5, "", "error: illegal transition")})
    bl = BuilderLauncher(builder_os_root=Path("/tmp"), runner=runner)
    with pytest.raises(BuilderLaunchError, match="illegal transition"):
        await bl.dispose(UID, "D1")


@pytest.mark.asyncio
async def test_cleanup_returns_stdout_token():
    """cleanup exits 0 even on a soft `cleanup_failed`, keying the outcome into
    stdout — so the returned token, not the exit code, is the signal."""
    runner = Runner({("ticket", "cleanup"): (0, "terminated\n", "")})
    bl = BuilderLauncher(builder_os_root=Path("/tmp"), runner=runner)
    assert await bl.cleanup(UID) == "terminated"

    runner = Runner({("ticket", "cleanup"): (0, "cleanup_failed\n", "")})
    bl = BuilderLauncher(builder_os_root=Path("/tmp"), runner=runner)
    assert await bl.cleanup(UID) == "cleanup_failed"


# ── minors: shlex-split cmd + fail-closed subprocess timeout ─────────────────


@pytest.mark.asyncio
async def test_default_runner_shlex_splits_quoted_cmd(monkeypatch):
    """`builder_os_cmd` with a quoted path splits correctly (shlex, not str.split)."""
    captured = {}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def fake_exec(*args, **kwargs):
        captured["argv"] = args
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    bl = BuilderLauncher(
        builder_os_cmd='"/opt/my venv/bin/builder-os" run', builder_os_root=Path("/tmp"))
    rc, out, _ = await bl._default_runner(["ticket", "status"])
    assert captured["argv"] == (
        "/opt/my venv/bin/builder-os", "run", "ticket", "status")
    assert rc == 0 and out == "ok"


@pytest.mark.asyncio
async def test_default_runner_timeout_kills_and_fails_closed(monkeypatch):
    """A hung CLI is killed and reported nonzero so the handler fails closed
    instead of hanging forever."""
    state = {"killed": False}

    class HangProc:
        returncode = None

        async def communicate(self):
            if not state["killed"]:
                await asyncio.sleep(10)  # the awaited call hangs
            return b"", b""

        def kill(self):
            state["killed"] = True

    async def fake_exec(*args, **kwargs):
        return HangProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    bl = BuilderLauncher(builder_os_root=Path("/tmp"), timeout=0.05)
    rc, _, err = await bl._default_runner(["ticket", "status"])
    assert rc == 124 and "timed out" in err
    assert state["killed"] is True


@pytest.mark.asyncio
async def test_admit_nonzero_raises_with_stderr():
    runner = Runner({("ticket", "admit"): (2, "", "error: gate refused admission")})
    bl = BuilderLauncher(builder_os_root=Path("/tmp"), runner=runner)
    with pytest.raises(BuilderLaunchError, match="gate refused admission"):
        await bl.admit(issue=10, repo=None, token="T")


@pytest.mark.asyncio
async def test_resume_takeover_flags():
    runner = Runner({("ticket", "resume"): (0, _spec_json(replay=True), "")})
    bl = BuilderLauncher(builder_os_root=Path("/tmp"), runner=runner)
    await bl.resume(UID, takeover=True, fenced_confirmed=True)
    call = runner.call_for("ticket", "resume")
    assert "--ticket" in call and UID in call
    assert "--takeover" in call
    assert "--fenced-confirmed" in call


@pytest.mark.asyncio
async def test_resume_without_takeover():
    runner = Runner({("ticket", "resume"): (0, _spec_json(), "")})
    bl = BuilderLauncher(builder_os_root=Path("/tmp"), runner=runner)
    await bl.resume(UID, takeover=False, fenced_confirmed=True)
    call = runner.call_for("ticket", "resume")
    assert "--takeover" not in call
    assert "--fenced-confirmed" in call


@pytest.mark.asyncio
async def test_record_session_args():
    runner = Runner()
    bl = BuilderLauncher(builder_os_root=Path("/tmp"), runner=runner)
    await bl.record_session(UID, "cli-abc", "@7")
    call = runner.call_for("ticket", "record-session")
    assert call == ["ticket", "record-session", "--ticket", UID,
                    "--cli-session", "cli-abc", "--window", "@7"]


@pytest.mark.asyncio
async def test_status_parses_projection():
    proj = {"ticket_uid": UID, "state": "REVIEWING", "epoch": 2,
            "driver_session_id": "drv-1", "legal_verbs": ["rerun-review"]}
    runner = Runner({("driver", "status"): (0, json.dumps(proj), "")})
    bl = BuilderLauncher(builder_os_root=Path("/tmp"), runner=runner)
    st = await bl.status(UID)
    assert st["state"] == "REVIEWING"
    assert st["epoch"] == 2


@pytest.mark.asyncio
async def test_status_non_json_raises():
    runner = Runner({("driver", "status"): (0, "not json", "")})
    bl = BuilderLauncher(builder_os_root=Path("/tmp"), runner=runner)
    with pytest.raises(BuilderLaunchError):
        await bl.status(UID)


@pytest.mark.asyncio
async def test_rerun_review_carries_session_and_epoch():
    runner = Runner({("driver", "rerun-review"): (0, "ok", "")})
    bl = BuilderLauncher(builder_os_root=Path("/tmp"), runner=runner)
    await bl.rerun_review(UID, "drv-1", 3)
    call = runner.call_for("driver", "rerun-review")
    assert "--session" in call and "drv-1" in call
    assert "--epoch" in call and "3" in call


# ── token minting + configured signal ───────────────────────────────────────


def test_mint_token_is_unique_hex():
    bl = BuilderLauncher(builder_os_root=Path("/tmp"))
    t1, t2 = bl.mint_token(), bl.mint_token()
    assert t1 != t2
    assert len(t1) == 64 and int(t1, 16) >= 0  # 32 bytes hex


def test_configured_tracks_root():
    assert BuilderLauncher(builder_os_root=Path("/tmp")).configured is True
    assert BuilderLauncher(builder_os_root=None).configured is False


# ── token handoff (faithful stub of builder-os's documented sink) ───────────


@pytest.mark.asyncio
async def test_capability_token_handoff_persists_sha256(tmp_path):
    """`admit --capability-token T` ⇒ builder-os writes sha256(T) at
    runtime_dir/auth/capability.sha256 (admit.py:326-330). This faithful stub
    reproduces that exact write; ghost mints T and passes it through, so the
    persisted hash must equal sha256 of the token ghost stores in its registry.
    """
    runtime_dir = tmp_path / "runtime-state" / "tickets" / "builder-os" / "10"

    async def stub_runner(args):
        # Emulate `ticket admit` writing the capability hash, faithfully.
        assert args[:2] == ["ticket", "admit"]
        token = args[args.index("--capability-token") + 1]
        cap = runtime_dir / "auth" / "capability.sha256"
        cap.parent.mkdir(parents=True, exist_ok=True)
        cap.write_text(hashlib.sha256(token.encode("utf-8")).hexdigest() + "\n")
        return 0, _spec_json(runtime_dir=str(runtime_dir)), ""

    bl = BuilderLauncher(builder_os_root=tmp_path, runner=stub_runner)
    token = bl.mint_token()
    await bl.admit(issue=10, repo="builder-os", token=token)

    persisted = (runtime_dir / "auth" / "capability.sha256").read_text().strip()
    assert persisted == hashlib.sha256(token.encode("utf-8")).hexdigest()
