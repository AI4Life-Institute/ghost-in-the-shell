"""Tests for the vendored builder-event schema + hand-rolled validator.

Includes the mechanical drift-check (PM ruling #1): the validator's in-code
tables must equal the vendored YAML field-for-field, so an edit to one without
the other fails loudly.
"""

import pytest
import yaml

from gits.core import builder_schema as bs
from gits.core.builder_schema import (
    SchemaInvalid,
    UnknownProtocol,
    validate_event,
)


def _valid_event(**overrides):
    ev = {
        "protocol": 1,
        "event_id": "evt-1",
        "sequence": 1,
        "occurred_at": "2026-07-11T10:20:00Z",
        "ticket_uid": "builder-os:17",
        "driver_session_id": "drv-1",
        "epoch": 1,
        "role": "coder",
        "type": "driver.started",
        "state": "ACTIVE",
        "summary": "started",
        "payload": {},
    }
    ev.update(overrides)
    return ev


# -- drift check -------------------------------------------------------------


class TestSchemaDrift:
    """Validator constants must match the vendored YAML (mechanical drift guard)."""

    def setup_method(self):
        self.y = yaml.safe_load(bs.DRIVER_EVENT_SCHEMA_FILE.read_text())

    def test_protocol(self):
        assert self.y["protocol"] == bs.PROTOCOL

    def test_envelope_required(self):
        assert tuple(self.y["envelope_required"]) == bs.ENVELOPE_REQUIRED

    def test_states(self):
        assert set(self.y["states"]) == set(bs.STATES)

    def test_type_keys(self):
        assert set(self.y["type_states"]) == set(bs.TYPE_STATES)

    def test_type_state_sets(self):
        for etype, states in self.y["type_states"].items():
            assert set(states) == set(bs.TYPE_STATES[etype]), f"drift for {etype}"

    def test_decision_types(self):
        assert set(self.y["decision_open_types"]) == set(bs.DECISION_OPEN_TYPES)
        assert self.y["decision_close_type"] == bs.DECISION_CLOSE_TYPE


# -- validation --------------------------------------------------------------


class TestValidateEvent:
    def test_valid(self):
        ev = _valid_event()
        assert validate_event(ev) is ev

    def test_unknown_protocol_distinct_from_schema_error(self):
        # protocol is checked first, so a bumped protocol is UnknownProtocol
        # even if other fields are also wrong.
        with pytest.raises(UnknownProtocol):
            validate_event(_valid_event(protocol=2))

    def test_missing_required_field(self):
        ev = _valid_event()
        del ev["summary"]
        with pytest.raises(SchemaInvalid):
            validate_event(ev)

    def test_unknown_type(self):
        with pytest.raises(SchemaInvalid):
            validate_event(_valid_event(type="driver.nope"))

    def test_state_not_valid_for_type(self):
        # driver.started must be ACTIVE, not BLOCKED
        with pytest.raises(SchemaInvalid):
            validate_event(_valid_event(state="BLOCKED"))

    def test_contextual_type_accepts_any_in_set(self):
        # driver.resumed may carry several states
        for st in ("ACTIVE", "BLOCKED", "REVIEWING", "DISPOSITION_CHECK"):
            validate_event(_valid_event(type="driver.resumed", state=st))

    def test_sequence_must_be_positive_int(self):
        with pytest.raises(SchemaInvalid):
            validate_event(_valid_event(sequence=0))
        with pytest.raises(SchemaInvalid):
            validate_event(_valid_event(sequence="1"))

    def test_sequence_bool_rejected(self):
        # bool is an int subclass — must not slip through as 1
        with pytest.raises(SchemaInvalid):
            validate_event(_valid_event(sequence=True))

    def test_payload_must_be_object(self):
        with pytest.raises(SchemaInvalid):
            validate_event(_valid_event(payload=[]))

    def test_non_dict_rejected(self):
        with pytest.raises(SchemaInvalid):
            validate_event("not an object")
