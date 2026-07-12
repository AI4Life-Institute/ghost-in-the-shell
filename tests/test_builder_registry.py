"""Tests for BuilderRegistry (G1) + SessionBinding builder pointer fields."""

import json

from gits.core.builder_registry import BuilderRegistry
from gits.core.session import SessionBinding, _binding_from_dict, _binding_to_dict


def _registry(tmp_path, root=None):
    return BuilderRegistry(tmp_path / "builder_tickets.json", builder_os_root=root)


# -- reads / dormancy --------------------------------------------------------


class TestReads:
    def test_absent_registry_is_empty(self, tmp_path):
        reg = _registry(tmp_path)
        assert reg.exists() is False
        assert reg.list_tickets() == []
        assert reg.get("builder-os:17") is None

    def test_corrupt_file_treated_as_empty_but_faulted(self, tmp_path):
        # Still tolerant (never crashes the poll loop) …
        f = tmp_path / "builder_tickets.json"
        f.write_text("{ this is not json")
        reg = _registry(tmp_path)
        assert reg.list_tickets() == []
        # … but corruption is now SURFACED (minor), distinct from a missing file.
        assert reg.integrity_fault() is not None

    def test_missing_file_is_not_a_fault(self, tmp_path):
        reg = _registry(tmp_path)
        assert reg.integrity_fault() is None  # dormant, not corrupt

    def test_non_object_records_skipped(self, tmp_path):
        f = tmp_path / "builder_tickets.json"
        f.write_text(json.dumps({
            "builder-os:1": "oops",
            "builder-os:2": {"runtime_dir": "/a", "event_log": "/a/events.jsonl"},
        }))
        reg = _registry(tmp_path)
        uids = {t.uid for t in reg.list_tickets()}
        assert uids == {"builder-os:2"}


# -- writes ------------------------------------------------------------------


class TestWrites:
    async def test_register_resolves_relative_against_root(self, tmp_path):
        root = tmp_path / "bos-root"
        reg = _registry(tmp_path, root=root)
        t = await reg.register(
            "builder-os:17",
            runtime_dir="runtime-state/builder-os/17",
            event_log="runtime-state/builder-os/17/events.jsonl",
        )
        assert t.runtime_dir == str(root / "runtime-state/builder-os/17")
        assert t.event_log == str(root / "runtime-state/builder-os/17/events.jsonl")
        # persisted absolute
        stored = json.loads((tmp_path / "builder_tickets.json").read_text())
        assert stored["builder-os:17"]["event_log"].startswith(str(root))

    async def test_register_absolute_passthrough(self, tmp_path):
        reg = _registry(tmp_path, root=tmp_path / "root")
        abs_log = str(tmp_path / "elsewhere" / "events.jsonl")
        t = await reg.register(
            "builder-os:9", runtime_dir=str(tmp_path / "elsewhere"), event_log=abs_log
        )
        assert t.event_log == abs_log

    async def test_capability_token_roundtrip(self, tmp_path):
        reg = _registry(tmp_path)
        await reg.register(
            "builder-os:1",
            runtime_dir=str(tmp_path / "r"),
            event_log=str(tmp_path / "r/events.jsonl"),
            capability_token="tok-secret",
            channel_id="chan-1",
            driver_session_id="drv-1",
            assistant_channel_id="asst-1",
        )
        got = reg.get("builder-os:1")
        assert got.capability_token == "tok-secret"
        assert got.channel_id == "chan-1"
        assert got.assistant_channel_id == "asst-1"

    async def test_unregister(self, tmp_path):
        reg = _registry(tmp_path)
        await reg.register(
            "builder-os:1", runtime_dir=str(tmp_path), event_log=str(tmp_path / "e.jsonl")
        )
        assert reg.get("builder-os:1") is not None
        removed = await reg.unregister("builder-os:1")
        assert removed is not None
        assert reg.get("builder-os:1") is None
        assert await reg.unregister("builder-os:1") is None

    async def test_concurrent_register_no_lost_writes(self, tmp_path):
        """B3: the register RMW is serialized under a cross-process mutex, so
        concurrent registrations don't clobber each other. The old unlocked
        read-modify-write (read {} → write one entry) lost all but the last."""
        import asyncio

        reg = _registry(tmp_path)

        async def reg_one(i):
            await reg.register(
                f"builder-os:{i}",
                runtime_dir=str(tmp_path / f"r{i}"),
                event_log=str(tmp_path / f"r{i}" / "e.jsonl"),
            )

        await asyncio.gather(*[reg_one(i) for i in range(20)])
        uids = {t.uid for t in reg.list_tickets()}
        assert uids == {f"builder-os:{i}" for i in range(20)}


# -- SessionBinding pointer fields -------------------------------------------


class TestBindingPointerFields:
    def test_omitted_when_none(self):
        b = SessionBinding(
            platform="discord", channel_id="c", window_id="@1",
            window_name="w", work_dir="/tmp",
        )
        data = _binding_to_dict(b)
        assert "builder_ticket_uid" not in data
        assert "builder_runtime_dir" not in data

    def test_roundtrip_when_set(self):
        b = SessionBinding(
            platform="discord", channel_id="c", window_id="@1",
            window_name="w", work_dir="/tmp",
            builder_ticket_uid="builder-os:17",
            builder_runtime_dir="/abs/runtime",
        )
        data = _binding_to_dict(b)
        assert data["builder_ticket_uid"] == "builder-os:17"
        assert data["builder_runtime_dir"] == "/abs/runtime"
        b2 = _binding_from_dict(data)
        assert b2.builder_ticket_uid == "builder-os:17"
        assert b2.builder_runtime_dir == "/abs/runtime"

    def test_unknown_field_still_dropped(self):
        # Forward-compat: an unknown future field is ignored, not raised.
        b = _binding_from_dict({
            "platform": "discord", "channel_id": "c", "window_id": "@1",
            "window_name": "w", "work_dir": "/tmp",
            "builder_ticket_uid": "builder-os:1",
            "some_future_field": "x",
        })
        assert b.builder_ticket_uid == "builder-os:1"
