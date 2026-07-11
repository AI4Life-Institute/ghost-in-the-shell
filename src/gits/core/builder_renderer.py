"""BuilderRenderer (G3) — turns validated driver events into the operator surface.

This is the consumer T6 left a seam for. It hangs off
:meth:`BuilderEventMonitor.on_event` / ``on_fault`` and does the whole of 0002
§5.2 rendering:

* **Lifecycle cards** for the four decision/terminal classes
  (``ready_for_human``, ``escalation_requested``, ``human_input_required``,
  ``failed``) — posted to the ticket thread as visually-distinct embeds and
  **pinned**, with a **mirror** to the persistent Assistant channel.
* **Review one-liners** mirrored to the Assistant channel.
* **``driver.progress`` coalescing** — at most one rendered line per ticket per
  ``coalesce_seconds`` (F7); elided updates are summarised on the next rendered
  line/card ("…N progress updates elided").
* **Completion card** renders ``observations[]`` and ``decisions[]`` as two
  distinct groups (§6.2, D4); observation buttons are non-consuming.
* **Two-phase decision rendering** — the card flips ``open → recorded`` when the
  response adapter records an answer, and ``recorded → delivered`` when the
  monitor observes ``driver.human_input_consumed`` (§5.6, AC3).

**Dedup / crash-window repair (§4.3).** T6 is honestly *at-least-once*: it
persists its receipts on a 10s debounce, so a crash between a successful post
and the receipt write replays the event. This renderer keeps its **own**
``event_id → message_id`` index and persists it *synchronously* on every post —
strictly more durable than T6's debounced receipts — so any T6 replay finds the
event already rendered and returns the existing message id, posting nothing new.
The only residual is a crash in the sub-millisecond gap between Discord's ACK
and the local atomic write, bounded to at most one extra card; a duplicate open
for a decision that already has a tracked card is deleted on sight (repair).

Nothing here parses builder-os contract state — the renderer reads only the
validated event envelope T6 hands it (0002 §5.8: no contract knowledge in ghost).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..adapters.base import Button, Embed, OutgoingMessage
from ..utils.atomic_write import atomic_write_json
from .builder_event_monitor import BuilderEvent
from .builder_registry import BuilderRegistry

logger = logging.getLogger(__name__)

# Embed accent colours per class (operator legibility, §5.2).
_COLOR_READY = 0x2ECC71       # green
_COLOR_ESCALATION = 0xE67E22  # orange
_COLOR_INPUT = 0x3498DB       # blue
_COLOR_FAILED = 0xE74C3C      # red
_COLOR_HALTED = 0xF1C40F      # amber
_COLOR_FAULT = 0x992D22       # dark red

# The four classes that get a pinned card + an Assistant-channel mirror (§5.2).
_LIFECYCLE_TYPES = frozenset({
    "driver.ready_for_human",
    "driver.escalation_requested",
    "driver.human_input_required",
    "driver.failed",
})

# Decision-opening classes whose card carries option buttons + a decision_id.
_DECISION_TYPES = frozenset({
    "driver.human_input_required",
    "driver.escalation_requested",
})

_REVIEW_TYPES = frozenset({
    "review.started", "review.approved", "review.changes_requested",
    "review.unknown",
})

# custom_id wire format. ``|`` separates fields because a ticket uid contains a
# ``:`` (``<repo>:<issue>``); decision ids (ULID) and option ids never contain
# ``|``. Kinds: ``d`` = decision option, ``o`` = observation, ``disp`` =
# disposition option on the completion card.
_CB_PREFIX = "bos"
_CB_SEP = "|"


def make_decision_cb(uid: str, decision_id: str, choice: str) -> str:
    return _CB_SEP.join((_CB_PREFIX, "d", uid, decision_id, choice))


def make_disposition_cb(uid: str, decision_id: str, choice: str) -> str:
    return _CB_SEP.join((_CB_PREFIX, "disp", uid, decision_id, choice))


def make_observation_cb(uid: str, decision_id: str, obs_id: str) -> str:
    return _CB_SEP.join((_CB_PREFIX, "o", uid, decision_id, obs_id))


def parse_cb(callback_data: str) -> tuple[str, str, str, str] | None:
    """Parse a ``bos|…`` callback_data into ``(kind, uid, decision_id, choice)``.

    Returns ``None`` for anything not addressed to the builder surface, so the
    engine's existing (non-builder) button handling is provably untouched.
    """
    if not callback_data.startswith(_CB_PREFIX + _CB_SEP):
        return None
    parts = callback_data.split(_CB_SEP)
    if len(parts) != 5:
        return None
    _, kind, uid, decision_id, choice = parts
    return kind, uid, decision_id, choice


class BuilderRenderer:
    """Renders builder-os events to Discord (0002 §5.2) — the ``on_event`` seam."""

    def __init__(
        self,
        registry: BuilderRegistry,
        state_file: Path,
        *,
        coalesce_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._registry = registry
        self._state_file = state_file
        self._coalesce_seconds = coalesce_seconds
        self._clock = clock
        self._adapter: Any = None

        # Persisted: uid -> {"events": {event_id: msg_id|None},
        #                    "decisions": {decision_id: {...}}}
        # Transient (cosmetic, safe to lose): progress coalescing state.
        self._state: dict[str, dict] = {}
        self._progress: dict[str, dict] = {}  # uid -> {"last": float, "elided": int}
        self._load()

    def set_adapter(self, adapter: Any) -> None:
        self._adapter = adapter

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        if not self._state_file.exists():
            return
        try:
            import json
            raw = json.loads(self._state_file.read_text())
        except (OSError, ValueError):
            logger.warning("Failed to load %s — starting fresh", self._state_file, exc_info=True)
            return
        if isinstance(raw, dict):
            self._state = raw

    async def _persist(self) -> None:
        try:
            await atomic_write_json(self._state_file, self._state)
        except OSError:
            logger.warning("Failed to persist %s", self._state_file, exc_info=True)

    def _ticket_state(self, uid: str) -> dict:
        return self._state.setdefault(uid, {"events": {}, "decisions": {}})

    # -- accessors used by the response adapter / engine --------------------

    def decision_record(self, uid: str, decision_id: str) -> dict | None:
        return self._state.get(uid, {}).get("decisions", {}).get(decision_id)

    def first_open_decision(self, uid: str) -> str | None:
        """The id of the first still-open decision for a ticket (or None)."""
        for did, rec in self._state.get(uid, {}).get("decisions", {}).items():
            if rec.get("status") == "open":
                return did
        return None

    # -- monitor seam: faults ----------------------------------------------

    async def on_fault(self, uid: str, kind: str, detail: str) -> None:
        """Render the freeze/tamper card (§4.2/§4.3). Pinned + mirrored."""
        if kind == "unknown_protocol":
            title = "⚠️ Builder ticket frozen — update ghost"
            body = (
                "This ticket emitted an event in a protocol version ghost does "
                "not understand. Rendering is frozen until ghost is updated and "
                "the log is re-verified (`builder-os ticket verify-log`)."
            )
        else:
            title = "⛔ Builder ticket frozen — log integrity fault"
            body = (
                "A schema/sequence integrity fault was detected; rendering is "
                "frozen for this ticket only. Run `builder-os ticket verify-log` "
                "to clear or confirm."
            )
        embed = Embed(
            title=title, description=body, color=_COLOR_FAULT,
            fields=[("Reason", kind, True), ("Detail", detail[:1000], False)],
            footer=f"ticket {uid}",
        )
        await self._post_lifecycle(uid, embed, pin=True, mirror=True, dedup_key=f"fault:{kind}")

    # -- monitor seam: events ----------------------------------------------

    async def on_event(self, uid: str, ev: BuilderEvent) -> str | None:
        """Render one validated event. Returns the posted Discord message id
        (recorded by the monitor in the projection receipt) or ``None``.

        Dedup first: an event already in our own index is a T6 replay — return
        the stored id and render nothing new.
        """
        ts = self._ticket_state(uid)
        eid = ev.event_id
        if eid in ts["events"]:
            return ts["events"][eid]

        etype = ev.type
        try:
            if etype == "driver.progress":
                return await self._on_progress(uid, ev)
            if etype == "driver.ready_for_human":
                return await self._on_ready(uid, ev)
            if etype in _DECISION_TYPES:
                return await self._on_decision(uid, ev)
            if etype == "driver.failed":
                return await self._on_failed(uid, ev)
            if etype == "driver.human_input_consumed":
                return await self._on_consumed(uid, ev)
            if etype in _REVIEW_TYPES:
                return await self._on_review(uid, ev)
            # Everything else (started/checkpointed/resumed/blocked/halted/
            # cleanup_failed/disposed/terminated/eva.*) → a thin thread line,
            # no pin, no mirror.
            return await self._on_thin(uid, ev)
        except Exception:
            # A render failure must NOT be swallowed as "delivered" — re-raise so
            # the monitor pins the offset and retries next poll (at-least-once).
            logger.warning("BuilderRenderer failed rendering %s for %s", etype, uid, exc_info=True)
            raise

    # -- per-class handlers -------------------------------------------------

    async def _on_progress(self, uid: str, ev: BuilderEvent) -> str | None:
        """Coalesce: ≤1 rendered line per ticket per ``coalesce_seconds`` (F7)."""
        st = self._progress.setdefault(uid, {"last": None, "elided": 0})
        now = self._clock()
        note = ev.payload.get("note") or ev.summary or ""
        if st["last"] is not None and (now - st["last"]) < self._coalesce_seconds:
            st["elided"] += 1
            return None  # coalesced away — receipted by the monitor, nothing posted
        elided = st["elided"]
        st["last"] = now
        st["elided"] = 0
        line = f"⏳ {note}" if note else "⏳ progress"
        if elided:
            line += f"\n_…{elided} progress update{'s' if elided != 1 else ''} elided_"
        ticket = self._registry.get(uid)
        return await self._post_line(uid, ev.event_id, ticket, line, persist=True)

    async def _on_review(self, uid: str, ev: BuilderEvent) -> str | None:
        """Review verdict → Assistant-channel one-liner (+ thin thread line)."""
        verdict = ev.type.split(".", 1)[1]
        rnd = ev.payload.get("round")
        summary = ev.payload.get("findings_summary") or ev.summary or ""
        line = f"🔎 review{f' round {rnd}' if rnd is not None else ''}: **{verdict}**"
        if summary:
            line += f" — {summary}"
        ticket = self._registry.get(uid)
        msg_id = await self._post_line(uid, ev.event_id, ticket, line, persist=False)
        # Mirror the one-liner to the Assistant channel (no buttons, no pin).
        if ticket and ticket.assistant_channel_id:
            await self._safe_send(
                ticket.assistant_channel_id, OutgoingMessage(text=f"[{uid}] {line}"))
        await self._persist()
        return msg_id

    async def _on_thin(self, uid: str, ev: BuilderEvent) -> str | None:
        """A low-salience event → one plain thread line (no pin/mirror)."""
        icons = {
            "driver.started": "▶️", "driver.checkpointed": "📌",
            "driver.resumed": "⏵", "driver.blocked": "⏸️",
            "driver.halted": "🟡", "driver.cleanup_failed": "🟡",
            "driver.disposed": "📦", "driver.terminated": "🏁",
        }
        icon = icons.get(ev.type, "•")
        summary = ev.summary or ev.type
        ticket = self._registry.get(uid)
        return await self._post_line(uid, ev.event_id, ticket, f"{icon} {summary}", persist=True)

    async def _on_failed(self, uid: str, ev: BuilderEvent) -> str | None:
        reason = ev.payload.get("reason") or ev.summary or "unknown"
        embed = Embed(
            title="❌ Driver failed (terminal)",
            description=str(reason),
            color=_COLOR_FAILED,
            fields=self._with_elision(uid, [("Ticket", uid, True), ("State", ev.state, True)]),
            footer=f"ticket {uid} · seq {ev.sequence}",
        )
        return await self._post_lifecycle(uid, embed, pin=True, mirror=True,
                                          dedup_key=ev.event_id, event_id=ev.event_id)

    async def _on_decision(self, uid: str, ev: BuilderEvent) -> str | None:
        """``human_input_required`` / ``escalation_requested`` → decision card."""
        payload = ev.payload
        decision_id = payload.get("decision_id")
        question = payload.get("question") or ev.summary or "Decision required"
        options = payload.get("options") or []
        is_escalation = ev.type == "driver.escalation_requested"
        title = ("🚨 Escalation — human decision required" if is_escalation
                 else "❓ Human input required")
        color = _COLOR_ESCALATION if is_escalation else _COLOR_INPUT

        fields: list[tuple[str, str, bool]] = []
        if is_escalation and payload.get("why_human_owned"):
            fields.append(("Why human-owned", str(payload["why_human_owned"])[:1000], False))
        evidence = payload.get("evidence_refs") or []
        if evidence:
            fields.append(("Evidence", "\n".join(f"• {e}" for e in evidence)[:1000], False))
        fields = self._with_elision(uid, fields)

        embed = Embed(
            title=title, description=question, color=color,
            fields=fields, footer=f"ticket {uid} · decision {decision_id}",
        )
        buttons = [self._option_buttons(uid, decision_id, options, make_decision_cb)]

        msg_id = await self._post_lifecycle(
            uid, embed, pin=True, mirror=True, buttons=buttons,
            dedup_key=ev.event_id, event_id=ev.event_id,
        )
        # Track the decision so the response adapter can flip the card and the
        # suppression gate can re-show it.
        if decision_id:
            ticket = self._registry.get(uid)
            self._ticket_state(uid)["decisions"][decision_id] = {
                "thread_msg_id": msg_id,
                "mirror_msg_id": self._last_mirror_id,
                "channel_id": ticket.channel_id if ticket else None,
                "assistant_channel_id": ticket.assistant_channel_id if ticket else None,
                "status": "open",
                "kind": "escalation" if is_escalation else "human_input",
                "question": question,
                "options": options,
                "resume_token": payload.get("resume_token"),
            }
            await self._persist()
        return msg_id

    async def _on_ready(self, uid: str, ev: BuilderEvent) -> str | None:
        """Completion card: observations[] and decisions[] rendered distinctly."""
        payload = ev.payload
        decision_id = (ev.envelope.get("refs") or {}).get("decision")
        title = payload.get("title") or f"{uid} ready for human"
        observations = payload.get("observations") or []
        decisions = payload.get("decisions") or []
        candidate_ref = payload.get("candidate_ref") or ""
        summary_ref = payload.get("summary_ref") or ""

        fields = self._with_elision(uid, [
            ("Candidate", str(candidate_ref) or "—", True),
            ("Summary", str(summary_ref) or "—", True),
            # §6.2 / D4: the two groups are labelled and visually separated.
            ("🔍 Observations (non-consuming)",
             "\n".join(f"• {o.get('label', o.get('id'))}" for o in observations) or "—", False),
            ("✅ Decisions (single-use)",
             "\n".join(f"• {d.get('label', d.get('id'))}" for d in decisions) or "—", False),
        ])
        embed = Embed(
            title=f"🎉 {title}", description="Ready for human disposition — no auto-merge.",
            color=_COLOR_READY, fields=fields,
            footer=f"ticket {uid} · decision {decision_id}",
        )
        # Observation buttons first (their own row), then disposition buttons —
        # visually distinct, and observation clicks never touch the record.
        button_rows: list[list[Button]] = []
        if observations:
            button_rows.append([
                Button(label=f"🔍 {o.get('label', o.get('id'))}",
                       callback_data=make_observation_cb(uid, decision_id or "-", o["id"]))
                for o in observations[:5]
            ])
        if decisions and decision_id:
            button_rows.append(
                self._option_buttons(uid, decision_id, decisions, make_disposition_cb))

        msg_id = await self._post_lifecycle(
            uid, embed, pin=True, mirror=True, buttons=button_rows or None,
            dedup_key=ev.event_id, event_id=ev.event_id,
        )
        if decision_id:
            ticket = self._registry.get(uid)
            self._ticket_state(uid)["decisions"][decision_id] = {
                "thread_msg_id": msg_id,
                "mirror_msg_id": self._last_mirror_id,
                "channel_id": ticket.channel_id if ticket else None,
                "assistant_channel_id": ticket.assistant_channel_id if ticket else None,
                "status": "open",
                "kind": "disposition",
                "question": title,
                "options": decisions,
                "observations": observations,
                "candidate_ref": candidate_ref,
                "summary_ref": summary_ref,
            }
            await self._persist()
        return msg_id

    async def _on_consumed(self, uid: str, ev: BuilderEvent) -> str | None:
        """``driver.human_input_consumed`` → flip the decision card to delivered
        (§5.6, AC3), then a thin confirmation line."""
        decision_id = ev.payload.get("decision_id")
        rec = self.decision_record(uid, decision_id) if decision_id else None
        if rec:
            rec["status"] = "delivered"
            choice = ev.payload.get("choice", rec.get("answered_choice", ""))
            await self._flip_card(
                uid, decision_id,
                banner=f"✅ Delivered — driver consumed decision (choice: {choice}).",
                keep_buttons=False,
            )
            await self._persist()
        ticket = self._registry.get(uid)
        return await self._post_line(
            uid, ev.event_id, ticket,
            f"⏵ decision {decision_id} consumed — driver resuming", persist=True,
        )

    # -- response-adapter-facing state transitions -------------------------

    async def mark_recorded(self, uid: str, decision_id: str, actor: str, choice: str) -> None:
        """Flip a decision card ``open → recorded`` after the answer is written
        (§5.6 "recorded, awaiting driver consume"). Buttons removed (single-use)."""
        rec = self.decision_record(uid, decision_id)
        if not rec:
            return
        rec["status"] = "recorded"
        rec["answered_by"] = actor
        rec["answered_choice"] = choice
        await self._flip_card(
            uid, decision_id,
            banner=f"📝 Answer recorded by {actor} ({choice}) — waiting for driver to consume.",
            keep_buttons=False,
        )
        await self._persist()

    async def mark_duplicate(self, uid: str, decision_id: str, detail: str) -> None:
        """Render the duplicate-rejection card (R8, AC4): "already decided …"."""
        await self._flip_card(
            uid, decision_id,
            banner=f"⛔ {detail}",
            keep_buttons=False,
        )

    async def render_guided_reply(self, uid: str, channel_id: str) -> None:
        """Suppression guided reply (§5.3): a conversational thread reply while
        BLOCKED is held; re-show the pending decision + how to respond."""
        did = None
        decisions = self._state.get(uid, {}).get("decisions", {})
        for d, rec in decisions.items():
            if rec.get("status") == "open":
                did = d
                break
        question = "an open decision"
        if did:
            question = decisions.get(did, {}).get("question", question)
        text = (
            f"⏸️ This ticket is **blocked on {question}**. Your message was **not** "
            "forwarded to the driver.\n"
            "Use the buttons on the pinned decision card or `/bos respond`. "
            "To force-forward anyway, use `/bos forward <text>` (audited)."
        )
        await self._safe_send(channel_id, OutgoingMessage(text=text))

    # -- low-level posting helpers -----------------------------------------

    _last_mirror_id: str | None = None

    async def _post_lifecycle(
        self, uid: str, embed: Embed, *, pin: bool, mirror: bool,
        buttons: list[list[Button]] | None = None,
        dedup_key: str, event_id: str | None = None,
    ) -> str | None:
        """Post a card to the thread (pinned) + mirror it (Assistant channel).

        Returns the thread message id, which is what the monitor receipts. The
        thread id is stored in our own dedup index synchronously so a T6 replay
        renders nothing new."""
        ticket = self._registry.get(uid)
        self._last_mirror_id = None
        if ticket is None or not ticket.channel_id:
            logger.warning("No channel for builder ticket %s — cannot render card", uid)
            return None

        thread_id = await self._safe_send(
            ticket.channel_id, OutgoingMessage(embed=embed, buttons=buttons))
        if thread_id is None:
            # Post failed: raise so the monitor pins + retries (don't record).
            raise RuntimeError(f"card post failed for {uid}")

        ts = self._ticket_state(uid)
        ts["events"][event_id or dedup_key] = thread_id
        await self._persist()  # durable BEFORE returning to the monitor

        if pin:
            await self._safe_pin(ticket.channel_id, thread_id)
        if mirror and ticket.assistant_channel_id:
            self._last_mirror_id = await self._safe_send(
                ticket.assistant_channel_id,
                OutgoingMessage(embed=embed, buttons=buttons),
            )
            if self._last_mirror_id:
                await self._safe_pin(ticket.assistant_channel_id, self._last_mirror_id)
        return thread_id

    async def _post_line(
        self, uid: str, event_id: str, ticket, text: str, *, persist: bool,
    ) -> str | None:
        if ticket is None or not ticket.channel_id:
            return None
        msg_id = await self._safe_send(ticket.channel_id, OutgoingMessage(text=text))
        if msg_id is None:
            raise RuntimeError(f"line post failed for {uid}")
        self._ticket_state(uid)["events"][event_id] = msg_id
        if persist:
            await self._persist()
        return msg_id

    async def _flip_card(
        self, uid: str, decision_id: str, *, banner: str, keep_buttons: bool,
    ) -> None:
        """Edit a decision card in place (thread + mirror): prepend a status
        banner to the description and drop the buttons unless asked to keep."""
        rec = self.decision_record(uid, decision_id)
        if not rec:
            return
        embed = Embed(
            title="🔒 Decision resolved" if not keep_buttons else "Decision",
            description=f"{banner}\n\n_{rec.get('question', '')}_",
            color=_COLOR_INPUT,
            footer=f"ticket {uid} · decision {decision_id}",
        )
        buttons = None
        if keep_buttons:
            buttons = [
                self._option_buttons(uid, decision_id, rec.get("options", []), make_decision_cb)]
        for chan_key, msg_key in (("channel_id", "thread_msg_id"),
                                  ("assistant_channel_id", "mirror_msg_id")):
            chan = rec.get(chan_key)
            mid = rec.get(msg_key)
            if chan and mid:
                await self._safe_edit(chan, mid, OutgoingMessage(embed=embed, buttons=buttons))

    def _option_buttons(self, uid, decision_id, options, cb_factory) -> list[Button]:
        return [
            Button(label=str(o.get("label", o.get("id")))[:80],
                   callback_data=cb_factory(uid, decision_id, o["id"]))
            for o in (options or [])[:5]
        ]

    def _with_elision(
        self, uid: str, fields: list[tuple[str, str, bool]],
    ) -> list[tuple[str, str, bool]]:
        """Fold any pending coalesced-progress count into a card as a field."""
        st = self._progress.get(uid)
        if st and st.get("elided"):
            n = st["elided"]
            st["elided"] = 0
            plural = "s" if n != 1 else ""
            fields = fields + [("Progress", f"…{n} progress update{plural} elided", False)]
        return fields

    # -- adapter wrappers (tolerant of a missing/failing adapter) -----------

    async def _safe_send(self, channel_id: str, msg: OutgoingMessage) -> str | None:
        if self._adapter is None:
            logger.warning("BuilderRenderer has no adapter — dropping message to %s", channel_id)
            return None
        return await self._adapter.send_message(channel_id, msg)

    async def _safe_edit(self, channel_id: str, message_id: str, msg: OutgoingMessage) -> None:
        if self._adapter is None:
            return
        try:
            await self._adapter.edit_message(channel_id, message_id, msg)
        except Exception:
            logger.warning(
                "BuilderRenderer edit failed for %s/%s", channel_id, message_id, exc_info=True)

    async def _safe_pin(self, channel_id: str, message_id: str) -> None:
        if self._adapter is None:
            return
        try:
            await self._adapter.pin_message(channel_id, message_id)
        except Exception:
            logger.warning(
                "BuilderRenderer pin failed for %s/%s", channel_id, message_id, exc_info=True)
