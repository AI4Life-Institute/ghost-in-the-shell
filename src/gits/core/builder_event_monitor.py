"""BuilderEventMonitor (G2) — consumes the builder-os event protocol (0002 §5.4).

Ghost's consumer for builder-os driver events. It polls each registered ticket's
``events.jsonl`` (via :class:`~gits.core.builder_registry.BuilderRegistry`) and
forwards validated lifecycle events to a renderer through a clean internal seam
(``on_event``) so T7 (renderer/adapter) plugs in without reworking this monitor.

It mirrors :class:`~gits.core.jsonl_monitor.JsonlMonitor`'s proven mechanics —
byte-offset tracking, mtime fast-path, deliver-then-advance, debounced persisted
offsets — with **two load-bearing divergences** demanded by the protocol:

1. **Byte-0 start on new files** (F2). JsonlMonitor skips to EOF on first
   discovery (don't replay chat history). Here the inverse is mandatory: events
   written before ghost noticed the file (fresh ticket, or ghost down) must
   never be dropped, so an unseen ticket starts at offset 0.

2. **Startup state fold** (B4). Before forwarding is enabled for a ticket, its
   log is folded to rebuild the projection ``{last_state, open_decision_id,
   suppression}`` and the per-``driver_session_id`` sequence cursor. *Delivery*
   resumes from the persisted offset; *state* derives from the full log. A fold
   that shows an open decision starts the ticket suppressed, so a blocking event
   consumed before a restart can never silently reopen the forward path.

Integrity per §4.2/§4.3: every complete line is parse+schema+sequence checked;
a violation freezes rendering **for that ticket only** and emits a fault to the
seam (T7 renders the card). Dedup is durable: projection **receipts**
``{event_id → discord_message_id}`` are persisted in the same store as offsets,
written before the offset advances, so replay never re-delivers a receipted
event. The guarantee is honestly **at-least-once** (0002 §4.3, which supersedes
issue #8's "exactly-once" wording): the crash window is the interval between a
successful ``on_event`` post and the *debounced* (≤10s) receipt persistence, so
an event delivered but not yet receipted is re-posted after a crash. The seam
consumer (T7's ``BuilderRenderer``) closes this: it keeps its own
``event_id → message_id`` index and persists it *synchronously per post* —
strictly more durable than this store's debounced receipts — so a replay finds
the event already rendered and returns the existing id, posting nothing new. The
residual is the sub-millisecond gap between the platform ACK and that atomic
write, bounded to at most one duplicate card and repaired renderer-side; all
card rendering + that repair are T7, out of scope here.

Dormant by default: with no ``~/.gits/builder_tickets.json`` the poll loop is a
stat-and-sleep no-op — zero behavior change, no store file created. A later
registration hot-activates without a restart.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from .builder_registry import BuilderRegistry, BuilderTicket
from .builder_schema import (
    DECISION_CLOSE_TYPE,
    DECISION_OPEN_TYPES,
    SchemaInvalid,
    UnknownProtocol,
    validate_event,
)

logger = logging.getLogger(__name__)

# Freeze reasons (the ``kind`` passed to the fault seam). Kept as constants so
# tests and T7 can key on them without string literals drifting.
FREEZE_SCHEMA = "schema_invalid"
FREEZE_PROTOCOL = "unknown_protocol"
FREEZE_SEQUENCE = "sequence_gap"


@dataclass
class BuilderEvent:
    """A validated driver event handed to the renderer seam (0002 §4.1).

    Thin wrapper over the raw envelope so T7 can read whatever it needs without
    this module committing to a rendering-specific shape.
    """

    ticket_uid: str
    envelope: dict

    @property
    def event_id(self) -> str:
        return self.envelope["event_id"]

    @property
    def sequence(self) -> int:
        return self.envelope["sequence"]

    @property
    def type(self) -> str:
        return self.envelope["type"]

    @property
    def state(self) -> str:
        return self.envelope["state"]

    @property
    def driver_session_id(self) -> str:
        return self.envelope["driver_session_id"]

    @property
    def summary(self) -> str:
        return self.envelope.get("summary", "")

    @property
    def payload(self) -> dict:
        return self.envelope.get("payload", {})


@dataclass
class Projection:
    """Derived ticket state rebuilt by the fold; never persisted (0002 §5.4)."""

    last_state: str | None = None
    open_decision_id: str | None = None
    suppressed: bool = False
    # per driver_session_id → last accepted sequence (spans resumes; §4.1).
    seq_by_session: dict[str, int] = field(default_factory=dict)


# Seam callback types.
OnEvent = Callable[[str, BuilderEvent], Awaitable["str | None"]]
OnFault = Callable[[str, str, str], Awaitable[None]]
# Builder-*global* fault (not per-ticket): the registry file itself is corrupt.
# Missing = dormant (silent); corrupt = surfaced (minor).
OnGlobalFault = Callable[[str], Awaitable[None]]


class BuilderEventMonitor:
    """Polls registered builder tickets' event logs and forwards to a renderer."""

    def __init__(
        self,
        registry: BuilderRegistry,
        offsets_file: Path,
        poll_interval: float = 2.0,
    ):
        self._registry = registry
        self._offsets_file = offsets_file
        self._poll_interval = poll_interval

        self._running = False
        self._task: asyncio.Task | None = None

        # Persisted per-ticket state.
        self._offsets: dict[str, int] = {}
        self._mtimes: dict[str, float] = {}
        # uid -> {event_id -> receipt}. NOTE (MVP bound): receipts grow O(events)
        # per ticket and the whole store is rewritten on each debounced save.
        # They are retained for the ticket's lifetime ON PURPOSE — the shrink /
        # torn-tail self-heal re-reads from byte 0 and relies on them to dedup
        # already-delivered events, so pruning below the offset would reintroduce
        # duplicate dispatch on self-heal. For MVP ticket sizes (hundreds of
        # events, short-lived) this is negligible; bounded pruning keyed to a
        # ticket-terminated signal is post-MVP.
        self._receipts: dict[str, dict[str, dict]] = {}
        self._frozen: dict[str, str] = {}  # uid -> freeze reason

        # Derived / transient (rebuilt by fold, never persisted).
        self._proj: dict[str, Projection] = {}
        self._folded: set[str] = set()
        self._fold_eof: dict[str, int] = {}  # byte position folded up to at startup

        # Debounced persistence, mirroring JsonlMonitor.
        self._dirty = False
        self._last_save = 0.0
        self._SAVE_DEBOUNCE = 10.0

        # Seam callbacks. Default: nothing wired ⇒ events are validated + state
        # is tracked, but nothing is delivered (harmless).
        self._on_event: OnEvent | None = None
        self._on_fault: OnFault | None = None
        self._on_global_fault: OnGlobalFault | None = None
        # Last-surfaced registry-corruption message, for dedup (fire once per
        # distinct fault; clear + log recovery when the file becomes valid again).
        self._registry_fault: str | None = None

        self._load()

    # -- seam registration --------------------------------------------------

    def on_event(self, callback: OnEvent) -> None:
        """Register the renderer seam. Callback returns the posted Discord message
        id (or None); that id is recorded in the projection receipt (0002 §4.3)."""
        self._on_event = callback

    def on_fault(self, callback: OnFault) -> None:
        """Register the fault seam: ``(ticket_uid, kind, detail)``. Invoked once
        when a ticket freezes (schema/protocol/sequence); T7 renders the card."""
        self._on_fault = callback

    def on_global_fault(self, callback: OnGlobalFault) -> None:
        """Register the builder-global fault seam: ``(detail)``. Invoked once
        when the registry file itself is corrupt (distinct from a *missing* file,
        which is the silent dormant default). Lets the operator learn that
        builder rendering is degraded, rather than the corruption being masked as
        zero tickets (minor)."""
        self._on_global_fault = callback

    # -- read-only projection accessors (for T7 / §5.3 suppression) ---------

    def is_suppressed(self, uid: str) -> bool:
        p = self._proj.get(uid)
        return bool(p and p.suppressed)

    def open_decision_id(self, uid: str) -> str | None:
        p = self._proj.get(uid)
        return p.open_decision_id if p else None

    def last_state(self, uid: str) -> str | None:
        p = self._proj.get(uid)
        return p.last_state if p else None

    def is_frozen(self, uid: str) -> bool:
        return uid in self._frozen

    def freeze_reason(self, uid: str) -> str | None:
        return self._frozen.get(uid)

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._running:
            logger.warning("BuilderEventMonitor already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop(), name="builder-event-monitor")
        logger.info("BuilderEventMonitor started (interval=%.1fs)", self._poll_interval)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        self._save(force=True)
        logger.info("BuilderEventMonitor stopped")

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("BuilderEventMonitor poll error")
            self._save()
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        """One poll iteration. No registry ⇒ immediate no-op (dormant default)."""
        if not self._registry.exists():
            return
        # A *present but corrupt* registry is not "zero tickets" — surface it as
        # a builder-global fault (once) and skip iteration; iterating a corrupt
        # view (which reads as empty) would silently stall every live ticket.
        fault = self._registry.integrity_fault()
        if fault is not None:
            await self._surface_registry_fault(fault)
            return
        self._clear_registry_fault()
        for ticket in self._registry.list_tickets():
            try:
                await self._check_ticket(ticket)
            except Exception:
                logger.warning(
                    "BuilderEventMonitor error for ticket %s", ticket.uid, exc_info=True
                )

    async def _surface_registry_fault(self, detail: str) -> None:
        """Emit the builder-global fault once per distinct corruption (minor)."""
        if detail == self._registry_fault:
            return  # already surfaced this exact fault
        self._registry_fault = detail
        logger.error("Builder registry fault — rendering degraded: %s", detail)
        if self._on_global_fault is not None:
            try:
                await self._on_global_fault(detail)
            except Exception:
                logger.warning("on_global_fault seam raised", exc_info=True)

    def _clear_registry_fault(self) -> None:
        if self._registry_fault is not None:
            logger.info("Builder registry fault cleared — registry readable again")
            self._registry_fault = None

    # -- per-ticket ---------------------------------------------------------

    async def _check_ticket(self, ticket: BuilderTicket) -> None:
        uid = ticket.uid
        if uid in self._frozen:
            return  # frozen until a human runs `builder-os ticket verify-log` (T-later)

        # Startup fold (B4): rebuild projection before forwarding for this ticket.
        # Runs once per ticket — at engine start, or when a ticket is registered
        # mid-run (hot-activation) — so forwarding never precedes state rebuild.
        if uid not in self._folded:
            await self._fold(ticket)
            if uid in self._frozen:  # fold detected corruption/tamper
                return

        path = Path(ticket.event_log)
        try:
            stat = path.stat()
        except OSError:
            return  # log not created yet — byte-0 rule applies once it appears

        last_offset = self._offsets.get(uid, 0)  # byte-0 default (F2 inversion)
        last_mtime = self._mtimes.get(uid, 0.0)
        if stat.st_mtime <= last_mtime and stat.st_size <= last_offset:
            return  # unchanged fast-path

        if stat.st_size < last_offset:
            # Append-only logs don't shrink except builder-os's torn-tail
            # self-heal (§4.3: back up + truncate an invalid trailing line). We
            # must re-read from byte 0 — but a bare offset reset would leave
            # `fold_eof` and the per-session sequence cursor at their live-
            # advanced values, so re-read post-fold events would be `is_new` and
            # hit the sequence gate (cursor already ahead) BEFORE receipt dedup —
            # a spurious, permanent freeze that turns the designed self-heal
            # fatal. So re-run the FULL startup fold: it rebuilds the projection,
            # `fold_eof`, and cursors from the shrunken log, after which every
            # surviving line is at/below `fold_eof` (dedup via receipts, no
            # sequence gate) and only genuinely-new appends are sequence-checked.
            logger.info("Builder event log for %s shrank — re-folding (self-heal)", uid)
            self._folded.discard(uid)
            self._proj.pop(uid, None)
            self._fold_eof.pop(uid, None)
            await self._fold(ticket)
            if uid in self._frozen:
                return
            last_offset = 0

        items, consumed = await asyncio.to_thread(self._read_lines, path, last_offset)
        proj = self._proj.setdefault(uid, Projection())
        receipts = self._receipts.setdefault(uid, {})
        fold_eof = self._fold_eof.get(uid, 0)

        committed = last_offset
        for raw, line_end in items:
            obj = self._parse(raw)
            if obj is None:
                # A complete but unparseable line is corruption/tamper (§4.2),
                # not a not-yet-written tail (tails are never newline-terminated
                # and are left unconsumed by _read_lines).
                await self._freeze(uid, FREEZE_SCHEMA, f"unparseable line at byte {committed}")
                self._commit(uid, committed, stat.st_mtime)
                return
            try:
                validate_event(obj)
            except UnknownProtocol as e:
                await self._freeze(uid, FREEZE_PROTOCOL, str(e))
                self._commit(uid, committed, stat.st_mtime)
                return
            except SchemaInvalid as e:
                await self._freeze(uid, FREEZE_SCHEMA, str(e))
                self._commit(uid, committed, stat.st_mtime)
                return

            ev = BuilderEvent(uid, obj)
            # Lines at/below the fold watermark were already validated + applied
            # to the projection during the fold; only delivery remains for them.
            # Genuinely new lines (appended after the fold) advance the sequence
            # cursor and the projection here.
            is_new = line_end > fold_eof
            if is_new and not self._sequence_ok(proj, ev):
                await self._freeze(
                    uid, FREEZE_SEQUENCE,
                    f"sequence {ev.sequence} != expected "
                    f"{proj.seq_by_session.get(ev.driver_session_id, 0) + 1} "
                    f"for driver_session {ev.driver_session_id}",
                )
                self._commit(uid, committed, stat.st_mtime)
                return

            # Deliver-then-advance with durable receipt dedup (§4.3). The sequence
            # cursor and projection advance ONLY after durable acceptance (below),
            # so a transient dispatch failure pins the offset with the cursor
            # unchanged — the retry re-reads the same line and re-checks cleanly
            # instead of tripping a spurious gap and freezing forever.
            eid = ev.event_id
            if eid not in receipts:
                delivered = await self._dispatch(uid, ev, receipts)
                if not delivered:
                    # Pin at the last committed line; retry next poll.
                    self._commit(uid, committed, stat.st_mtime, sync_mtime=False)
                    return

            if is_new:
                self._apply(proj, ev)  # advances seq cursor + last_state/decision
            committed = line_end
            self._offsets[uid] = committed
            self._dirty = True

        # All read lines delivered — advance past any trailing complete content
        # and sync mtime for the fast-path.
        self._commit(uid, max(committed, consumed), stat.st_mtime)

    async def _dispatch(self, uid: str, ev: BuilderEvent, receipts: dict[str, dict]) -> bool:
        """Post one event to the renderer seam and record its receipt.

        Returns True once the receipt is recorded (delivered), False if the seam
        raised — in which case the caller pins the offset so the next poll
        retries (at-least-once). The receipt is written **before** the offset
        advances (the caller advances only after this returns True), so a replay
        skips an already-delivered event.
        """
        if self._on_event is None:
            # No renderer wired: treat as delivered (record a null receipt) so
            # state tracking proceeds without stalling on a missing consumer.
            receipts[ev.event_id] = {"discord_message_id": None, "rendered_at": self._now()}
            self._dirty = True
            return True
        try:
            msg_id = await self._on_event(uid, ev)
        except Exception:
            logger.warning(
                "BuilderEventMonitor dispatch failed for %s event %s — pinning offset",
                uid, ev.event_id, exc_info=True,
            )
            return False
        receipts[ev.event_id] = {"discord_message_id": msg_id, "rendered_at": self._now()}
        self._dirty = True
        return True

    async def _fold(self, ticket: BuilderTicket) -> None:
        """Rebuild the projection from byte 0 without delivering (B4).

        Validates every complete line (so on-disk tamper of history is caught
        here too, §4.2) and records the byte position folded up to, so the
        forward path knows which lines it must NOT re-apply to the projection.
        """
        uid = ticket.uid
        self._folded.add(uid)
        proj = Projection()
        path = Path(ticket.event_log)
        try:
            items, consumed = await asyncio.to_thread(self._read_lines, path, 0)
        except OSError:
            items, consumed = [], 0
        for raw, line_end in items:
            obj = self._parse(raw)
            if obj is None:
                await self._freeze(uid, FREEZE_SCHEMA, f"unparseable line at byte {line_end}")
                return
            try:
                validate_event(obj)
            except UnknownProtocol as e:
                await self._freeze(uid, FREEZE_PROTOCOL, str(e))
                return
            except SchemaInvalid as e:
                await self._freeze(uid, FREEZE_SCHEMA, str(e))
                return
            ev = BuilderEvent(uid, obj)
            if not self._sequence_ok(proj, ev):
                await self._freeze(
                    uid, FREEZE_SEQUENCE,
                    f"sequence {ev.sequence} != expected "
                    f"{proj.seq_by_session.get(ev.driver_session_id, 0) + 1} "
                    f"for driver_session {ev.driver_session_id}",
                )
                return
            self._apply(proj, ev)
        self._proj[uid] = proj
        self._fold_eof[uid] = consumed
        logger.info(
            "Folded builder ticket %s: %d events, state=%s, open_decision=%s, suppressed=%s",
            uid, len(items), proj.last_state, proj.open_decision_id, proj.suppressed,
        )

    # -- projection maths ---------------------------------------------------

    @staticmethod
    def _sequence_ok(proj: Projection, ev: BuilderEvent) -> bool:
        """Per-``driver_session_id`` gap-free-from-1 check (0002 §4.1) — pure.

        The sequence resets per driver_session_id, so a single log spanning
        resumes/restarts (multiple sessions) is fine — each session must still be
        gap-free and monotonic. This is **non-mutating**: the cursor is advanced
        only by :meth:`_apply`, and only after an event is durably accepted, so a
        pinned-then-retried line re-checks against the same expected value rather
        than tripping a spurious gap (deliver-then-advance, §4.3).
        """
        expected = proj.seq_by_session.get(ev.driver_session_id, 0) + 1
        return ev.sequence == expected

    @staticmethod
    def _apply(proj: Projection, ev: BuilderEvent) -> None:
        """Commit one accepted event into the projection.

        Advances the per-``driver_session_id`` sequence cursor together with
        ``last_state`` and the decision/suppression flag. Called only after the
        event is durably accepted (dispatched+receipted, or already receipted),
        so the cursor never runs ahead of what has actually been delivered.
        """
        proj.seq_by_session[ev.driver_session_id] = ev.sequence
        proj.last_state = ev.state
        etype = ev.type
        if etype in DECISION_OPEN_TYPES:
            did = ev.payload.get("decision_id")
            if did:
                proj.open_decision_id = did
        elif etype == DECISION_CLOSE_TYPE:
            did = ev.payload.get("decision_id")
            # First-write-wins ack (§4.5): close only the matching open decision.
            if did and did == proj.open_decision_id:
                proj.open_decision_id = None
        # Suppression tracks "an open decision exists" (§5.3). T7 enforces the
        # actual not-forwarding-to-pane behavior; G2 only rebuilds the flag.
        proj.suppressed = proj.open_decision_id is not None

    # -- freeze -------------------------------------------------------------

    async def _freeze(self, uid: str, reason: str, detail: str) -> None:
        """Freeze rendering for one ticket and emit a fault to the seam (§4.3).

        Idempotent: a ticket already frozen is not re-faulted. Other tickets are
        unaffected — this is per-ticket, not global.
        """
        if uid in self._frozen:
            return
        self._frozen[uid] = reason
        self._dirty = True
        logger.warning("BuilderEventMonitor froze ticket %s: %s (%s)", uid, reason, detail)
        if self._on_fault is not None:
            try:
                await self._on_fault(uid, reason, detail)
            except Exception:
                logger.exception("BuilderEventMonitor fault callback error for %s", uid)

    # -- parsing / IO -------------------------------------------------------

    def _commit(
        self, uid: str, offset: int, mtime: float, *, sync_mtime: bool = True
    ) -> None:
        """Advance the persisted offset (and optionally mtime) for a ticket."""
        if offset > self._offsets.get(uid, 0):
            self._offsets[uid] = offset
            self._dirty = True
        if sync_mtime:
            self._mtimes[uid] = mtime
            self._dirty = True

    @staticmethod
    def _parse(raw: str) -> dict | None:
        raw = raw.strip()
        if not raw:
            return None
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None

    @staticmethod
    def _read_lines(path: Path, offset: int) -> tuple[list[tuple[str, int]], int]:
        """Read newline-terminated lines from *offset*.

        Returns ``(items, consumed)`` where each item is ``(raw_line, line_end)``
        and ``consumed`` is the byte position past the last complete line. A
        trailing partial line (unterminated tail) is left unconsumed so a
        mid-write append is re-read whole next poll (§4.3). Blank lines are
        skipped but still consumed.
        """
        items: list[tuple[str, int]] = []
        consumed = offset
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read()
        except OSError as e:
            logger.error("Error reading builder event log %s: %s", path, e)
            return items, consumed
        pos = 0
        while True:
            nl = data.find(b"\n", pos)
            if nl == -1:
                break  # trailing partial — leave unconsumed
            line_end = offset + nl + 1
            raw_bytes = data[pos:nl + 1]
            pos = nl + 1
            try:
                line = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                # Complete but undecodable — advance past it; caller will treat
                # the (None) parse as tamper.
                items.append(("", line_end))
                consumed = line_end
                continue
            if line.strip():
                items.append((line, line_end))
            consumed = line_end
        return items, consumed

    # -- persistence --------------------------------------------------------

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _load(self) -> None:
        if not self._offsets_file.exists():
            return
        try:
            raw = json.loads(self._offsets_file.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to load %s — starting fresh", self._offsets_file, exc_info=True)
            return
        if not isinstance(raw, dict):
            return
        for uid, rec in raw.items():
            if not isinstance(rec, dict):
                continue
            self._offsets[uid] = rec.get("offset", 0)
            self._mtimes[uid] = rec.get("mtime", 0.0)
            recs = rec.get("receipts", {})
            self._receipts[uid] = recs if isinstance(recs, dict) else {}
            if rec.get("frozen"):
                self._frozen[uid] = rec["frozen"]
        logger.info("Loaded builder event offsets for %d ticket(s)", len(self._offsets))

    def _save(self, force: bool = False) -> None:
        if not self._dirty:
            return
        now = time.time()
        if not force and (now - self._last_save) < self._SAVE_DEBOUNCE:
            return
        raw = {}
        uids = set(self._offsets) | set(self._receipts) | set(self._frozen)
        for uid in uids:
            raw[uid] = {
                "offset": self._offsets.get(uid, 0),
                "mtime": self._mtimes.get(uid, 0.0),
                "receipts": self._receipts.get(uid, {}),
                "frozen": self._frozen.get(uid),
            }
        try:
            tmp = self._offsets_file.with_suffix(".tmp")
            self._offsets_file.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(raw, indent=2))
            tmp.replace(self._offsets_file)
            self._dirty = False
            self._last_save = now
        except OSError:
            logger.warning("Failed to save %s", self._offsets_file, exc_info=True)
