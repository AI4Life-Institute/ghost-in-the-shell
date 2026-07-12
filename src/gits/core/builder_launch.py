"""BuilderLauncher (G5/G6) — the ``/bos`` launch + lifecycle orchestrator (0002 §5.7).

This is the ghost seam onto the builder-os CLI for the *entry points* that
create, resume, and steer builder sessions. It is deliberately **side-effect
free with respect to tmux and Discord**: it only

1. mints the per-ticket capability token (ghost-side authority, §11.5),
2. shells the builder-os CLI verbs (``ticket admit`` / ``ticket resume`` /
   ``ticket record-session`` / ``driver status`` / ``driver rerun-review``), and
3. parses + validates the **LaunchSpec** (§3.4) that ``admit``/``resume`` emit.

The tmux window, Discord thread, registry write, and binding all live in the
Engine (:mod:`gits.core.engine`) so they stay testable behind mocks. This split
mirrors T7's :class:`~gits.core.builder_response.BuilderResponseAdapter`, which
owns the ``driver respond`` seam the same way.

**Ghost never writes builder-os ``runtime-state/`` (§5.5).** Every lifecycle
mutation goes through a CLI verb here; the ghost-owned registry
(``builder_tickets.json``) is the only thing ghost writes. In particular ghost
**never caches builder-os lifecycle state** — the epoch that epoch-fenced verbs
(``rerun-review``) require is resolved fresh via ``driver status`` at invocation
time (PM ruling, task x3vqt8 #1), never mirrored into the registry.

The default runner shells ``builder_os_cmd`` (split on whitespace, ``cwd`` =
``builder_os_root``); a deploy-time concern — override for a venv/wrapper. Tests
inject a runner and never touch a subprocess.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# (returncode, stdout, stderr)
Runner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]

# Fail-closed subprocess timeout (minor: a hung CLI/Eva/fs op must never hold a
# Discord handler forever). Exceeded ⇒ the child is killed and the runner
# reports a nonzero rc, so ``_run_ok`` raises like any other failure.
_CLI_TIMEOUT_S = 90.0
# rc surfaced when a builder-os subprocess is killed for exceeding the timeout
# (mirrors the shell/coreutils convention for a timed-out command).
_RC_TIMEOUT = 124

# Fields a LaunchSpec must carry for ghost to launch from it (§3.4). The full
# execution profile (cli/cli_args/model) is resolved builder-os-side and carried
# in the spec — ghost is a dumb executor of it, never a second opinion (B4).
_REQUIRED_SPEC_FIELDS = (
    "driver_session_id",
    "epoch",
    "work_dir",
    "cli",
    "cli_args",
    "ticket_uid",
    "event_log",
    "runtime_dir",
)


class BuilderLaunchError(Exception):
    """A builder-os verb failed or emitted output ghost could not use.

    Carries the first line of the CLI's stderr where available so the command
    layer can surface it on a refusal card without leaking a stack trace.
    """


@dataclass(frozen=True)
class LaunchSpec:
    """The Ghost seam (§3.4) — a pure function of builder-os durable records.

    ``cli_args`` already contains ``--model``/``--permission-mode`` (and, on a
    resume with a surviving transcript, ``--resume <cli_session_id>``); ghost
    concatenates them verbatim. ``initial_prompt`` is a **worktree-relative**
    ref (e.g. ``.builder-os/BRIEF.md``); ``work_dir`` is absolute (resolved at
    emit time, M2). ``replay`` means bind to the existing session — do NOT launch
    a second CLI (takeover is ``ticket resume --takeover``).
    """

    driver_session_id: str
    epoch: int
    work_dir: str
    cli: str
    cli_args: list[str]
    ticket_uid: str
    event_log: str
    runtime_dir: str
    role: str | None = None
    initial_prompt: str | None = None
    model: str | None = None
    display_id: str | None = None
    replay: bool = False

    @classmethod
    def from_json(cls, text: str) -> LaunchSpec:
        """Parse + validate the JSON ``admit``/``resume`` printed to stdout.

        Fail-closed: malformed JSON, a non-object, a missing required field, or a
        ``cli_args`` that is not a list of strings all raise
        :class:`BuilderLaunchError` — ghost never launches from a spec it cannot
        fully trust.
        """
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BuilderLaunchError(f"LaunchSpec is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise BuilderLaunchError("LaunchSpec is not a JSON object")
        missing = [f for f in _REQUIRED_SPEC_FIELDS if f not in data]
        if missing:
            raise BuilderLaunchError(
                f"LaunchSpec missing required field(s): {', '.join(missing)}"
            )
        cli_args = data["cli_args"]
        if not isinstance(cli_args, list) or not all(isinstance(a, str) for a in cli_args):
            raise BuilderLaunchError("LaunchSpec cli_args must be a list of strings")
        return cls(
            driver_session_id=str(data["driver_session_id"]),
            epoch=int(data["epoch"]),
            work_dir=str(data["work_dir"]),
            cli=str(data["cli"]),
            cli_args=list(cli_args),
            ticket_uid=str(data["ticket_uid"]),
            event_log=str(data["event_log"]),
            runtime_dir=str(data["runtime_dir"]),
            role=data.get("role"),
            initial_prompt=data.get("initial_prompt"),
            model=data.get("model"),
            display_id=data.get("display_id"),
            replay=bool(data.get("replay", False)),
        )


class BuilderLauncher:
    """Shells the builder-os CLI for the ``/bos`` entry points (0002 §5.7)."""

    def __init__(
        self,
        *,
        builder_os_cmd: str = "builder-os",
        builder_os_root: Path | None = None,
        runner: Runner | None = None,
        token_factory: Callable[[], str] | None = None,
        timeout: float = _CLI_TIMEOUT_S,
    ):
        self._cmd = builder_os_cmd
        self._root = builder_os_root
        self._timeout = timeout
        self._runner = runner or self._default_runner
        # 32 bytes of urandom → 64 hex chars. Ghost-side authority; builder-os
        # only ever sees the SHA-256 (persisted at admit, §11.5).
        self._token_factory = token_factory or (lambda: secrets.token_hex(32))

    @property
    def configured(self) -> bool:
        """True once a ``builder_os_root`` is set — the activation signal.

        With no root, ghost cannot resolve builder-os's repo-relative paths and
        the CLI is almost certainly not reachable, so every ``/bos`` verb refuses
        cleanly *after* the fail-closed actor gate rather than spawning a doomed
        subprocess.
        """
        return self._root is not None

    def mint_token(self) -> str:
        """Mint a fresh per-ticket capability token (ghost-side authority)."""
        return self._token_factory()

    # -- ticket verbs -------------------------------------------------------

    async def admit(
        self, issue: int, repo: str | None, token: str, *, requester: str | None = None,
    ) -> LaunchSpec:
        """``ticket admit`` → LaunchSpec. Supplies the ghost-minted token (T3).

        builder-os persists ``sha256(token)`` at ``runtime_dir/auth/
        capability.sha256`` — the token itself is never written into
        ``runtime-state/``; it lives only in ghost's registry.

        **B4 — remote requester auth.** When *requester* (the resolved
        ``human_builder_id``) is supplied, ghost admits on that human's behalf
        over the remote (Discord) seam: it passes ``--requester <hid> --remote``
        so admission evaluates ``local_operator=False`` and validates the
        requester against the pinned contract. A ``/bos start`` always has a
        fail-closed-resolved actor, so it always admits remote; the argument is
        optional only so a purely local operator invocation stays possible.
        """
        args = ["ticket", "admit", "--issue", str(issue)]
        if repo:
            args += ["--repo", repo]
        args += ["--capability-token", token]
        if requester:
            args += ["--requester", requester, "--remote"]
        out = await self._run_ok(args)
        return LaunchSpec.from_json(out)

    async def dispose(self, uid: str, decision_id: str) -> str:
        """``ticket dispose`` — execute a recorded disposition decision (§6.3, B1).

        The completion choice was already recorded (``driver respond``); this
        drives the disposition it selected (merge / close / …). Fail-closed:
        a nonzero exit (illegal transition, guard refusal) raises
        :class:`BuilderLaunchError`, so ghost never advances to ``cleanup`` on a
        dispose that did not actually happen.
        """
        return await self._run_ok(
            ["ticket", "dispose", "--ticket", uid, "--decision", decision_id])

    async def cleanup(self, uid: str) -> str:
        """``ticket cleanup`` — tear down a disposed ticket (idempotent, B1).

        Returns the CLI's stdout token. **Contract nuance:** the current
        builder-os ``cleanup`` exits 0 even on a soft ``cleanup_failed`` (it
        prints the outcome to stdout rather than keying it into the exit code),
        so the caller MUST inspect the returned token — ``"terminated"`` means
        torn down, anything else means cleanup did not complete and the ticket
        must stay registered. A hard failure (ticket not found, etc.) still
        raises via the nonzero-exit path.
        """
        return (await self._run_ok(["ticket", "cleanup", "--ticket", uid])).strip()

    async def resume(
        self, uid: str, *, takeover: bool = False, fenced_confirmed: bool = True
    ) -> LaunchSpec:
        """``ticket resume`` → LaunchSpec (with ``--resume`` iff transcript lives).

        builder-os refuses unless ``fenced_confirmed`` — the caller (Engine) must
        have positively confirmed the prior window is dead (killed it) first.
        ``takeover`` bumps the epoch so a surviving zombie CLI is fenced out (D5).
        """
        args = ["ticket", "resume", "--ticket", uid]
        if takeover:
            args.append("--takeover")
        if fenced_confirmed:
            args.append("--fenced-confirmed")
        out = await self._run_ok(args)
        return LaunchSpec.from_json(out)

    async def record_session(self, uid: str, cli_session_id: str, window: str) -> None:
        """``ticket record-session`` — append the driver:cli session mapping (G5).

        The only session-capture write, and it stays inside the harness (ghost
        never writes ``runtime-state/``). 1 ``driver_session_id`` : N
        ``cli_session_id`` (F5) — re-invoked on every launch/resume.
        """
        await self._run_ok([
            "ticket", "record-session",
            "--ticket", uid,
            "--cli-session", cli_session_id,
            "--window", window,
        ])

    # -- driver verbs -------------------------------------------------------

    async def status(self, uid: str) -> dict:
        """``driver status`` → the read-only projection (state + legal verbs).

        This is how ``/bos status`` stays honest (reads the projection, never
        guesses) and how epoch-fenced verbs resolve ``epoch``/``driver_session_id``
        fresh at invocation time (never cached ghost-side).
        """
        out = await self._run_ok(["driver", "status", "--ticket", uid])
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise BuilderLaunchError(f"driver status emitted non-JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise BuilderLaunchError("driver status did not emit a JSON object")
        return data

    async def rerun_review(self, uid: str, session: str, epoch: int) -> str:
        """``driver rerun-review`` — re-review the same candidate, new round.

        Epoch-fenced (a model verb): ``session``/``epoch`` come from a just-taken
        :meth:`status` read, never a cached value (PM ruling #1).
        """
        return await self._run_ok([
            "driver", "rerun-review",
            "--ticket", uid,
            "--session", session,
            "--epoch", str(epoch),
        ])

    # -- launch-command assembly -------------------------------------------

    @staticmethod
    def build_command(spec: LaunchSpec, base_cmd: str) -> str:
        """Assemble the shell command from *base_cmd* + the spec (dumb executor).

        *base_cmd* is the ghost-resolved launch binary for ``spec.cli`` (e.g.
        ``clpy``), possibly multi-token, so it is not quoted; every ``cli_arg``
        and the ``initial_prompt`` are shell-quoted individually. ``initial_prompt``
        is appended only when present (a ``--resume`` LaunchSpec may omit it).
        """
        import shlex

        parts = [base_cmd]
        parts += [shlex.quote(a) for a in spec.cli_args]
        if spec.initial_prompt:
            parts.append(shlex.quote(spec.initial_prompt))
        return " ".join(parts)

    # -- runner -------------------------------------------------------------

    async def _run_ok(self, args: list[str]) -> str:
        """Run a verb, raising :class:`BuilderLaunchError` on a nonzero exit."""
        rc, out, err = await self._runner(args)
        if rc != 0:
            raise BuilderLaunchError(_first_line(err) or f"builder-os exited {rc}")
        return out

    async def _default_runner(self, args: list[str]) -> tuple[int, str, str]:
        """Invoke the builder-os CLI as a subprocess (deploy-time ``builder_os_cmd``).

        ``builder_os_cmd`` is split with :func:`shlex.split` so a deploy-time
        wrapper with quoted paths (e.g. ``"/opt/my venv/bin/builder-os"``)
        survives. The wait is bounded by ``self._timeout``: a hung CLI is killed
        and reported as a nonzero (``124``) rc so ``_run_ok`` fails closed rather
        than pinning a Discord handler forever.
        """
        cmd = shlex.split(self._cmd) + args
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._root) if self._root else None,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), self._timeout)
        except TimeoutError:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.communicate()  # reap the killed child
            verb = " ".join(args[:2])
            logger.warning(
                "builder-os %s timed out after %.0fs — killed", verb, self._timeout)
            return _RC_TIMEOUT, "", f"builder-os {verb} timed out after {self._timeout:.0f}s"
        return (
            proc.returncode or 0,
            out_b.decode("utf-8", "replace"),
            err_b.decode("utf-8", "replace"),
        )


def _first_line(text: str) -> str:
    """First non-empty line, with a leading ``error: `` prefix stripped."""
    for line in (text or "").splitlines():
        s = line.strip()
        if s:
            return s[len("error: "):] if s.startswith("error: ") else s
    return ""
