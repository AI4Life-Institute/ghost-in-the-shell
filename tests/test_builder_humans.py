"""Tests for BuilderHumans — fail-closed Discord-id → human-builder resolution."""

import json

from gits.core.builder_humans import BuilderHumans


def test_absent_file_is_fail_closed(tmp_path):
    h = BuilderHumans(tmp_path / "builder_humans.json")
    # Dormant default: nothing exists ⇒ every lookup refuses.
    assert h.resolve("123456789") is None


def test_mapped_id_resolves(tmp_path):
    f = tmp_path / "builder_humans.json"
    f.write_text(json.dumps({"123456789": "liangchen"}))
    h = BuilderHumans(f)
    assert h.resolve("123456789") == "liangchen"
    # int-like ids resolve by string coercion (Discord snowflakes).
    assert h.resolve(123456789) == "liangchen"


def test_unmapped_id_refuses(tmp_path):
    f = tmp_path / "builder_humans.json"
    f.write_text(json.dumps({"111": "liangchen"}))
    h = BuilderHumans(f)
    assert h.resolve("999") is None


def test_blank_and_none_refuse(tmp_path):
    f = tmp_path / "builder_humans.json"
    f.write_text(json.dumps({"111": "liangchen"}))
    h = BuilderHumans(f)
    assert h.resolve("") is None
    assert h.resolve(None) is None


def test_corrupt_file_is_fail_closed(tmp_path):
    f = tmp_path / "builder_humans.json"
    f.write_text("{not valid json")
    h = BuilderHumans(f)
    # A single bad edit can only ever deny, never grant.
    assert h.resolve("111") is None


def test_non_string_value_refuses(tmp_path):
    f = tmp_path / "builder_humans.json"
    f.write_text(json.dumps({"111": 42, "222": "", "333": "  "}))
    h = BuilderHumans(f)
    assert h.resolve("111") is None
    assert h.resolve("222") is None
    assert h.resolve("333") is None


def test_non_object_json_is_fail_closed(tmp_path):
    f = tmp_path / "builder_humans.json"
    f.write_text(json.dumps(["not", "a", "map"]))
    h = BuilderHumans(f)
    assert h.resolve("111") is None
