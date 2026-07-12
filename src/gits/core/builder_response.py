"""BuilderResponseAdapter (G4) — the authenticated human-input path (0002 §5.6).

A button click (or, in T8, ``/bos respond``) lands here. The adapter:

1. **Resolves the actor, fail-closed** — the Discord user id is mapped to a
   human-builder id via :class:`~gits.core.builder_humans.BuilderHumans`; an
   unmapped id is **refused** with an "unmapped identity" card and **no input is
   written** (§11.4; never an OS-user fallback).
2. **Passes the capability token through** — the per-ticket token minted at
   registration (held in ``builder_tickets.json``) is handed to
   ``builder-os driver respond --token``; ghost never validates it itself
   (builder-os does, §4.5).
3. **Renders the two-phase result** — on success the card flips to "recorded,
   awaiting driver consume" (the renderer flips it to "delivered" when the
   monitor later sees ``driver.human_input_consumed``); a duplicate is rendered
   "already decided by X at T" (R8/AC4); an unauthorized/tamper attempt is
   rendered as such.
4. **Injects the resume nudge** into the driver pane on a recorded answer (§7.4)
   — a hint, not state.

Observation clicks (``inspect`` / ``open_evidence`` on the completion card) are
**non-consuming**: they re-surface the referenced material and never call
``respond`` — the decision record is untouched (§6.2, D4).

Exit-code contract (builder-os ``driver/core/errors.py``, "stable — Ghost keys
behavior off these"): ``0`` recorded · ``8`` unauthorized · ``9`` duplicate.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import json
import logging
import shlex
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from ..adapters.base import Embed, OutgoingMessage
from .builder_humans import BuilderHumans
from .builder_registry import BuilderRegistry
from .builder_renderer import BuilderRenderer, parse_cb

logger = logging.getLogger(__name__)

# builder-os stable exit codes (subset ghost keys on).
RC_OK = 0
RC_UNAUTHORIZED = 8
RC_DUPLICATE = 9

# Fail-closed subprocess timeout (minor): a hung `driver respond` must not hold
# the Discord handler forever. Exceeded ⇒ child killed, nonzero rc reported.
_CLI_TIMEOUT_S = 90.0
_RC_TIMEOUT = 124

# (returncode, stdout, stderr)
Runner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]
# (ticket_uid, nudge_line) — injects a structured hint into the driver pane.
Nudge = Callable[[str, str], Awaitable[None]]
# (ticket_uid, decision_id) → the disposition outcome (B1). Wired by the engine
# to drive `ticket dispose` → `ticket cleanup` → unregister + teardown; a
# recorded READY_FOR_HUMAN disposition routes here instead of the (illegal)
# consume-input nudge.
Disposer = Callable[[str, str], Awaitable["DispositionOutcome"]]


class RespondOutcome(enum.StrEnum):
    """The real result of an answer/disposition, so the command layer can report
    it truthfully (minor: ``/bos respond`` must not say "Submitted" on a
    duplicate / unauthorized / internal failure)."""

    RECORDED = "recorded"          # clarification/escalation answer recorded
    DISPOSED = "disposed"          # disposition recorded + disposed + torn down
    DISPOSE_FAILED = "dispose_failed"  # recorded, but dispose/cleanup did not complete
    DUPLICATE = "duplicate"        # someone else already decided (first-write-wins)
    UNAUTHORIZED = "unauthorized"  # capability token rejected (tamper)
    UNMAPPED = "unmapped"          # Discord id not mapped to a builder identity
    FAILED = "failed"              # builder-os internal / unexpected failure


class DispositionOutcome(enum.StrEnum):
    """Result of the disposition teardown seam (``dispose`` → ``cleanup`` →
    unregister), reported back by the engine-wired :data:`Disposer`."""

    DISPOSED = "disposed"          # dispose ok, cleanup terminated, unregistered
    DISPOSE_FAILED = "dispose_failed"  # `ticket dispose` refused/failed
    CLEANUP_FAILED = "cleanup_failed"  # disposed, but `ticket cleanup` did not terminate


class BuilderResponseAdapter:
    """Resolves + records human decision answers via ``builder-os driver respond``."""

    def __init__(
        self,
        humans: BuilderHumans,
        registry: BuilderRegistry,
        renderer: BuilderRenderer,
        *,
        builder_os_cmd: str = "builder-os",
        builder_os_root: Path | None = None,
        forced_forward_log: Path | None = None,
        runner: Runner | None = None,
        nudge: Nudge | None = None,
        disposer: Disposer | None = None,
        clock: Callable[[], float] = time.time,
        timeout: float = _CLI_TIMEOUT_S,
    ):
        self._humans = humans
        self._registry = registry
        self._renderer = renderer
        self._cmd = builder_os_cmd
        self._root = builder_os_root
        self._forced_forward_log = forced_forward_log
        self._runner = runner or self._default_runner
        self._nudge = nudge
        self._disposer = disposer
        self._clock = clock
        self._timeout = timeout
        self._adapter = None

    def set_adapter(self, adapter) -> None:
        self._adapter = adapter

    # -- button entrypoint --------------------------------------------------

    def owns(self, callback_data: str) -> bool:
        """True iff this callback_data addresses the builder surface."""
        return parse_cb(callback_data) is not None

    async def handle_click(self, channel_id: str, user_id: str, callback_data: str) -> bool:
        """Handle a builder button click. Returns True if it was ours.

        Non-builder callbacks return False immediately so the engine's existing
        button handling is provably unaffected.
        """
        parsed = parse_cb(callback_data)
        if parsed is None:
            return False
        kind, uid, decision_id, choice = parsed

        if kind == "o":
            await self._handle_observation(channel_id, uid, decision_id, choice)
            return True

        # Decision / disposition: authenticated, single-use. The callback kind is
        # authoritative — a ``disp`` button is a READY_FOR_HUMAN disposition
        # (record → dispose → cleanup → unregister), a ``d`` button is an ordinary
        # clarification/escalation answer (record → consume-input nudge). B1.
        await self._handle_decision(
            channel_id, user_id, uid, decision_id, choice,
            is_disposition=(kind == "disp"),
        )
        return True

    # -- observation (non-consuming) ---------------------------------------

    async def _handle_observation(
        self, channel_id: str, uid: str, decision_id: str, obs_id: str,
    ) -> None:
        """inspect / open_evidence: re-surface refs. Never touches the record."""
        rec = self._renderer.decision_record(uid, decision_id) or {}
        lines = [f"🔍 **{obs_id}** (non-consuming — the decision is untouched)"]
        if rec.get("candidate_ref"):
            lines.append(f"• candidate: `{rec['candidate_ref']}`")
        if rec.get("summary_ref"):
            lines.append(f"• summary: `{rec['summary_ref']}`")
        await self._send(channel_id, OutgoingMessage(text="\n".join(lines)))

    # -- slash-command entrypoint (T8 `/bos respond`) ----------------------

    async def respond(
        self, channel_id: str, user_id: str, uid: str, decision_id: str, choice: str,
    ) -> RespondOutcome:
        """`/bos respond` → the same authenticated, single-use path as a button.

        A thin, explicit entrypoint for the T8 slash command: the command layer
        has already resolved the ticket (from the bound thread) and the open
        decision (from the renderer projection); the actor gate + capability-token
        pass-through + two-phase render all live in :meth:`_handle_decision`, so
        button clicks and ``/bos respond`` are provably one code path (fail-closed
        on identity, keyed off builder-os exit codes).

        The open decision may itself be a disposition (the completion card is
        answerable by ``/bos respond`` too), so route by the tracked record kind
        — a disposition must drive dispose→cleanup→unregister, never the illegal
        consume-input nudge (B1). Returns the real :class:`RespondOutcome` so the
        command layer reports the true result, not an unconditional "Submitted".
        """
        rec = self._renderer.decision_record(uid, decision_id) or {}
        is_disposition = rec.get("kind") == "disposition"
        return await self._handle_decision(
            channel_id, user_id, uid, decision_id, choice,
            is_disposition=is_disposition,
        )

    # -- decision (authenticated, single-use) ------------------------------

    async def _handle_decision(
        self, channel_id: str, user_id: str, uid: str, decision_id: str, choice: str,
        *, is_disposition: bool = False,
    ) -> RespondOutcome:
        # (1) actor resolution — fail closed. Unmapped ⇒ refuse, write nothing.
        actor = self._humans.resolve(user_id)
        if actor is None:
            logger.warning(
                "Builder decision refused: unmapped Discord id %s (ticket %s decision %s)",
                user_id, uid, decision_id,
            )
            await self._send(channel_id, OutgoingMessage(embed=Embed(
                title="🚫 Unmapped identity — decision refused",
                description=(
                    "Your Discord account is not mapped to a builder identity, so "
                    "this decision was **not** recorded (fail-closed). Ask the "
                    "operator to add you to `builder_humans.json`."
                ),
                color=0xE74C3C,
                footer=f"ticket {uid} · decision {decision_id}",
            )))
            return RespondOutcome.UNMAPPED

        # (2) capability token pass-through (ghost never validates it).
        ticket = self._registry.get(uid)
        token = ticket.capability_token if ticket else None

        args = [
            "driver", "respond",
            "--ticket", uid,
            "--decision", decision_id,
            "--choice", choice,
            "--actor", actor,
            "--source", "ghost-discord",
        ]
        if token:
            args += ["--token", token]

        rc, out, err = await self._runner(args)

        # (3) render outcome by stable exit code.
        if rc == RC_OK:
            # B1: a READY_FOR_HUMAN disposition advances via dispose→cleanup→
            # unregister; consume-input is illegal from READY_FOR_HUMAN. An
            # ordinary clarification/escalation answer flips to "recorded, awaiting
            # consume" and gets the resume nudge. Route disposition straight to
            # teardown so the card never shows the (wrong) "awaiting consume".
            if is_disposition:
                return await self._drive_disposition(
                    channel_id, uid, decision_id, actor, choice)
            await self._renderer.mark_recorded(uid, decision_id, actor, choice)
            await self._emit_answer_nudge(uid, decision_id)
            return RespondOutcome.RECORDED
        elif rc == RC_DUPLICATE:
            detail = (_first_line(err)
                      or f"decision {decision_id} already decided (first-write-wins).")
            await self._renderer.mark_duplicate(uid, decision_id, detail)
            return RespondOutcome.DUPLICATE
        elif rc == RC_UNAUTHORIZED:
            await self._send(channel_id, OutgoingMessage(embed=Embed(
                title="⛔ Rejected — unauthorized (tamper)",
                description=(_first_line(err) or "capability token rejected."),
                color=0x992D22, footer=f"ticket {uid} · decision {decision_id}",
            )))
            return RespondOutcome.UNAUTHORIZED
        else:
            await self._send(channel_id, OutgoingMessage(embed=Embed(
                title="⚠️ respond failed",
                description=(_first_line(err) or f"builder-os exited {rc}."),
                color=0xE67E22, footer=f"ticket {uid} · decision {decision_id}",
            )))
            return RespondOutcome.FAILED

    async def _drive_disposition(
        self, channel_id: str, uid: str, decision_id: str, actor: str, choice: str,
    ) -> RespondOutcome:
        """Route a recorded disposition into ``dispose`` → ``cleanup`` →
        unregister via the engine-wired :data:`Disposer` seam (B1).

        The choice is already recorded (``driver respond`` returned OK). Without
        a wired disposer the decision is marked recorded (no teardown) — safe, and
        matches the T7 default where the seam is optional. With one, the outcome
        card reflects the real terminal result: disposed+terminated, or a refusal
        that leaves the ticket registered for a retry.
        """
        if self._disposer is None:
            logger.info(
                "disposition %s recorded for %s but no disposer wired — "
                "not tearing down", decision_id, uid)
            await self._renderer.mark_recorded(uid, decision_id, actor, choice)
            return RespondOutcome.RECORDED
        try:
            outcome = await self._disposer(uid, decision_id)
        except Exception:
            logger.warning("disposition teardown raised for %s", uid, exc_info=True)
            outcome = DispositionOutcome.DISPOSE_FAILED
        if outcome == DispositionOutcome.DISPOSED:
            await self._renderer.mark_disposed(
                uid, decision_id,
                banner=f"📦 Disposed by {actor} ({choice}) — cleaned up and terminated.")
            return RespondOutcome.DISPOSED
        # Recorded, but teardown did not complete — surface it, keep the ticket.
        detail = ("`ticket dispose` refused or failed"
                  if outcome == DispositionOutcome.DISPOSE_FAILED
                  else "disposed, but `ticket cleanup` did not terminate")
        await self._send(channel_id, OutgoingMessage(embed=Embed(
            title="⚠️ Disposition incomplete",
            description=(
                f"Your **{choice}** decision was recorded, but {detail}. The "
                "ticket is still registered — re-run once the cause is cleared."
            ),
            color=0xE67E22, footer=f"ticket {uid} · decision {decision_id}",
        )))
        return RespondOutcome.DISPOSE_FAILED

    async def _emit_answer_nudge(self, uid: str, decision_id: str) -> None:
        """§7.4 nudge on an answered decision — a resume hint into the pane."""
        if self._nudge is None:
            return
        rec = self._renderer.decision_record(uid, decision_id) or {}
        line = (
            f"[builder-os] decision {decision_id} answered — "
            f"run: builder-os driver consume-input --decision {decision_id}"
        )
        token = rec.get("resume_token")
        if token:
            line += f" --token {token}"
        try:
            await self._nudge(uid, line)
        except Exception:
            logger.warning("nudge injection failed for %s", uid, exc_info=True)

    # -- forced-forward override (§5.3): mechanics + audit; command is T8 ----

    async def record_forced_forward(self, uid: str, user_id: str, text: str) -> str:
        """Append a ghost-side audit record for a ``/bos forward`` override.

        Ghost never writes into builder-os ``runtime-state/`` (§5.5), so the
        forced-forward audit lives in a ghost-owned log rather than the ticket's
        ``inputs.jsonl``. No lifecycle effect. Returns the resolved actor (or the
        raw Discord id if unmapped — forward is an operator escape hatch, not a
        decision answer, so it is audited rather than fail-closed).
        """
        actor = self._humans.resolve(user_id) or f"discord:{user_id}"
        open_decision = self._renderer.first_open_decision(uid)
        record = {
            "ts": self._iso(),
            "ticket_uid": uid,
            "discord_user_id": user_id,
            "actor": actor,
            "source": "forced-forward",
            "open_decision_id": open_decision,
            "text": text,
        }
        if self._forced_forward_log is not None:
            try:
                self._forced_forward_log.parent.mkdir(parents=True, exist_ok=True)
                with open(self._forced_forward_log, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
            except OSError:
                logger.warning(
                    "Failed to write forced-forward audit %s",
                    self._forced_forward_log, exc_info=True,
                )
        logger.info("forced-forward override audited: ticket=%s actor=%s", uid, actor)
        return actor

    # -- helpers ------------------------------------------------------------

    def _iso(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._clock()))

    async def _send(self, channel_id: str, msg: OutgoingMessage) -> None:
        if self._adapter is None:
            logger.warning("BuilderResponseAdapter has no adapter — dropping message")
            return
        await self._adapter.send_message(channel_id, msg)

    async def _default_runner(self, args: list[str]) -> tuple[int, str, str]:
        """Invoke the builder-os CLI as a subprocess. ``builder_os_cmd`` is split
        with :func:`shlex.split` (quoted-path safe) and the verb args appended;
        cwd = ``builder_os_root``. The wait is bounded by ``self._timeout``: a
        hung ``driver respond`` is killed and reported as a nonzero (``124``) rc
        so the button/command handler fails closed instead of hanging."""
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
                await proc.communicate()
            logger.warning(
                "builder-os %s timed out after %.0fs — killed", " ".join(args[:2]),
                self._timeout)
            return _RC_TIMEOUT, "", f"builder-os {' '.join(args[:2])} timed out"
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
