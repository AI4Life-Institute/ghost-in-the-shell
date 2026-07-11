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
import json
import logging
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

# (returncode, stdout, stderr)
Runner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]
# (ticket_uid, nudge_line) — injects a structured hint into the driver pane.
Nudge = Callable[[str, str], Awaitable[None]]


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
        clock: Callable[[], float] = time.time,
    ):
        self._humans = humans
        self._registry = registry
        self._renderer = renderer
        self._cmd = builder_os_cmd
        self._root = builder_os_root
        self._forced_forward_log = forced_forward_log
        self._runner = runner or self._default_runner
        self._nudge = nudge
        self._clock = clock
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

        # Decision / disposition: authenticated, single-use.
        await self._handle_decision(channel_id, user_id, uid, decision_id, choice)
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
    ) -> None:
        """`/bos respond` → the same authenticated, single-use path as a button.

        A thin, explicit entrypoint for the T8 slash command: the command layer
        has already resolved the ticket (from the bound thread) and the open
        decision (from the renderer projection); the actor gate + capability-token
        pass-through + two-phase render all live in :meth:`_handle_decision`, so
        button clicks and ``/bos respond`` are provably one code path (fail-closed
        on identity, keyed off builder-os exit codes).
        """
        await self._handle_decision(channel_id, user_id, uid, decision_id, choice)

    # -- decision (authenticated, single-use) ------------------------------

    async def _handle_decision(
        self, channel_id: str, user_id: str, uid: str, decision_id: str, choice: str,
    ) -> None:
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
            return

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
            await self._renderer.mark_recorded(uid, decision_id, actor, choice)
            await self._emit_answer_nudge(uid, decision_id)
        elif rc == RC_DUPLICATE:
            detail = (_first_line(err)
                      or f"decision {decision_id} already decided (first-write-wins).")
            await self._renderer.mark_duplicate(uid, decision_id, detail)
        elif rc == RC_UNAUTHORIZED:
            await self._send(channel_id, OutgoingMessage(embed=Embed(
                title="⛔ Rejected — unauthorized (tamper)",
                description=(_first_line(err) or "capability token rejected."),
                color=0x992D22, footer=f"ticket {uid} · decision {decision_id}",
            )))
        else:
            await self._send(channel_id, OutgoingMessage(embed=Embed(
                title="⚠️ respond failed",
                description=(_first_line(err) or f"builder-os exited {rc}."),
                color=0xE67E22, footer=f"ticket {uid} · decision {decision_id}",
            )))

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
        on whitespace and the verb args appended; cwd = ``builder_os_root``."""
        cmd = self._cmd.split() + args
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._root) if self._root else None,
        )
        out_b, err_b = await proc.communicate()
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
