"""Reusable pieces for the tv6q3n integration gate.

Three actors, mirroring a production run with no coder agent and no Discord:

* :class:`ScriptedDriver` — shells the **real** ``builder-os driver …`` verbs
  (through the offline shim) to emit a real ``events.jsonl`` — it stands in for
  the coder agent a live ticket would have.
* the **real ghost chain** — a headless :class:`~gits.core.engine.Engine`
  (mirroring ``tests/test_bos_commands.py``) with its registry, event monitor,
  renderer, response adapter and disposer wired exactly as in production.
* :class:`FakeTransportAdapter` — records every card / mirror / pin / edit and
  lets a test synthesise a button click (the fake Discord transport).

The builder-os CLI runs as a **subprocess** via
:mod:`tests.builder_integration.bos_offline` under the builder-os venv python, so
the state machine, ``events.jsonl``, ``runtime-state``, capability-token hash and
stable exit codes are all real. Only the external providers are offline doubles.

CI pins the builder-os side to a recorded SHA; ``BOS_REPO`` / ``BOS_PYTHON`` point
at that checkout's venv (defaults suit the dev machine).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

REPO_ALIAS = "builder-os"
BOS_REPO = Path(os.environ.get("BOS_REPO", "/Users/sharon/src/builder-os"))
BOS_PYTHON = Path(os.environ.get("BOS_PYTHON", str(BOS_REPO / ".venv" / "bin" / "python")))
SHIM = Path(__file__).resolve().parent / "bos_offline.py"


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=check)


# --------------------------------------------------------------------------- #
# hermetic env (mirrors builder-os tests/conftest.py::bos_env)
# --------------------------------------------------------------------------- #
@dataclass
class BosEnv:
    root: Path
    checkout: Path
    clones: Path
    clone: Path
    issue: int
    capability_token: str = "cap-tok-tv6q3n"

    @property
    def uid(self) -> str:
        return f"{REPO_ALIAS}:{self.issue}"

    @property
    def runtime_dir(self) -> Path:
        return self.checkout / "runtime-state" / "tickets" / REPO_ALIAS / str(self.issue)

    @property
    def event_log(self) -> Path:
        return self.runtime_dir / "events.jsonl"

    @property
    def worktree(self) -> Path:
        return self.checkout / "runtime-state" / "worktrees" / REPO_ALIAS / str(self.issue)

    @property
    def local_config(self) -> Path:
        return self.checkout / "runtime-state" / "local-config.yaml"

    @property
    def capability_hash_file(self) -> Path:
        return self.runtime_dir / "auth" / "capability.sha256"

    @property
    def state_json(self) -> Path:
        return self.runtime_dir / "state.json"

    @property
    def admission_json(self) -> Path:
        return self.runtime_dir / "admission.json"

    def read_state(self) -> dict:
        return json.loads(self.state_json.read_text())

    def read_admission(self) -> dict:
        return json.loads(self.admission_json.read_text())

    def event_types(self) -> list[str]:
        if not self.event_log.exists():
            return []
        lines = self.event_log.read_text().splitlines()
        return [json.loads(line)["type"] for line in lines if line.strip()]

    def clone_log(self) -> str:
        return _git(self.clone, "log", "--oneline", check=False).stdout

    def clone_branches(self) -> str:
        return _git(self.clone, "branch", "--list", check=False).stdout

    def activate(self, monkeypatch, *, issue_json: str | None = None) -> None:
        """Export the builder-os env vars so the engine's own CLI subprocesses
        (respond / dispose / cleanup / status) resolve this hermetic env."""
        monkeypatch.setenv("BOS_CHECKOUT_ROOT", str(self.checkout))
        monkeypatch.setenv("BOS_LOCAL_CONFIG", str(self.local_config))
        if issue_json is not None:
            monkeypatch.setenv("BOS_OFFLINE_ISSUE", issue_json)


def make_bos_env(tmp_path: Path, issue: int = 4) -> BosEnv:
    checkout = tmp_path / "checkout"
    (checkout / "runtime-state").mkdir(parents=True)
    clones = tmp_path / "clones"
    clones.mkdir()
    clone = clones / REPO_ALIAS
    clone.mkdir()
    _git(clone, "init", "-q", "-b", "master")
    _git(clone, "config", "user.email", "t@e.com")
    _git(clone, "config", "user.name", "t")
    (clone / "README.md").write_text("seed\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "seed")
    (checkout / "runtime-state" / "local-config.yaml").write_text(f"clones_root: {clones}\n")
    return BosEnv(root=tmp_path, checkout=checkout, clones=clones, clone=clone, issue=issue)


# --------------------------------------------------------------------------- #
# scripted driver — the real builder-os driver verbs (offline shim subprocess)
# --------------------------------------------------------------------------- #
class ScriptedDriver:
    def __init__(self, env: BosEnv, *, labels=("type:feature",), title="toy feature",
                 body="add a thing"):
        self.env = env
        self._issue_json = json.dumps(
            {"number": env.issue, "title": title, "body": body, "labels": list(labels)})
        self.session_id: str | None = None
        self.epoch: int = 1

    def _run(self, *args: str, need_issue: bool = False):
        e = dict(os.environ)
        e["BOS_CHECKOUT_ROOT"] = str(self.env.checkout)
        e["BOS_LOCAL_CONFIG"] = str(self.env.local_config)
        if need_issue:
            e["BOS_OFFLINE_ISSUE"] = self._issue_json
        p = subprocess.run([str(BOS_PYTHON), str(SHIM), *args],
                           cwd=str(self.env.checkout), env=e, capture_output=True, text=True)
        return p.returncode, p.stdout, p.stderr

    # -- ticket / launch ---------------------------------------------------
    def admit(self, *, requester: str | None = None, remote: bool = False) -> dict:
        args = ["ticket", "admit", "--issue", str(self.env.issue), "--repo", REPO_ALIAS,
                "--capability-token", self.env.capability_token]
        if requester:
            args += ["--requester", requester]
        if remote:
            args += ["--remote"]
        rc, out, err = self._run(*args, need_issue=True)
        assert rc == 0, f"admit failed ({rc}): {err}"
        spec = json.loads(out)
        self.session_id = spec["driver_session_id"]
        self.epoch = int(spec["epoch"])
        return spec

    def resume(self, *, takeover: bool = False, fenced_confirmed: bool = False):
        args = ["ticket", "resume", "--ticket", self.env.uid]
        if takeover:
            args.append("--takeover")
        if fenced_confirmed:
            args.append("--fenced-confirmed")
        return self._run(*args)

    def record_session(self, cli_session_id: str, window: str = "@1"):
        return self._run("ticket", "record-session", "--ticket", self.env.uid,
                         "--cli-session", cli_session_id, "--window", window)

    # -- candidate / review ------------------------------------------------
    def commit_candidate(self, *, with_test: bool = True, extra: dict | None = None) -> str:
        wt = self.env.worktree
        (wt / "feature.txt").write_text("feature\n")
        if with_test:
            (wt / "test_smoke.py").write_text("def test_ok():\n    assert True\n")
        for name, content in (extra or {}).items():
            (wt / name).write_text(content)
        _git(wt, "add", "-A")
        _git(wt, "commit", "-qm", "candidate work")
        return _git(wt, "rev-parse", "HEAD").stdout.strip()

    def submit_candidate(self, sha: str) -> str:
        rc, out, err = self._run("driver", "submit-candidate", "--ticket", self.env.uid,
                                 "--session", self.session_id, "--epoch", str(self.epoch),
                                 "--sha", sha)
        assert rc == 0, f"submit-candidate failed ({rc}): {err}"
        return out.strip()

    def ingest_review(self, verdict: str, candidate_ref: str, *, review_round: int = 1,
                      findings=None):
        vf = self.env.root / f"verdict-{review_round}.json"
        vf.write_text(json.dumps({"verdict": verdict, "candidate_ref": candidate_ref,
                                  "review_round": review_round, "findings": findings or []}))
        rc, out, err = self._run("driver", "ingest-review", "--ticket", self.env.uid,
                                 "--verdict", str(vf))
        assert rc == 0, f"ingest-review failed ({rc}): {err}"
        return out.strip()

    def declare_ready(self):
        return self._run("driver", "declare-ready", "--ticket", self.env.uid,
                         "--session", self.session_id, "--epoch", str(self.epoch))

    # -- decisions ---------------------------------------------------------
    def escalate(self, question: str, why: str, options) -> str:
        args = ["driver", "escalate", "--ticket", self.env.uid, "--session", self.session_id,
                "--epoch", str(self.epoch), "--question", question, "--why-human-owned", why]
        for oid, label in options:
            args += ["--option", f"{oid}={label}"]
        rc, out, err = self._run(*args)
        assert rc == 0, f"escalate failed ({rc}): {err}"
        return out.strip()

    def ask_human(self, question: str, options) -> str:
        args = ["driver", "ask-human", "--ticket", self.env.uid, "--session", self.session_id,
                "--epoch", str(self.epoch), "--question", question]
        for oid, label in options:
            args += ["--option", f"{oid}={label}"]
        rc, out, err = self._run(*args)
        assert rc == 0, f"ask-human failed ({rc}): {err}"
        return out.strip()

    def respond(self, decision_id: str, choice: str, *, actor: str = "liang",
                token: str | None = None):
        """Record a human decision answer via the real ``driver respond`` verb —
        used to model a durable answer that ghost recorded before a crash."""
        args = ["driver", "respond", "--ticket", self.env.uid, "--decision", decision_id,
                "--choice", choice, "--actor", actor, "--source", "ghost-discord"]
        if token:
            args += ["--token", token]
        return self._run(*args)

    def consume_input(self, decision_id: str, *, token: str | None = None):
        args = ["driver", "consume-input", "--ticket", self.env.uid, "--session", self.session_id,
                "--epoch", str(self.epoch), "--decision", decision_id]
        if token:
            args += ["--token", token]
        return self._run(*args)

    def status(self) -> dict:
        rc, out, err = self._run("driver", "status", "--ticket", self.env.uid)
        assert rc == 0, f"status failed ({rc}): {err}"
        return json.loads(out)

    def disposition_decision_id(self) -> str | None:
        return self.env.read_state().get("disposition_decision_id")

    # -- composite ---------------------------------------------------------
    def drive_to_ready(self) -> str:
        """admit → candidate → approved review → declare-ready; return the
        disposition decision id the ready card will carry."""
        self.admit()
        cref = self.submit_candidate(self.commit_candidate())
        self.ingest_review("approved", cref)
        rc, out, err = self.declare_ready()
        assert rc == 0, f"declare-ready failed ({rc}): {err}"
        did = self.disposition_decision_id()
        assert did, "declare-ready did not mint a disposition decision"
        return did


# --------------------------------------------------------------------------- #
# fake Discord transport
# --------------------------------------------------------------------------- #
@dataclass
class Sent:
    channel_id: str
    msg: object
    message_id: str


class FakeTransportAdapter:
    """Records the operator surface; can be told to fail on specific channels."""

    def __init__(self, fail_channels=()):
        self.sent: list[Sent] = []
        self.edited: list[tuple] = []
        self.pinned: list[tuple] = []
        self.deleted: list[tuple] = []
        self.fail_channels = set(fail_channels)
        self._n = 0

    async def send_message(self, channel_id, msg):
        if channel_id in self.fail_channels:
            raise RuntimeError(f"fake transport down for {channel_id}")
        self._n += 1
        mid = f"m{self._n}"
        self.sent.append(Sent(channel_id, msg, mid))
        return mid

    async def edit_message(self, channel_id, message_id, msg):
        self.edited.append((channel_id, message_id, msg))

    async def delete_message(self, channel_id, message_id):
        self.deleted.append((channel_id, message_id))

    async def pin_message(self, channel_id, message_id):
        self.pinned.append((channel_id, message_id))

    async def unpin_message(self, channel_id, message_id):
        pass

    async def create_thread(self, channel_id, title, auto_archive_minutes=10080):
        return f"thread-{channel_id}"

    async def archive_thread(self, thread_id):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass

    def on_message(self, callback):
        pass

    def on_button_click(self, callback):
        pass

    # -- introspection helpers --------------------------------------------
    def cards(self) -> list[Sent]:
        return [s for s in self.sent if getattr(s.msg, "embed", None) is not None]

    def texts(self) -> list[str]:
        return [s.msg.text for s in self.sent if getattr(s.msg, "text", None)]

    def callbacks(self) -> list[str]:
        out = []
        for s in self.sent:
            for row in (getattr(s.msg, "buttons", None) or []):
                for b in row:
                    out.append(b.callback_data)
        return out

    def find_cb(self, *, kind: str, choice: str | None = None) -> str | None:
        """First button callback of ``bos|<kind>|…`` (optionally ending in choice)."""
        prefix = f"bos|{kind}|"
        for cb in self.callbacks():
            if cb.startswith(prefix) and (choice is None or cb.rsplit("|", 1)[-1] == choice):
                return cb
        return None


# --------------------------------------------------------------------------- #
# engine wiring (mirrors Engine.start's builder seam wiring, minus the bg loop)
# --------------------------------------------------------------------------- #
def make_settings(env: BosEnv, tmp_path: Path):
    from gits.config import Settings

    return Settings(
        _env_file=None,
        gits_dir=tmp_path / ".gits",
        gits_discord_token="test-token",
        tmux_session_name="test-gits",
        coding_cli_command="claude",
        allowed_paths=[],
        bind_root=None,
        gits_default_path=None,
        builder_os_root=env.checkout,
        builder_os_cmd=f"{BOS_PYTHON} {SHIM}",
        builder_event_poll_interval=0.02,
        builder_progress_coalesce_seconds=0.0,
    )


def make_engine(settings, adapter):
    """Real headless Engine with tmux mocked, builder seams wired, no bg loop."""
    from gits.core.engine import Engine

    e = Engine(settings)
    e.tmux = MagicMock()
    e.tmux.kill_window = AsyncMock(return_value=True)
    e.tmux.window_exists = AsyncMock(return_value=True)
    e.tmux.send_text = AsyncMock()
    e.set_adapter(adapter)
    e.builder_event_monitor.on_event(e.builder_renderer.on_event)
    e.builder_event_monitor.on_fault(e.builder_renderer.on_fault)
    e.builder_event_monitor.on_global_fault(e._builder_global_fault)
    return e


def write_humans(settings, mapping: dict) -> None:
    f = settings.builder_humans_file
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(mapping))


_USE_ENV_TOKEN = object()  # sentinel: default to the env's real capability token


async def register_ticket(engine, env: BosEnv, driver_session_id: str, *,
                          channel_id: str = "thread-1", assistant_channel_id: str = "assist-1",
                          capability_token=_USE_ENV_TOKEN):
    """Register the ticket in ghost's registry. ``capability_token`` defaults to
    the env's real token; pass a wrong string or ``None`` (missing) to exercise
    the auth-rejection path."""
    token = env.capability_token if capability_token is _USE_ENV_TOKEN else capability_token
    return await engine.builder_registry.register(
        env.uid,
        runtime_dir=str(env.runtime_dir),
        event_log=str(env.event_log),
        channel_id=channel_id,
        driver_session_id=driver_session_id,
        capability_token=token,
        assistant_channel_id=assistant_channel_id,
    )


async def pump(engine, times: int = 1):
    """Deterministically advance the monitor (no background poll loop)."""
    for _ in range(times):
        await engine.builder_event_monitor._poll_once()
