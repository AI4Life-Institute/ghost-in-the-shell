"""Tests for SessionManager."""

import asyncio
import json
from pathlib import Path

import pytest

from gits.core.session import SessionBinding, SessionManager


@pytest.fixture
def session_mgr(tmp_path):
    return SessionManager(state_dir=tmp_path)


class TestSessionBinding:
    def test_defaults(self):
        b = SessionBinding(
            platform="discord",
            channel_id="123",
            window_id="@1",
            window_name="test",
            work_dir="/tmp",
        )
        assert b.coding_cli == "claude"
        assert b.cli_session_id is None
        assert b.parent_channel_id is None
        assert b.subdir is None
        assert b.created_at  # should have a timestamp


class TestBind:
    def test_basic_bind(self, session_mgr):
        async def _test():
            b = await session_mgr.bind(
                platform="discord",
                channel_id="ch-1",
                window_id="@1",
                window_name="my-project",
                work_dir="/data/projects/my-project",
            )
            assert b.channel_id == "ch-1"
            assert b.window_id == "@1"
            assert b.work_dir == "/data/projects/my-project"

        asyncio.run(_test())

    def test_bind_creates_state_file(self, session_mgr, tmp_path):
        async def _test():
            await session_mgr.bind(
                platform="discord",
                channel_id="ch-1",
                window_id="@1",
                window_name="test",
                work_dir="/tmp",
            )
            state_file = tmp_path / "state.json"
            assert state_file.exists()
            with open(state_file) as f:
                data = json.load(f)
            assert "ch-1" in data["bindings"]

        asyncio.run(_test())

    def test_bind_with_all_options(self, session_mgr):
        async def _test():
            b = await session_mgr.bind(
                platform="discord",
                channel_id="thread-1",
                window_id="@2",
                window_name="fix-bug",
                work_dir="/data/projects/my-project/frontend",
                coding_cli="codex",
                cli_session_id="sess-abc",
                parent_channel_id="ch-1",
                subdir="frontend",
            )
            assert b.coding_cli == "codex"
            assert b.cli_session_id == "sess-abc"
            assert b.parent_channel_id == "ch-1"
            assert b.subdir == "frontend"

        asyncio.run(_test())

    def test_bind_overwrites(self, session_mgr):
        async def _test():
            await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="old", work_dir="/old",
            )
            await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@2", window_name="new", work_dir="/new",
            )
            b = session_mgr.get_binding("ch-1")
            assert b.window_id == "@2"
            assert b.work_dir == "/new"

        asyncio.run(_test())


class TestUnbind:
    def test_unbind_existing(self, session_mgr):
        async def _test():
            await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="test", work_dir="/tmp",
            )
            removed = await session_mgr.unbind("ch-1")
            assert removed is not None
            assert removed.channel_id == "ch-1"
            assert session_mgr.get_binding("ch-1") is None

        asyncio.run(_test())

    def test_unbind_nonexistent(self, session_mgr):
        async def _test():
            removed = await session_mgr.unbind("nonexistent")
            assert removed is None

        asyncio.run(_test())


class TestGetBinding:
    def test_get_existing(self, session_mgr):
        async def _test():
            await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="test", work_dir="/tmp",
            )
            b = session_mgr.get_binding("ch-1")
            assert b is not None
            assert b.channel_id == "ch-1"

        asyncio.run(_test())

    def test_get_nonexistent(self, session_mgr):
        assert session_mgr.get_binding("missing") is None

    def test_get_by_window(self, session_mgr):
        async def _test():
            await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@5", window_name="test", work_dir="/tmp",
            )
            b = session_mgr.get_binding_by_window("@5")
            assert b is not None
            assert b.channel_id == "ch-1"

        asyncio.run(_test())

    def test_get_by_window_missing(self, session_mgr):
        assert session_mgr.get_binding_by_window("@999") is None


class TestListBindings:
    def test_list_empty(self, session_mgr):
        assert session_mgr.list_bindings() == []

    def test_list_multiple(self, session_mgr):
        async def _test():
            await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="a", work_dir="/a",
            )
            await session_mgr.bind(
                platform="discord", channel_id="ch-2",
                window_id="@2", window_name="b", work_dir="/b",
            )
            bindings = session_mgr.list_bindings()
            assert len(bindings) == 2

        asyncio.run(_test())

    def test_list_threads(self, session_mgr):
        async def _test():
            await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="parent", work_dir="/p",
            )
            await session_mgr.bind(
                platform="discord", channel_id="thread-1",
                window_id="@2", window_name="child",
                work_dir="/p/sub", parent_channel_id="ch-1",
            )
            await session_mgr.bind(
                platform="discord", channel_id="thread-2",
                window_id="@3", window_name="child2",
                work_dir="/p/sub2", parent_channel_id="ch-1",
            )
            threads = session_mgr.list_channel_threads("ch-1")
            assert len(threads) == 2

        asyncio.run(_test())


class TestUpdate:
    def test_update_window_id(self, session_mgr):
        async def _test():
            await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="test", work_dir="/tmp",
            )
            await session_mgr.update_window_id("ch-1", "@99")
            b = session_mgr.get_binding("ch-1")
            assert b.window_id == "@99"

        asyncio.run(_test())

    def test_update_cli_session_id(self, session_mgr):
        async def _test():
            await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="test", work_dir="/tmp",
            )
            await session_mgr.update_cli_session_id("ch-1", "new-session-id")
            b = session_mgr.get_binding("ch-1")
            assert b.cli_session_id == "new-session-id"

        asyncio.run(_test())

    def test_update_cli_session_id_by_window(self, session_mgr):
        async def _test():
            await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="my-win", work_dir="/tmp",
            )
            await session_mgr.update_cli_session_id_by_window("my-win", "from-hook")
            b = session_mgr.get_binding("ch-1")
            assert b.cli_session_id == "from-hook"

        asyncio.run(_test())


class TestPersistence:
    def test_reload_from_disk(self, tmp_path):
        async def _test():
            mgr1 = SessionManager(state_dir=tmp_path)
            await mgr1.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="persist", work_dir="/data",
                cli_session_id="sid-1",
            )

            # New instance loads from disk
            mgr2 = SessionManager(state_dir=tmp_path)
            b = mgr2.get_binding("ch-1")
            assert b is not None
            assert b.window_name == "persist"
            assert b.cli_session_id == "sid-1"

        asyncio.run(_test())

    def test_corrupted_state_file(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("not valid json{{{")
        mgr = SessionManager(state_dir=tmp_path)
        # Should not crash, just start empty
        assert mgr.list_bindings() == []


class TestClaudeAccountField:
    """Phase 0.4 — claude_account field, migration, and serialization."""

    def test_default_is_none(self):
        b = SessionBinding(
            platform="discord", channel_id="c", window_id="@1",
            window_name="w", work_dir="/d",
        )
        assert b.claude_account is None
        assert b.respawn_failed is False

    def test_legacy_state_loads_with_none(self, tmp_path):
        """state.json from older versions (no claude_account field) loads cleanly."""
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({
            "bindings": {
                "ch-1": {
                    "platform": "discord", "channel_id": "ch-1",
                    "window_id": "@1", "window_name": "w", "work_dir": "/d",
                    "coding_cli": "claude", "cli_session_id": "s",
                    "parent_channel_id": None, "subdir": None,
                    "permission_mode": None, "created_at": "2026-04-28T00:00:00",
                    "last_active_at": 0.0, "suspended": False,
                }
            }
        }))
        mgr = SessionManager(state_dir=tmp_path)
        b = mgr.get_binding("ch-1")
        assert b is not None
        assert b.claude_account is None

    def test_state_with_unknown_future_field_loads(self, tmp_path):
        """Forward-compat: unknown fields are silently ignored."""
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({
            "bindings": {
                "ch-1": {
                    "platform": "discord", "channel_id": "ch-1",
                    "window_id": "@1", "window_name": "w", "work_dir": "/d",
                    "future_field_v2": "hello",
                }
            }
        }))
        mgr = SessionManager(state_dir=tmp_path)
        b = mgr.get_binding("ch-1")
        assert b is not None
        assert b.window_name == "w"

    def test_bind_accepts_claude_account(self, session_mgr):
        async def _test():
            b = await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="w", work_dir="/d",
                claude_account="personal",
            )
            assert b.claude_account == "personal"
        asyncio.run(_test())

    def test_serialization_omits_none_claude_account(self, session_mgr, tmp_path):
        async def _test():
            await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="w", work_dir="/d",
                # default claude_account=None
            )
        asyncio.run(_test())
        data = json.loads((tmp_path / "state.json").read_text())
        assert "claude_account" not in data["bindings"]["ch-1"]
        assert "respawn_failed" not in data["bindings"]["ch-1"]

    def test_serialization_writes_set_claude_account(self, session_mgr, tmp_path):
        async def _test():
            await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="w", work_dir="/d",
                claude_account="work",
            )
        asyncio.run(_test())
        data = json.loads((tmp_path / "state.json").read_text())
        assert data["bindings"]["ch-1"]["claude_account"] == "work"

    def test_update_claude_account(self, session_mgr):
        async def _test():
            await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="w", work_dir="/d",
            )
            updated = await session_mgr.update_claude_account("ch-1", "personal")
            assert updated.claude_account == "personal"
            cleared = await session_mgr.update_claude_account("ch-1", None)
            assert cleared.claude_account is None
            missing = await session_mgr.update_claude_account("nope", "x")
            assert missing is None
        asyncio.run(_test())

    def test_mark_respawn_failed(self, session_mgr):
        async def _test():
            await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="w", work_dir="/d",
            )
            await session_mgr.mark_respawn_failed("ch-1", True)
            assert session_mgr.get_binding("ch-1").respawn_failed is True
            await session_mgr.mark_respawn_failed("ch-1", False)
            assert session_mgr.get_binding("ch-1").respawn_failed is False
        asyncio.run(_test())

    def test_migrate_legacy_bindings_skips_already_set(self, session_mgr):
        async def _test():
            await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="w", work_dir="/d",
            )  # claude_account=None
            await session_mgr.bind(
                platform="discord", channel_id="ch-2",
                window_id="@2", window_name="w2", work_dir="/d",
                claude_account="other",
            )
            count = await session_mgr.migrate_legacy_bindings("personal")
            assert count == 1  # only ch-1 was migrated
            assert session_mgr.get_binding("ch-1").claude_account == "personal"
            assert session_mgr.get_binding("ch-2").claude_account == "other"
        asyncio.run(_test())

    def test_migrate_legacy_bindings_idempotent(self, session_mgr):
        async def _test():
            await session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="w", work_dir="/d",
            )
            count1 = await session_mgr.migrate_legacy_bindings("personal")
            count2 = await session_mgr.migrate_legacy_bindings("personal")
            assert count1 == 1
            assert count2 == 0
        asyncio.run(_test())

    def test_migrate_legacy_bindings_empty(self, session_mgr):
        async def _test():
            count = await session_mgr.migrate_legacy_bindings("personal")
            assert count == 0
        asyncio.run(_test())
