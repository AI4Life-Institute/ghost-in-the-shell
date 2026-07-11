"""Minimal hand-rolled validator for builder-os driver events (0002 §4.1/§4.4).

Ghost consumes the builder-os agent-to-agent event protocol but must not take a
hard dependency on ``jsonschema`` — this is a live PM2 production system and the
change is additive. So instead of full JSON Schema draft-2020-12 validation we
hand-roll the load-bearing subset the ghost consumer actually needs:

    * ``protocol`` is exactly ``PROTOCOL`` (the divergence guard: any other value
      is an unknown protocol → the monitor freezes the ticket and T7 renders one
      "update ghost" card, 0002 §4.1);
    * the envelope carries every required key (``ENVELOPE_REQUIRED``);
    * ``type`` is a known event type;
    * ``state`` is in the declared per-type set (the normative §4.4 catalog's
      type->state binding).

Per-type *payload* shape is intentionally NOT validated here — that is
builder-os's concern and is re-checked there; ghost validates the envelope + the
type->state binding only (0002 §5.4, T6 scope).

The tables below are the single in-code source of truth. A vendored YAML copy
(``contract/schemas/driver-event.schema.yaml``) mirrors them, and
``test_builder_schema.py`` asserts the two agree field-for-field so drift
between code and the vendored schema is caught mechanically (PM ruling #1). T9
separately reconciles the vendored copy against builder-os's real jsonschema.
"""

from __future__ import annotations

from pathlib import Path

# Directory holding the vendored schema YAML (packaged alongside the code).
SCHEMA_DIR = Path(__file__).resolve().parent.parent / "contract" / "schemas"
DRIVER_EVENT_SCHEMA_FILE = SCHEMA_DIR / "driver-event.schema.yaml"

# Protocol version this ghost build understands (0002 §4.1). A line whose
# ``protocol`` differs is an unknown protocol, not a schema error — the monitor
# distinguishes the two (freeze reasons ``unknown_protocol`` vs ``schema_invalid``).
PROTOCOL = 1

# Envelope required top-level keys (0002 §4.1).
ENVELOPE_REQUIRED: tuple[str, ...] = (
    "protocol",
    "event_id",
    "sequence",
    "occurred_at",
    "ticket_uid",
    "driver_session_id",
    "epoch",
    "role",
    "type",
    "state",
    "summary",
    "payload",
)

# Full lifecycle state set (0001 §5.1 amended). Instance ``state`` must be in-set.
STATES: frozenset[str] = frozenset(
    {
        "CREATED",
        "ADMISSION_CHECK",
        "ADMITTED",
        "PROVISIONED",
        "ACTIVE",
        "BLOCKED",
        "CANDIDATE_READY",
        "REVIEWING",
        "REVIEW_INCONCLUSIVE",
        "CR_REWORK",
        "DISPOSITION_CHECK",
        "READY_FOR_HUMAN",
        "DISPOSED",
        "CLEANUP",
        "TERMINATED",
        "HALTED",
        "CLEANUP_FAILED",
        "FAILED",
    }
)

# eva.* verdict events are contextual: their ``state`` is the ticket state at
# emission (0002 §4.4, §8). Shared to keep the table DRY.
_EVA_STATES = (
    "ADMISSION_CHECK",
    "ACTIVE",
    "BLOCKED",
    "REVIEW_INCONCLUSIVE",
    "DISPOSITION_CHECK",
    "READY_FOR_HUMAN",
    "HALTED",
)

# type -> allowed ``state`` set (the normative §4.4 catalog binding). The three
# contextual types (driver.resumed, eva.*, driver.disposed) carry a set; the
# invariant majority carry a single state.
TYPE_STATES: dict[str, frozenset[str]] = {
    "driver.started": frozenset({"ACTIVE"}),
    "driver.progress": frozenset({"ACTIVE"}),
    "driver.checkpointed": frozenset({"ACTIVE"}),
    "driver.blocked": frozenset({"BLOCKED"}),
    "driver.human_input_required": frozenset({"BLOCKED"}),
    "driver.escalation_requested": frozenset({"BLOCKED"}),
    "driver.human_input_consumed": frozenset({"ACTIVE"}),
    "driver.resumed": frozenset(
        {"ACTIVE", "BLOCKED", "REVIEWING", "REVIEW_INCONCLUSIVE", "CR_REWORK", "DISPOSITION_CHECK"}
    ),
    "driver.halted": frozenset({"HALTED"}),
    "driver.cleanup_failed": frozenset({"CLEANUP_FAILED"}),
    "driver.failed": frozenset({"FAILED"}),
    "review.started": frozenset({"REVIEWING"}),
    "review.approved": frozenset({"DISPOSITION_CHECK"}),
    "review.changes_requested": frozenset({"CR_REWORK"}),
    "review.unknown": frozenset({"REVIEW_INCONCLUSIVE"}),
    "eva.conforming": frozenset(_EVA_STATES),
    "eva.revise": frozenset(_EVA_STATES),
    "eva.escalate": frozenset(_EVA_STATES),
    "eva.unknown": frozenset(_EVA_STATES),
    "driver.ready_for_human": frozenset({"READY_FOR_HUMAN"}),
    "driver.disposed": frozenset({"DISPOSED", "CLEANUP"}),
    "driver.terminated": frozenset({"TERMINATED"}),
}

# Decision lifecycle (0002 §5.3, §7.2). Types whose payload ``decision_id`` opens
# an outstanding human decision, and the single type that closes it.
DECISION_OPEN_TYPES: frozenset[str] = frozenset(
    {"driver.blocked", "driver.human_input_required", "driver.escalation_requested"}
)
DECISION_CLOSE_TYPE = "driver.human_input_consumed"


class UnknownProtocol(Exception):
    """Raised when an event's ``protocol`` differs from :data:`PROTOCOL`.

    Distinct from :class:`SchemaInvalid` so the monitor can freeze with the
    ``unknown_protocol`` reason (→ one "update ghost" card, 0002 §4.1) rather
    than a generic tamper/fault card.
    """


class SchemaInvalid(Exception):
    """Raised when a complete event line violates the envelope/type->state rules.

    On a complete (newline-terminated) line this signals corruption or tamper
    (0002 §4.2) → the monitor freezes the ticket. A schema-invalid *unterminated*
    tail is handled earlier as not-yet-written and never reaches here.
    """


def validate_event(obj: object) -> dict:
    """Validate one decoded event object against the envelope + type->state rules.

    Returns the validated event dict on success. Raises :class:`UnknownProtocol`
    if ``protocol`` diverges, else :class:`SchemaInvalid` on any other violation.
    ``protocol`` is checked first so a future protocol bump is reported as such
    rather than as a spurious schema error.
    """
    if not isinstance(obj, dict):
        raise SchemaInvalid(f"event is not a JSON object: {type(obj).__name__}")

    # protocol first — the divergence guard.
    if "protocol" not in obj:
        raise SchemaInvalid("missing required field: protocol")
    if obj["protocol"] != PROTOCOL:
        raise UnknownProtocol(f"unknown protocol: {obj['protocol']!r} (expected {PROTOCOL})")

    missing = [k for k in ENVELOPE_REQUIRED if k not in obj]
    if missing:
        raise SchemaInvalid(f"missing required field(s): {', '.join(missing)}")

    seq = obj["sequence"]
    # bool is an int subclass — reject it explicitly so `true` can't pass as 1.
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        raise SchemaInvalid(f"sequence must be an integer >= 1, got {seq!r}")

    if not isinstance(obj["payload"], dict):
        raise SchemaInvalid("payload must be an object")

    etype = obj["type"]
    allowed = TYPE_STATES.get(etype)
    if allowed is None:
        raise SchemaInvalid(f"unknown event type: {etype!r}")

    state = obj["state"]
    if state not in allowed:
        raise SchemaInvalid(
            f"state {state!r} not valid for type {etype!r} (allowed: {sorted(allowed)})"
        )

    return obj
