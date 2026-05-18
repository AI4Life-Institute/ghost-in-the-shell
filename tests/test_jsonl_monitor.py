"""Tests for JsonlMonitor — JSONL polling, parsing, and callback firing."""

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gits.core.jsonl_monitor import (
    JsonlMonitor,
    extract_assistant_content,
    format_tool_use_summary,
    parse_jsonl_line,
)


# -- parse_jsonl_line --------------------------------------------------------


class TestParseJsonlLine:
    def test_valid_json(self):
        line = '{"type": "assistant", "message": {}}'
        result = parse_jsonl_line(line)
        assert result == {"type": "assistant", "message": {}}

    def test_empty_line(self):
        assert parse_jsonl_line("") is None
        assert parse_jsonl_line("   ") is None
        assert parse_jsonl_line("\n") is None

    def test_invalid_json(self):
        assert parse_jsonl_line("{broken") is None
        assert parse_jsonl_line("not json at all") is None

    def test_whitespace_stripped(self):
        result = parse_jsonl_line('  {"a": 1}  \n')
        assert result == {"a": 1}


# -- format_tool_use_summary ------------------------------------------------


class TestFormatToolUseSummary:
    def test_read_tool(self):
        result = format_tool_use_summary("Read", {"file_path": "/tmp/foo.py"})
        assert "Read" in result
        assert "/tmp/foo.py" in result

    def test_bash_tool(self):
        result = format_tool_use_summary("Bash", {"command": "ls -la"})
        assert "Bash" in result
        assert "ls -la" in result

    def test_grep_tool(self):
        result = format_tool_use_summary("Grep", {"pattern": "TODO"})
        assert "Grep" in result
        assert "TODO" in result

    def test_write_tool(self):
        result = format_tool_use_summary("Write", {"file_path": "/tmp/out.txt"})
        assert "Write" in result
        assert "/tmp/out.txt" in result

    def test_edit_tool(self):
        result = format_tool_use_summary("Edit", {"file_path": "/tmp/edit.py"})
        assert "Edit" in result
        assert "/tmp/edit.py" in result

    def test_unknown_tool_no_input(self):
        result = format_tool_use_summary("CustomTool", {})
        assert "CustomTool" in result

    def test_non_dict_input(self):
        result = format_tool_use_summary("Foo", "just a string")
        assert "Foo" in result

    def test_long_summary_truncated(self):
        long_cmd = "x" * 300
        result = format_tool_use_summary("Bash", {"command": long_cmd})
        assert len(result) < 350  # summary truncated + prefix
        assert "\u2026" in result

    def test_web_fetch(self):
        result = format_tool_use_summary("WebFetch", {"url": "https://example.com"})
        assert "WebFetch" in result
        assert "https://example.com" in result

    def test_web_search(self):
        result = format_tool_use_summary("WebSearch", {"query": "python async"})
        assert "WebSearch" in result
        assert "python async" in result

    def test_task_tool(self):
        result = format_tool_use_summary("Task", {"description": "do stuff"})
        assert "Task" in result
        assert "do stuff" in result

    def test_generic_tool_first_string_value(self):
        result = format_tool_use_summary("MyTool", {"key": "value123"})
        assert "MyTool" in result
        assert "value123" in result


# -- extract_assistant_content -----------------------------------------------


class TestExtractAssistantContent:
    def test_assistant_text_block(self):
        entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Hello, world!"}
                ]
            },
        }
        result = extract_assistant_content(entry)
        assert result == ["Hello, world!"]

    def test_multiple_text_blocks(self):
        entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "First"},
                    {"type": "text", "text": "Second"},
                ]
            },
        }
        result = extract_assistant_content(entry)
        assert result == ["First", "Second"]

    def test_tool_use_block(self):
        entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/tmp/foo.py"},
                    }
                ]
            },
        }
        result = extract_assistant_content(entry)
        assert len(result) == 1
        assert "Read" in result[0]
        assert "/tmp/foo.py" in result[0]

    def test_mixed_text_and_tool_use(self):
        entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Let me check that file."},
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/tmp/foo.py"},
                    },
                ]
            },
        }
        result = extract_assistant_content(entry)
        assert len(result) == 2
        assert result[0] == "Let me check that file."
        assert "Read" in result[1]

    def test_thinking_block_skipped(self):
        entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "I should..."},
                    {"type": "text", "text": "Here is my answer."},
                ]
            },
        }
        result = extract_assistant_content(entry)
        assert result == ["Here is my answer."]

    def test_user_message_skipped(self):
        entry = {
            "type": "user",
            "message": {
                "content": [{"type": "text", "text": "user prompt"}]
            },
        }
        result = extract_assistant_content(entry)
        assert result == []

    def test_summary_type_skipped(self):
        entry = {"type": "summary", "summary": "some summary text"}
        result = extract_assistant_content(entry)
        assert result == []

    def test_empty_text_block_skipped(self):
        entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": ""},
                    {"type": "text", "text": "   "},
                    {"type": "text", "text": "Real content"},
                ]
            },
        }
        result = extract_assistant_content(entry)
        assert result == ["Real content"]

    def test_string_content(self):
        entry = {
            "type": "assistant",
            "message": {"content": "Plain string response"},
        }
        result = extract_assistant_content(entry)
        assert result == ["Plain string response"]

    def test_no_message(self):
        entry = {"type": "assistant"}
        result = extract_assistant_content(entry)
        assert result == []

    def test_non_dict_message(self):
        entry = {"type": "assistant", "message": "not a dict"}
        result = extract_assistant_content(entry)
        assert result == []


# -- JsonlMonitor -----------------------------------------------------------


@pytest.fixture
def session_mgr():
    mgr = MagicMock()
    mgr.list_bindings.return_value = []
    # touch_active is awaited by JsonlMonitor after every successful outbound POST
    mgr.touch_active = AsyncMock()
    return mgr


@pytest.fixture
def monitor(session_mgr):
    return JsonlMonitor(session_mgr=session_mgr, poll_interval=0.05)


def _make_jsonl_file(path: Path, entries: list[dict]) -> None:
    """Write JSONL entries to a file."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _make_binding(channel_id="ch1", cli_session_id="sess-123", work_dir="/tmp/project"):
    b = MagicMock()
    b.channel_id = channel_id
    b.cli_session_id = cli_session_id
    b.work_dir = work_dir
    b.suspended = False
    b.coding_cli = "claude"
    return b


# -- Lifecycle tests ---------------------------------------------------------


class TestJsonlMonitorLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_task(self, monitor):
        monitor.start()
        assert monitor._running
        assert monitor._task is not None
        monitor.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, monitor):
        monitor.start()
        monitor.stop()
        assert not monitor._running
        assert monitor._task is None

    @pytest.mark.asyncio
    async def test_double_start_warns(self, monitor):
        monitor.start()
        monitor.start()  # should warn, not crash
        monitor.stop()


# -- Byte-offset tracking ---------------------------------------------------


class TestByteOffsetTracking:
    @pytest.mark.asyncio
    async def test_first_poll_skips_to_end(self, monitor, session_mgr, tmp_path):
        """First time seeing a file should skip to end (no replay)."""
        jsonl_file = tmp_path / "sess-123.jsonl"
        _make_jsonl_file(jsonl_file, [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "old message"}]},
            }
        ])

        binding = _make_binding()
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock()
        monitor.on_message(callback)

        # Patch _find_jsonl_file to return our test file
        monitor._find_jsonl_file = MagicMock(return_value=jsonl_file)

        await monitor._poll_once()

        # First poll should NOT trigger callback (skip to end)
        callback.assert_not_called()

        # Offset should be set to file size
        file_key = (binding.channel_id, str(jsonl_file))
        assert monitor._offsets[file_key] == jsonl_file.stat().st_size

    @pytest.mark.asyncio
    async def test_new_content_triggers_callback(self, monitor, session_mgr, tmp_path):
        """New content appended after initial skip should trigger callback."""
        jsonl_file = tmp_path / "sess-123.jsonl"
        _make_jsonl_file(jsonl_file, [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "old"}]},
            }
        ])

        binding = _make_binding()
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock()
        monitor.on_message(callback)
        monitor._find_jsonl_file = MagicMock(return_value=jsonl_file)

        # First poll — skip to end
        await monitor._poll_once()
        callback.assert_not_called()

        # Append new content
        with open(jsonl_file, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "new response"}]},
            }) + "\n")

        await monitor._poll_once()
        callback.assert_called_once()
        assert callback.call_args[0][0] == "ch1"
        assert callback.call_args[0][1] == "new response"

    @pytest.mark.asyncio
    async def test_no_double_read(self, monitor, session_mgr, tmp_path):
        """Content should not be read twice."""
        jsonl_file = tmp_path / "sess-123.jsonl"
        _make_jsonl_file(jsonl_file, [])

        binding = _make_binding()
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock()
        monitor.on_message(callback)
        monitor._find_jsonl_file = MagicMock(return_value=jsonl_file)

        # Initial poll
        await monitor._poll_once()

        # Append content
        with open(jsonl_file, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "msg1"}]},
            }) + "\n")

        await monitor._poll_once()
        assert callback.call_count == 1

        # Poll again without changes
        await monitor._poll_once()
        assert callback.call_count == 1  # still 1 — no double-read


# -- mtime skip -------------------------------------------------------------


class TestMtimeSkip:
    @pytest.mark.asyncio
    async def test_unchanged_file_skipped(self, monitor, session_mgr, tmp_path):
        """File with unchanged mtime should not be read."""
        jsonl_file = tmp_path / "sess-123.jsonl"
        _make_jsonl_file(jsonl_file, [])

        binding = _make_binding()
        session_mgr.list_bindings.return_value = [binding]
        monitor._find_jsonl_file = MagicMock(return_value=jsonl_file)

        # Initialize tracking
        await monitor._poll_once()

        # Set up a spy on _read_new_entries
        original_read = monitor._read_new_entries
        read_calls = []

        def spy_read(fp, offset):
            read_calls.append((fp, offset))
            return original_read(fp, offset)

        monitor._read_new_entries = staticmethod(spy_read)

        # Poll again — file hasn't changed
        await monitor._poll_once()
        assert len(read_calls) == 0


# -- Truncation handling -----------------------------------------------------


class TestTruncationHandling:
    @pytest.mark.asyncio
    async def test_truncated_file_resets_offset(self, monitor, session_mgr, tmp_path):
        """If file is truncated (e.g. /clear), offset should reset."""
        jsonl_file = tmp_path / "sess-123.jsonl"

        # Write initial content
        big_entry = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "x" * 500}]},
        }
        _make_jsonl_file(jsonl_file, [big_entry])

        binding = _make_binding()
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock()
        monitor.on_message(callback)
        monitor._find_jsonl_file = MagicMock(return_value=jsonl_file)

        # First poll — skip to end
        await monitor._poll_once()
        file_key = (binding.channel_id, str(jsonl_file))
        assert monitor._offsets[file_key] > 0

        # Truncate file to something smaller
        _make_jsonl_file(jsonl_file, [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "fresh"}]},
            }
        ])

        # Force mtime change
        new_mtime = os.path.getmtime(jsonl_file) + 1
        os.utime(jsonl_file, (new_mtime, new_mtime))

        await monitor._poll_once()
        # Should have read the new content after reset
        callback.assert_called_once()
        assert "fresh" in callback.call_args[0][1]


# -- Callback behavior -------------------------------------------------------


class TestCallbackBehavior:
    @pytest.mark.asyncio
    async def test_long_text_split(self, monitor, session_mgr, tmp_path):
        """Text exceeding MAX_MESSAGE_LENGTH should be split into chunks."""
        jsonl_file = tmp_path / "sess-123.jsonl"
        _make_jsonl_file(jsonl_file, [])

        binding = _make_binding()
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock()
        monitor.on_message(callback)
        monitor._find_jsonl_file = MagicMock(return_value=jsonl_file)

        # Initialize
        await monitor._poll_once()

        # Append very long message
        with open(jsonl_file, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "A" * 3000}]},
            }) + "\n")

        await monitor._poll_once()
        assert callback.call_count == 2  # split into 2 chunks
        for call_args in callback.call_args_list:
            sent_text = call_args[0][1]
            assert len(sent_text) <= 1900

    @pytest.mark.asyncio
    async def test_callback_error_doesnt_crash(self, monitor, session_mgr, tmp_path):
        """Callback errors should be caught, not crash the monitor."""
        jsonl_file = tmp_path / "sess-123.jsonl"
        _make_jsonl_file(jsonl_file, [])

        binding = _make_binding()
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock(side_effect=RuntimeError("discord down"))
        monitor.on_message(callback)
        monitor._find_jsonl_file = MagicMock(return_value=jsonl_file)

        await monitor._poll_once()

        with open(jsonl_file, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "test"}]},
            }) + "\n")

        # Should not raise
        await monitor._poll_once()

    @pytest.mark.asyncio
    async def test_no_callback_registered(self, monitor, session_mgr, tmp_path):
        """Polling without a callback should not crash."""
        jsonl_file = tmp_path / "sess-123.jsonl"
        _make_jsonl_file(jsonl_file, [])

        binding = _make_binding()
        session_mgr.list_bindings.return_value = [binding]
        monitor._find_jsonl_file = MagicMock(return_value=jsonl_file)

        # No callback registered — should not crash
        await monitor._poll_once()

        with open(jsonl_file, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "test"}]},
            }) + "\n")

        await monitor._poll_once()


# -- No session ID -----------------------------------------------------------


class TestNoSessionId:
    @pytest.mark.asyncio
    async def test_binding_without_session_id_skipped(self, monitor, session_mgr):
        """Bindings without cli_session_id should be skipped."""
        binding = _make_binding(cli_session_id=None)
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock()
        monitor.on_message(callback)

        await monitor._poll_once()
        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_binding_with_empty_session_id_skipped(self, monitor, session_mgr):
        """Bindings with empty cli_session_id should be skipped."""
        binding = _make_binding(cli_session_id="")
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock()
        monitor.on_message(callback)

        await monitor._poll_once()
        callback.assert_not_called()


# -- Tool use output ---------------------------------------------------------


class TestToolUseOutput:
    @pytest.mark.asyncio
    async def test_tool_use_fires_callback(self, monitor, session_mgr, tmp_path):
        """Tool use blocks should generate formatted summary messages."""
        jsonl_file = tmp_path / "sess-123.jsonl"
        _make_jsonl_file(jsonl_file, [])

        binding = _make_binding()
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock()
        monitor.on_message(callback)
        monitor._find_jsonl_file = MagicMock(return_value=jsonl_file)

        # Initialize
        await monitor._poll_once()

        # Append tool use entry
        with open(jsonl_file, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Let me read that."},
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": "/tmp/example.py"},
                        },
                    ]
                },
            }) + "\n")

        await monitor._poll_once()
        assert callback.call_count == 2
        # First call: text
        assert callback.call_args_list[0][0][1] == "Let me read that."
        # Second call: tool_use summary
        assert "Read" in callback.call_args_list[1][0][1]
        assert "/tmp/example.py" in callback.call_args_list[1][0][1]


# -- _read_new_entries (static method) ----------------------------------------


class TestReadNewEntries:
    def test_reads_from_offset(self, tmp_path):
        """Should only read entries after the given byte offset."""
        jsonl_file = tmp_path / "test.jsonl"

        # Write two entries
        entries = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "old"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "new"}]}},
        ]

        with open(jsonl_file, "w") as f:
            f.write(json.dumps(entries[0]) + "\n")
            offset = f.tell()
            f.write(json.dumps(entries[1]) + "\n")

        result = JsonlMonitor._read_new_entries(jsonl_file, offset)
        assert result == ["new"]

    def test_reads_all_from_zero(self, tmp_path):
        """Offset 0 should read all entries."""
        jsonl_file = tmp_path / "test.jsonl"
        _make_jsonl_file(jsonl_file, [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "a"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "b"}]}},
        ])

        result = JsonlMonitor._read_new_entries(jsonl_file, 0)
        assert result == ["a", "b"]

    def test_skips_user_messages(self, tmp_path):
        """User messages should not appear in output."""
        jsonl_file = tmp_path / "test.jsonl"
        _make_jsonl_file(jsonl_file, [
            {"type": "user", "message": {"content": [{"type": "text", "text": "user msg"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "reply"}]}},
        ])

        result = JsonlMonitor._read_new_entries(jsonl_file, 0)
        assert result == ["reply"]

    def test_skips_summary_entries(self, tmp_path):
        """Summary entries should be skipped."""
        jsonl_file = tmp_path / "test.jsonl"
        _make_jsonl_file(jsonl_file, [
            {"type": "summary", "summary": "session summary"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
        ])

        result = JsonlMonitor._read_new_entries(jsonl_file, 0)
        assert result == ["hi"]

    def test_handles_invalid_lines(self, tmp_path):
        """Invalid JSON lines should be skipped without error."""
        jsonl_file = tmp_path / "test.jsonl"
        with open(jsonl_file, "w") as f:
            f.write("not json\n")
            f.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "valid"}]},
            }) + "\n")
            f.write("\n")  # empty line

        result = JsonlMonitor._read_new_entries(jsonl_file, 0)
        assert result == ["valid"]


# -- _find_jsonl_file robustness --------------------------------------------


class TestFindJsonlFile:
    """Test the robust JSONL file finder with various dir-hash formats."""

    def test_exact_dir_hash_match(self, monitor, tmp_path):
        """Exact dir-hash (slashes replaced by dashes) should work."""
        projects = tmp_path / "projects"
        monitor._projects_path = projects

        work_dir = "/data/projects/my-app"
        dir_hash = "-data-projects-my-app"
        session_id = "aaaa-bbbb-cccc-dddd"

        project_dir = projects / dir_hash
        project_dir.mkdir(parents=True)
        jsonl_file = project_dir / f"{session_id}.jsonl"
        jsonl_file.write_text("{}\n")

        binding = _make_binding(
            cli_session_id=session_id, work_dir=work_dir
        )

        result = monitor._find_jsonl_file(binding)
        assert result == jsonl_file

    def test_scan_fallback_when_hash_differs(self, monitor, tmp_path):
        """When dir-hash doesn't match exactly, scanning should find it."""
        projects = tmp_path / "projects"
        monitor._projects_path = projects

        # Real path has underscore, but Claude Code stored it with dash
        work_dir = "/Volumes/Crucial_8T/src/project"
        session_id = "1111-2222-3333-4444"

        # Create dir with dashes (as Claude Code actually does)
        actual_dir = projects / "-Volumes-Crucial-8T-src-project"
        actual_dir.mkdir(parents=True)
        jsonl_file = actual_dir / f"{session_id}.jsonl"
        jsonl_file.write_text("{}\n")

        binding = _make_binding(
            cli_session_id=session_id, work_dir=work_dir
        )

        result = monitor._find_jsonl_file(binding)
        assert result == jsonl_file

    def test_no_projects_dir(self, monitor, tmp_path):
        """Missing projects directory should return None."""
        monitor._projects_path = tmp_path / "nonexistent"
        binding = _make_binding()
        assert monitor._find_jsonl_file(binding) is None

    def test_no_session_id(self, monitor, tmp_path):
        """Binding without session_id should return None."""
        monitor._projects_path = tmp_path
        binding = _make_binding(cli_session_id=None)
        assert monitor._find_jsonl_file(binding) is None

    def test_no_matching_file(self, monitor, tmp_path):
        """No matching JSONL file should return None."""
        projects = tmp_path / "projects"
        projects.mkdir()
        monitor._projects_path = projects

        binding = _make_binding(cli_session_id="nonexistent-session")
        assert monitor._find_jsonl_file(binding) is None


# -- Session map integration ------------------------------------------------


class TestSessionMapIntegration:
    """Test that session_map.json updates flow through to JSONL monitoring."""

    @pytest.mark.asyncio
    async def test_session_map_updates_binding(self, tmp_path):
        """Session map with matching window_id should update binding's session."""
        # Set up session_map.json
        gits_dir = tmp_path / ".gits"
        gits_dir.mkdir()
        session_map = {
            "gits:@5": {
                "session_id": "new-session-abc",
                "cwd": "/tmp/project",
            }
        }
        (gits_dir / "session_map.json").write_text(json.dumps(session_map))

        # Set up monitor with patched session_map location
        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(cli_session_id=None, work_dir="/tmp/project")
        binding.window_id = "@5"
        session_mgr.list_bindings.return_value = [binding]

        monitor = JsonlMonitor(session_mgr=session_mgr, poll_interval=0.05)
        monitor._read_session_map = lambda: session_map

        await monitor._poll_once()

        session_mgr.update_cli_session_id.assert_called_once_with(
            binding.channel_id, "new-session-abc"
        )
        assert binding.cli_session_id == "new-session-abc"

    @pytest.mark.asyncio
    async def test_session_map_with_different_tmux_session_name(self, tmp_path):
        """Session map key with non-'gits' prefix should still match by window_id suffix."""
        session_map = {
            "my-custom-session:@7": {
                "session_id": "sess-xyz",
                "cwd": "/tmp/project",
            }
        }

        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(cli_session_id=None, work_dir="/tmp/project")
        binding.window_id = "@7"
        session_mgr.list_bindings.return_value = [binding]

        monitor = JsonlMonitor(session_mgr=session_mgr, poll_interval=0.05)
        monitor._read_session_map = lambda: session_map

        await monitor._poll_once()

        session_mgr.update_cli_session_id.assert_called_once_with(
            binding.channel_id, "sess-xyz"
        )

    @pytest.mark.asyncio
    async def test_full_pipeline_session_map_to_jsonl_callback(self, tmp_path):
        """End-to-end: session_map provides session_id, then JSONL file is monitored."""
        projects = tmp_path / "projects"
        session_id = "e2e-session-1234"

        # Create JSONL file in a project directory
        project_dir = projects / "-tmp-project"
        project_dir.mkdir(parents=True)
        jsonl_file = project_dir / f"{session_id}.jsonl"
        # Write initial content (will be skipped on first poll)
        _make_jsonl_file(jsonl_file, [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "old"}]},
            }
        ])

        session_map = {
            "gits:@10": {
                "session_id": session_id,
                "cwd": "/tmp/project",
            }
        }

        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(cli_session_id=None, work_dir="/tmp/project")
        binding.window_id = "@10"
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock()
        monitor = JsonlMonitor(
            session_mgr=session_mgr,
            poll_interval=0.05,
            projects_path=projects,
        )
        monitor.on_message(callback)
        monitor._read_session_map = lambda: session_map

        # Poll 1: picks up session_id from session_map, skips JSONL to end
        await monitor._poll_once()
        assert binding.cli_session_id == session_id
        callback.assert_not_called()  # first poll skips to end

        # Append new content to JSONL
        with open(jsonl_file, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "Hello from the new session!"}]
                },
            }) + "\n")

        # Poll 2: should detect new content and fire callback
        await monitor._poll_once()
        callback.assert_called_once()
        assert callback.call_args[0][0] == binding.channel_id
        assert callback.call_args[0][1] == "Hello from the new session!"


# -- Account-aware path resolution (Phase 0.6) --------------------------------


class TestAccountAwarePaths:
    """``_find_claude_jsonl`` resolves the projects dir from binding.claude_account."""

    def test_account_routes_to_account_projects_dir(self, monitor, tmp_path, monkeypatch):
        """When binding.claude_account is set, JSONL is found under ~/.claude-{name}/projects."""
        from gits.core import account as account_mod

        monkeypatch.setattr(
            account_mod.AccountLayout, "__init__",
            lambda self: setattr(self, "_AccountLayout__patched", None) or AccountLayout_init(self, home=tmp_path),
        )
        # Easier: patch Path.home and use real AccountLayout
        monkeypatch.setattr(account_mod.Path, "home", lambda: tmp_path)

        # Set up an account dir with a JSONL for a known work_dir.
        work_dir = "/data/proj"
        dir_hash = "-data-proj"
        session_id = "X-Y-Z"
        account_projects = tmp_path / ".claude-personal" / "projects" / dir_hash
        account_projects.mkdir(parents=True)
        target = account_projects / f"{session_id}.jsonl"
        target.write_text("{}\n")

        # Also a different file under legacy ~/.claude — which MUST be ignored.
        legacy_projects = tmp_path / ".claude" / "projects" / dir_hash
        legacy_projects.mkdir(parents=True)
        (legacy_projects / f"{session_id}.jsonl").write_text("{wrong:1}\n")

        # Re-point monitor's legacy fallback so test isolation holds.
        monitor._projects_path = tmp_path / ".claude" / "projects"

        # Build a real-shaped binding (not MagicMock) — claude_account must be a string.
        binding = MagicMock()
        binding.cli_session_id = session_id
        binding.work_dir = work_dir
        binding.suspended = False
        binding.coding_cli = "claude"
        binding.claude_account = "personal"

        result = monitor._find_jsonl_file(binding)
        assert result == target  # account-aware path, not legacy

    def test_no_account_uses_legacy_path(self, monitor, tmp_path):
        """binding.claude_account=None falls back to monitor._projects_path."""
        legacy = tmp_path / ".claude" / "projects" / "-w"
        legacy.mkdir(parents=True)
        target = legacy / "sess.jsonl"
        target.write_text("{}\n")
        monitor._projects_path = tmp_path / ".claude" / "projects"

        binding = MagicMock()
        binding.cli_session_id = "sess"
        binding.work_dir = "/w"
        binding.suspended = False
        binding.coding_cli = "claude"
        binding.claude_account = None

        result = monitor._find_jsonl_file(binding)
        assert result == target

    def test_non_string_claude_account_treated_as_none(self, monitor, tmp_path):
        """Defensive: a MagicMock or other non-string claude_account → fallback to legacy."""
        legacy = tmp_path / ".claude" / "projects" / "-w"
        legacy.mkdir(parents=True)
        target = legacy / "sess.jsonl"
        target.write_text("{}\n")
        monitor._projects_path = tmp_path / ".claude" / "projects"

        binding = MagicMock()  # claude_account auto-becomes a MagicMock
        binding.cli_session_id = "sess"
        binding.work_dir = "/w"
        binding.suspended = False
        binding.coding_cli = "claude"
        # Don't set claude_account explicitly — MagicMock default.

        result = monitor._find_jsonl_file(binding)
        assert result == target  # not crashed by the MagicMock attribute


def AccountLayout_init(self, home):
    """Helper used in monkeypatched AccountLayout above."""
    self._home = home


# -- Hardening: offset isolation, persistence, session-switch guard ----------


class TestPerChannelOffsetIsolation:
    """Two channels sharing one JSONL file each track their own read position."""

    @pytest.mark.asyncio
    async def test_shared_file_both_channels_receive_all_messages(self, tmp_path):
        """Neither channel's offset should advance the other's read position."""
        projects = tmp_path / "projects"
        session_id = "shared-session-abc"
        project_dir = projects / "-tmp-shared"
        project_dir.mkdir(parents=True)
        jsonl_file = project_dir / f"{session_id}.jsonl"

        # Write a line that both channels should see
        _make_jsonl_file(jsonl_file, [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "first message"}]},
            }
        ])

        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        ch_a = _make_binding(channel_id="ch-a", cli_session_id=session_id, work_dir="/tmp/shared")
        ch_b = _make_binding(channel_id="ch-b", cli_session_id=session_id, work_dir="/tmp/shared")
        session_mgr.list_bindings.return_value = [ch_a, ch_b]

        cb_a = AsyncMock()
        cb_b = AsyncMock()

        monitor = JsonlMonitor(
            session_mgr=session_mgr,
            poll_interval=0.05,
            projects_path=projects,
        )
        # Route callbacks per channel
        async def dispatch(channel_id, text):
            if channel_id == "ch-a":
                await cb_a(channel_id, text)
            else:
                await cb_b(channel_id, text)

        monitor.on_message(dispatch)
        monitor._read_session_map = lambda: {}

        # Poll 1: both channels see file for first time — skip to end
        await monitor._poll_once()
        cb_a.assert_not_called()
        cb_b.assert_not_called()

        # Append new content
        with open(jsonl_file, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "new message"}]},
            }) + "\n")

        # Poll 2: both channels should independently receive the new line
        await monitor._poll_once()
        cb_a.assert_called_once()
        cb_b.assert_called_once()
        assert cb_a.call_args[0][1] == "new message"
        assert cb_b.call_args[0][1] == "new message"

        # Offsets must be independent keys
        key_a = ("ch-a", str(jsonl_file))
        key_b = ("ch-b", str(jsonl_file))
        assert key_a in monitor._offsets
        assert key_b in monitor._offsets


class TestOffsetPersistence:
    """Offsets survive a simulated restart and resume from last position."""

    @pytest.mark.asyncio
    async def test_new_content_after_restart_forwarded_without_replay(self, tmp_path):
        session_id = "persist-session"
        project_dir = tmp_path / "projects" / "-tmp-persist"
        project_dir.mkdir(parents=True)
        jsonl_file = project_dir / f"{session_id}.jsonl"
        offsets_file = tmp_path / "jsonl_offsets.json"

        _make_jsonl_file(jsonl_file, [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "old history"}]},
            }
        ])

        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()
        binding = _make_binding(channel_id="ch1", cli_session_id=session_id, work_dir="/tmp/persist")
        session_mgr.list_bindings.return_value = [binding]

        # --- First "run" ---
        monitor1 = JsonlMonitor(
            session_mgr=session_mgr,
            poll_interval=0.05,
            projects_path=tmp_path / "projects",
        )
        monitor1._offsets_file = offsets_file
        monitor1._read_session_map = lambda: {}

        cb1 = AsyncMock()
        monitor1.on_message(cb1)

        # Poll: skip to end (first-seen)
        await monitor1._poll_once()
        cb1.assert_not_called()

        # Force-save offsets to disk
        monitor1._save_offsets(force=True)
        assert offsets_file.exists()

        # --- Simulated restart: second monitor instance ---
        monitor2 = JsonlMonitor(
            session_mgr=session_mgr,
            poll_interval=0.05,
            projects_path=tmp_path / "projects",
        )
        monitor2._offsets_file = offsets_file
        monitor2._load_offsets()  # reload from disk
        monitor2._read_session_map = lambda: {}

        cb2 = AsyncMock()
        monitor2.on_message(cb2)

        # Append new content after "restart"
        with open(jsonl_file, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "post-restart message"}]},
            }) + "\n")

        # Poll: should only see new content, not replay "old history"
        await monitor2._poll_once()
        cb2.assert_called_once()
        assert cb2.call_args[0][1] == "post-restart message"


class TestSessionAssignment:
    """Session assignment via session_map — A group scenarios."""

    @pytest.mark.asyncio
    async def test_a1_fresh_binding_picks_up_session(self, tmp_path):
        """A1: fresh binding (no session_id) picks up from session_map."""
        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(cli_session_id=None, work_dir="/tmp/proj")
        binding.window_id = "@1"
        session_mgr.list_bindings.return_value = [binding]

        monitor = JsonlMonitor(session_mgr=session_mgr, poll_interval=0.05)
        monitor._read_session_map = lambda: {"gits:@1": {"session_id": "sess-new"}}

        await monitor._poll_once()

        session_mgr.update_cli_session_id.assert_called_once_with(binding.channel_id, "sess-new")
        assert binding.cli_session_id == "sess-new"

    @pytest.mark.asyncio
    async def test_a2_session_switch_accepted_when_old_file_exists(self, tmp_path):
        """A2: session switch accepted even when the old session's file still exists.

        The file-existence guard has been removed. session_map is authoritative.
        """
        projects = tmp_path / "projects"
        project_dir = projects / "-tmp-proj"
        project_dir.mkdir(parents=True)
        (project_dir / "sess-A.jsonl").write_text("{}\n")  # old file still present
        (project_dir / "sess-B.jsonl").write_text("{}\n")  # new file

        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(cli_session_id="sess-A", work_dir="/tmp/proj")
        binding.window_id = "@1"
        session_mgr.list_bindings.return_value = [binding]

        monitor = JsonlMonitor(
            session_mgr=session_mgr, poll_interval=0.05, projects_path=projects
        )
        monitor._read_session_map = lambda: {"gits:@1": {"session_id": "sess-B"}}

        await monitor._poll_once()

        session_mgr.update_cli_session_id.assert_called_once_with(binding.channel_id, "sess-B")
        assert binding.cli_session_id == "sess-B"

    @pytest.mark.asyncio
    async def test_a3_session_switch_accepted_when_old_file_gone(self, tmp_path):
        """A3: session switch accepted when old file is gone (baseline)."""
        projects = tmp_path / "projects"
        project_dir = projects / "-tmp-proj"
        project_dir.mkdir(parents=True)
        (project_dir / "sess-B.jsonl").write_text("{}\n")  # only new file

        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(cli_session_id="sess-A", work_dir="/tmp/proj")
        binding.window_id = "@1"
        session_mgr.list_bindings.return_value = [binding]

        monitor = JsonlMonitor(
            session_mgr=session_mgr, poll_interval=0.05, projects_path=projects
        )
        monitor._read_session_map = lambda: {"gits:@1": {"session_id": "sess-B"}}

        await monitor._poll_once()

        session_mgr.update_cli_session_id.assert_called_once_with(binding.channel_id, "sess-B")
        assert binding.cli_session_id == "sess-B"

    @pytest.mark.asyncio
    async def test_a4_same_session_no_op(self):
        """A4: session_map has the same session already assigned → no update."""
        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(cli_session_id="sess-A", work_dir="/tmp/proj")
        binding.window_id = "@1"
        session_mgr.list_bindings.return_value = [binding]

        monitor = JsonlMonitor(session_mgr=session_mgr, poll_interval=0.05)
        monitor._read_session_map = lambda: {"gits:@1": {"session_id": "sess-A"}}

        await monitor._poll_once()

        session_mgr.update_cli_session_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_a5_no_session_map_entry(self):
        """A5: no session_map entry for this window → session unchanged."""
        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(cli_session_id=None, work_dir="/tmp/proj")
        binding.window_id = "@99"
        session_mgr.list_bindings.return_value = [binding]

        monitor = JsonlMonitor(session_mgr=session_mgr, poll_interval=0.05)
        monitor._read_session_map = lambda: {"gits:@1": {"session_id": "sess-other"}}

        await monitor._poll_once()

        session_mgr.update_cli_session_id.assert_not_called()
        assert binding.cli_session_id is None


class TestTwoWindowsSameDir:
    """B group: two windows sharing project dir each pick correct session_map entry."""

    @pytest.mark.asyncio
    async def test_b1_two_windows_same_dir_each_gets_own_session(self):
        """B1: two windows, same project dir, each gets its own session from session_map."""
        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        ch27 = _make_binding(channel_id="ch-27", cli_session_id=None, work_dir="/tmp/stock")
        ch27.window_id = "@27"
        ch28 = _make_binding(channel_id="ch-28", cli_session_id=None, work_dir="/tmp/stock")
        ch28.window_id = "@28"
        session_mgr.list_bindings.return_value = [ch27, ch28]

        monitor = JsonlMonitor(session_mgr=session_mgr, poll_interval=0.05)
        monitor._read_session_map = lambda: {
            "gits:@27": {"session_id": "sess-27"},
            "gits:@28": {"session_id": "sess-28"},
        }

        await monitor._poll_once()

        calls = {args[0]: args[1] for args, _ in session_mgr.update_cli_session_id.call_args_list}
        assert calls["ch-27"] == "sess-27"
        assert calls["ch-28"] == "sess-28"

    @pytest.mark.asyncio
    async def test_b2_session_map_prevents_cross_window_contamination(self):
        """B2: session_map assigns different sessions → no cross-contamination."""
        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        ch27 = _make_binding(channel_id="ch-27", cli_session_id=None, work_dir="/tmp/stock")
        ch27.window_id = "@27"
        ch28 = _make_binding(channel_id="ch-28", cli_session_id=None, work_dir="/tmp/stock")
        ch28.window_id = "@28"
        session_mgr.list_bindings.return_value = [ch27, ch28]

        monitor = JsonlMonitor(session_mgr=session_mgr, poll_interval=0.05)
        monitor._read_session_map = lambda: {
            "gits:@27": {"session_id": "sess-27"},
            "gits:@28": {"session_id": "sess-28"},
        }

        await monitor._poll_once()

        calls = {args[0]: args[1] for args, _ in session_mgr.update_cli_session_id.call_args_list}
        # ch-28 must NOT get sess-27
        assert calls.get("ch-28") != "sess-27"
        assert calls.get("ch-28") == "sess-28"


class TestMissingSessionWarning:
    """C group: warning when session file not found after assignment.

    Gating is two-stage: (1) user has interacted (first_interaction_at set);
    (2) ``(attempt+1) * _WARN_RETRY_INTERVAL`` seconds elapsed since
    ``max(assigned_at, first_interaction_at)``.
    """

    @pytest.mark.asyncio
    async def test_c1_session_file_not_found_emits_warning_and_discord_message(self, tmp_path):
        """C1: user interacted, jsonl never materialized → WARNING + alert."""
        projects = tmp_path / "projects"
        projects.mkdir()

        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(cli_session_id=None, work_dir="/tmp/proj")
        binding.window_id = "@1"
        # Simulate the user having actually sent a message — this is what
        # opens the warning gate.  Without this the test would (correctly)
        # never see a warning, since fresh /bind alone is not enough.
        binding.first_interaction_at = time.time() - 50
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock()
        monitor = JsonlMonitor(
            session_mgr=session_mgr, poll_interval=0.05, projects_path=projects
        )
        monitor.on_message(callback)
        # session_map assigns sess-missing, but no JSONL file exists
        monitor._read_session_map = lambda: {"gits:@1": {"session_id": "sess-missing"}}

        # First poll: assigns session, queues pending warn (no immediate alert)
        await monitor._poll_once()
        assert binding.cli_session_id == "sess-missing"
        callback.assert_not_called()

        # Advance clock past grace period, on final attempt so warning fires
        monitor._pending_warn["ch1"] = (
            "sess-missing",
            "/tmp/proj",
            time.time() - 46,
            2,  # final attempt (attempt + 1 >= _WARN_MAX_ATTEMPTS=3)
        )

        # Second poll: grace expired, file still absent → warning fires
        await monitor._poll_once()
        callback.assert_called_once()
        alert_text = callback.call_args[0][1]
        assert "sess-missing" in alert_text
        assert "⚠️" in alert_text
        # AC4: warning text describes what was actually checked, no more
        # misleading "--resume from wrong directory" diagnosis.
        assert "no jsonl appeared in" in alert_text
        assert "/tmp/proj" in alert_text
        assert "after first user input" in alert_text
        assert "--resume" not in alert_text


class TestFirstInteractionGate:
    """Gate the missing-session warning on actual user interaction.

    Covers task ``50cp7c`` cases A / A' / B — the fresh-/bind race that
    used to false-alarm at ~46 s every time the user wasn't quick enough.
    """

    @pytest.mark.asyncio
    async def test_a_fresh_bind_no_interaction_no_warning(self, tmp_path):
        """A: fresh /bind, user never types → no warning even after 5 min."""
        projects = tmp_path / "projects"
        projects.mkdir()

        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(cli_session_id=None, work_dir="/tmp/proj")
        binding.window_id = "@1"
        binding.first_interaction_at = None  # explicit: user has not typed
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock()
        monitor = JsonlMonitor(
            session_mgr=session_mgr, poll_interval=0.05, projects_path=projects
        )
        monitor.on_message(callback)
        monitor._read_session_map = lambda: {"gits:@1": {"session_id": "sess-A"}}

        # First poll: assigns session, queues pending warn.
        await monitor._poll_once()
        assert "ch1" in monitor._pending_warn

        # Force the pending entry to look 5 minutes old AND already on its
        # final attempt — without the gate this would emit a warning.
        monitor._pending_warn["ch1"] = ("sess-A", "/tmp/proj", time.time() - 300, 2)

        # Poll repeatedly — should never fire because first_interaction_at is
        # still None.  Entry stays queued (no attempt expiration either).
        for _ in range(5):
            await monitor._poll_once()
        callback.assert_not_called()
        assert "ch1" in monitor._pending_warn

    @pytest.mark.asyncio
    async def test_a_prime_late_interaction_no_warning_if_jsonl_arrives(self, tmp_path):
        """A': idle then late message — if jsonl shows up within grace, no warn."""
        projects = tmp_path / "projects"
        project_dir = projects / "-tmp-proj"
        project_dir.mkdir(parents=True)

        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(cli_session_id=None, work_dir="/tmp/proj")
        binding.window_id = "@1"
        binding.first_interaction_at = None
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock()
        monitor = JsonlMonitor(
            session_mgr=session_mgr, poll_interval=0.05, projects_path=projects
        )
        monitor.on_message(callback)
        monitor._read_session_map = lambda: {"gits:@1": {"session_id": "sess-Aprime"}}

        await monitor._poll_once()  # queues pending_warn

        # Simulate 5 idle minutes, then user types — first_interaction_at set
        # NOW, so anchor slides to now and the 45s window starts fresh.
        binding.first_interaction_at = time.time()

        # Within that fresh window the jsonl appears (claude flushed it).
        jsonl = project_dir / "sess-Aprime.jsonl"
        jsonl.write_text("")

        # Even if we force the entry onto its final attempt, the file-exists
        # short-circuit must skip the warning.
        monitor._pending_warn["ch1"] = (
            "sess-Aprime", "/tmp/proj", time.time() - 300, 2,
        )
        # Anchor for due-check uses max(assigned_at, first_int) — bump first_int
        # backwards so the entry is due, then verify the file-exists path wins.
        binding.first_interaction_at = time.time() - 50

        await monitor._poll_once()
        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_b_immediate_interaction_with_jsonl_no_warning(self, tmp_path):
        """B: bind + immediate message, jsonl present from the start → no warn."""
        projects = tmp_path / "projects"
        project_dir = projects / "-tmp-proj"
        project_dir.mkdir(parents=True)
        jsonl = project_dir / "sess-B.jsonl"
        jsonl.write_text("")

        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(cli_session_id=None, work_dir="/tmp/proj")
        binding.window_id = "@1"
        binding.first_interaction_at = time.time()
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock()
        monitor = JsonlMonitor(
            session_mgr=session_mgr, poll_interval=0.05, projects_path=projects
        )
        monitor.on_message(callback)
        monitor._read_session_map = lambda: {"gits:@1": {"session_id": "sess-B"}}

        # Poll a few times — file is present so even though pending_warn is
        # queued briefly, the file-exists short-circuit must skip the warning.
        await monitor._poll_once()
        # Speed up: pretend it's the final attempt and time has elapsed.
        if "ch1" in monitor._pending_warn:
            monitor._pending_warn["ch1"] = (
                "sess-B", "/tmp/proj", time.time() - 50, 2,
            )
        await monitor._poll_once()
        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_anchor_uses_max_of_assigned_and_first_interaction(self, tmp_path):
        """Anchor = max(assigned_at, first_interaction_at).

        When user interacted long before session was assigned (e.g. across a
        session_switch), the wait counts from assigned_at, not from the older
        first_interaction_at — otherwise switches would fire instantly.
        """
        projects = tmp_path / "projects"
        projects.mkdir()

        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(cli_session_id="sess-old", work_dir="/tmp/proj")
        binding.window_id = "@1"
        # User interacted ages ago, then session_switch just happened.
        binding.first_interaction_at = time.time() - 3600
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock()
        monitor = JsonlMonitor(
            session_mgr=session_mgr, poll_interval=0.05, projects_path=projects
        )
        monitor.on_message(callback)

        # Entry assigned 5 s ago, only on first attempt → anchor=assigned_at,
        # 5 s elapsed < 15 s threshold → NOT due, no warning yet.
        monitor._pending_warn["ch1"] = (
            "sess-old", "/tmp/proj", time.time() - 5, 0,
        )
        monitor._read_session_map = lambda: {}
        await monitor._poll_once()
        callback.assert_not_called()
        assert "ch1" in monitor._pending_warn


class TestSuspendedBindingSkipped:
    """Suspended bindings must not advance their JSONL offset."""

    @pytest.mark.asyncio
    async def test_suspended_binding_offset_not_advanced(self, tmp_path):
        projects = tmp_path / "projects"
        session_id = "susp-session-xyz"
        project_dir = projects / "-tmp-susp"
        project_dir.mkdir(parents=True)
        jsonl_file = project_dir / f"{session_id}.jsonl"
        _make_jsonl_file(jsonl_file, [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "you should not see this"}]},
            }
        ])

        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(
            channel_id="ch-susp",
            cli_session_id=session_id,
            work_dir="/tmp/susp",
        )
        binding.suspended = True
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock()
        monitor = JsonlMonitor(
            session_mgr=session_mgr,
            poll_interval=0.05,
            projects_path=projects,
        )
        monitor.on_message(callback)
        monitor._read_session_map = lambda: {}

        await monitor._poll_once()

        # Callback must not fire — binding was suspended
        callback.assert_not_called()

        # Offset must not have been recorded (binding was skipped entirely)
        file_key = ("ch-susp", str(jsonl_file))
        assert file_key not in monitor._offsets


# -- Codex content parsing ---------------------------------------------------


class TestCodexFormat:
    """H group: Codex CLI JSONL format parsing."""

    def test_h1_response_item_format_parsed(self):
        """H1: Codex response_item format with output_text is extracted."""
        entry = {
            "type": "response_item",
            "payload": {
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Here is the answer."}
                ],
            },
        }
        result = extract_assistant_content(entry)
        assert result == ["Here is the answer."]

    def test_h1_response_item_non_assistant_skipped(self):
        """H1: response_item with role != assistant is ignored."""
        entry = {
            "type": "response_item",
            "payload": {
                "role": "user",
                "content": [{"type": "output_text", "text": "user text"}],
            },
        }
        result = extract_assistant_content(entry)
        assert result == []

    @pytest.mark.asyncio
    async def test_h2_codex_file_found_by_exact_session_id(self, tmp_path):
        """H2: Codex file is found when session_id matches the filename suffix."""
        codex_dir = tmp_path / ".codex" / "sessions" / "2026" / "03" / "19"
        codex_dir.mkdir(parents=True)
        sid = "019cef25-2ae6-73f0-97fc-795524e3cdbe"
        jsonl_file = codex_dir / f"rollout-2026-03-19T10-00-00-{sid}.jsonl"
        jsonl_file.write_text("{}\n")

        binding = _make_binding(cli_session_id=sid, work_dir="/tmp/proj")
        binding.coding_cli = "codex"

        session_mgr = MagicMock()
        monitor = JsonlMonitor(session_mgr=session_mgr, poll_interval=0.05)
        # Patch _find_codex_jsonl to use our tmp codex dir
        original = monitor._find_codex_jsonl
        from pathlib import Path as _Path

        def patched_find_codex(b):
            # Temporarily override codex_dir root
            import gits.core.jsonl_monitor as jm
            old = _Path.home
            _Path.home = lambda: tmp_path
            try:
                return monitor.__class__._find_codex_jsonl(monitor, b)
            finally:
                _Path.home = old

        monitor._find_codex_jsonl = patched_find_codex

        result = monitor._find_jsonl_file(binding)
        assert result == jsonl_file

    @pytest.mark.asyncio
    async def test_h4_codex_binding_uses_session_map(self):
        """H4: Codex bindings use session_map just like Claude bindings."""
        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(cli_session_id=None, work_dir="/tmp/proj")
        binding.window_id = "@5"
        binding.coding_cli = "codex"
        session_mgr.list_bindings.return_value = [binding]

        monitor = JsonlMonitor(session_mgr=session_mgr, poll_interval=0.05)
        monitor._read_session_map = lambda: {"gits:@5": {"session_id": "codex-sess-001"}}

        await monitor._poll_once()

        session_mgr.update_cli_session_id.assert_called_once_with(binding.channel_id, "codex-sess-001")


# -- OpenCode monitoring -----------------------------------------------------


class TestOpenCodeMonitoring:
    """I group: OpenCode SQLite monitoring."""

    @pytest.mark.asyncio
    async def test_i3_opencode_db_missing_skips_gracefully(self, tmp_path):
        """I3: Missing OpenCode DB should not crash the monitor."""
        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(cli_session_id="oc-session-abc")
        binding.coding_cli = "opencode"
        session_mgr.list_bindings.return_value = [binding]

        callback = AsyncMock()
        monitor = JsonlMonitor(session_mgr=session_mgr, poll_interval=0.05)
        monitor.on_message(callback)
        monitor._read_session_map = lambda: {}

        # Override the DB path to a nonexistent location
        original_check = monitor._check_opencode_binding

        async def check_with_missing_db(b):
            import gits.core.jsonl_monitor as jm
            from pathlib import Path
            original_path = Path.home().__class__
            # Just call directly; DB won't exist → should return without error
            await jm.JsonlMonitor._check_opencode_binding(monitor, b)

        monitor._check_opencode_binding = check_with_missing_db

        # Should not raise
        await monitor._poll_once()
        callback.assert_not_called()


# -- Hook non-interactive filter tests ----------------------------------------


class TestHookNonInteractiveFilter:
    """G group: hook skips session_map update for non-interactive CLI invocations.

    Tests call _cmd_hook() directly so that import-scoping bugs (e.g.
    UnboundLocalError from a late `from pathlib import Path`) are caught at
    the same time as the filter logic.
    """

    SESSION_ID = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"

    def _run_hook(self, tmp_path, comm: str, cmdline_args: list[bytes]) -> bool:
        """Call _cmd_hook() for real with mocked /proc and tmux.

        Returns True  → hook returned early (session_map NOT written).
        Returns False → hook ran to completion (session_map written).
        """
        import argparse
        import io
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from gits.__main__ import _cmd_hook  # real function — catches import bugs

        map_file = tmp_path / ".gits" / "session_map.json"

        # Simulate: current pid=100 → shell (200) → cli (300)
        proc_data: dict[str, str | bytes] = {
            "/proc/100/status": "PPid:\t200\n",
            "/proc/200/status": "PPid:\t300\n",
            "/proc/200/comm": "sh\n",
            "/proc/300/comm": f"{comm}\n",
            "/proc/300/cmdline": b"\x00".join(
                [comm.encode()] + cmdline_args
            ) + b"\x00",
        }

        def fake_read_text(self_path):
            val = proc_data.get(str(self_path))
            if val is None:
                raise FileNotFoundError(str(self_path))
            if isinstance(val, bytes):
                raise TypeError("use read_bytes")
            return val

        def fake_read_bytes(self_path):
            val = proc_data.get(str(self_path))
            if val is None:
                raise FileNotFoundError(str(self_path))
            return val if isinstance(val, bytes) else val.encode()

        tmux_result = MagicMock()
        tmux_result.stdout = "gits:@99\n"

        payload = json.dumps({
            "session_id": self.SESSION_ID,
            "cwd": str(tmp_path),
            "hook_event_name": "SessionStart",
        })

        args = argparse.Namespace(
            install=False,
            install_copilot=False,
            install_codex=False,
            install_opencode=False,
        )

        with patch.object(Path, "read_text", fake_read_text), \
             patch.object(Path, "read_bytes", fake_read_bytes), \
             patch.object(Path, "home", staticmethod(lambda: tmp_path)), \
             patch.dict("os.environ", {"TMUX_PANE": "%99"}), \
             patch("os.getpid", return_value=100), \
             patch("sys.stdin", io.StringIO(payload)), \
             patch("subprocess.run", return_value=tmux_result):
            _cmd_hook(args)

        return not map_file.exists()  # True = returned early (no write)

    def test_g1_claude_p_returns_early(self, tmp_path):
        """G1: claude -p ancestor → hook skips session_map update."""
        assert self._run_hook(tmp_path, "claude", [b"-p", b"some prompt"]) is True

    def test_g2_codex_q_returns_early(self, tmp_path):
        """G2: codex -q ancestor → hook skips session_map update."""
        assert self._run_hook(tmp_path, "codex", [b"-q", b"task text"]) is True

    def test_g3_claude_print_returns_early(self, tmp_path):
        """G3: claude --print ancestor → hook skips session_map update."""
        assert self._run_hook(tmp_path, "claude", [b"--print", b"query"]) is True

    def test_g4_codex_quiet_returns_early(self, tmp_path):
        """G4: codex --quiet ancestor → hook skips session_map update."""
        assert self._run_hook(tmp_path, "codex", [b"--quiet"]) is True

    def test_interactive_claude_proceeds(self, tmp_path):
        """Interactive claude (no -p/--print) → hook writes session_map."""
        assert self._run_hook(tmp_path, "claude", []) is False

    def test_interactive_codex_proceeds(self, tmp_path):
        """Interactive codex (no -q/--quiet) → hook writes session_map."""
        assert self._run_hook(tmp_path, "codex", []) is False


# -- Idle-timer refresh on outbound POST (task q4r8nm) -----------------------


class TestTouchActiveOnOutbound:
    """Outbound Discord POSTs must refresh the idle-suspend timer.

    Without this, an outbound-heavy session (PM butler driving crons, file
    edits, PR merges) gets idle-suspended every ~70 min even while busy,
    because `touch_active` was historically only called on inbound messages.
    """

    @pytest.mark.asyncio
    async def test_success_calls_touch_active(
        self, monitor, session_mgr, tmp_path,
    ):
        """A successful forward refreshes the binding's idle timer."""
        jsonl_file = tmp_path / "sess-123.jsonl"
        _make_jsonl_file(jsonl_file, [])

        binding = _make_binding()
        session_mgr.list_bindings.return_value = [binding]
        monitor.on_message(AsyncMock())
        monitor._find_jsonl_file = MagicMock(return_value=jsonl_file)

        # Prime offset
        await monitor._poll_once()
        session_mgr.touch_active.assert_not_called()

        # Append new content → outbound forward should fire touch_active
        with open(jsonl_file, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "hello"}]},
            }) + "\n")

        await monitor._poll_once()
        session_mgr.touch_active.assert_awaited_once_with("ch1")

    @pytest.mark.asyncio
    async def test_failed_post_does_not_touch_active(
        self, monitor, session_mgr, tmp_path,
    ):
        """If _on_message raises, touch_active must NOT fire — refreshing the
        idle timer on a failed POST would mask a broken Discord bridge."""
        jsonl_file = tmp_path / "sess-123.jsonl"
        _make_jsonl_file(jsonl_file, [])

        binding = _make_binding()
        session_mgr.list_bindings.return_value = [binding]
        monitor.on_message(AsyncMock(side_effect=RuntimeError("discord down")))
        monitor._find_jsonl_file = MagicMock(return_value=jsonl_file)

        await monitor._poll_once()

        with open(jsonl_file, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "x"}]},
            }) + "\n")

        await monitor._poll_once()
        session_mgr.touch_active.assert_not_called()

    @pytest.mark.asyncio
    async def test_throttle_coalesces_burst(
        self, monitor, session_mgr, tmp_path,
    ):
        """A burst of chunks inside the throttle window collapses to one touch.

        Otherwise every chunk would re-write state.json (touch_active does an
        atomic save), which would thrash disk on chatty sessions.
        """
        jsonl_file = tmp_path / "sess-123.jsonl"
        _make_jsonl_file(jsonl_file, [])

        binding = _make_binding()
        session_mgr.list_bindings.return_value = [binding]
        monitor.on_message(AsyncMock())
        monitor._find_jsonl_file = MagicMock(return_value=jsonl_file)

        await monitor._poll_once()

        # ~3000 chars → split into 2 chunks (MAX_MESSAGE_LENGTH=1900), and
        # write ten such messages — well over the chunks-per-poll headroom.
        with open(jsonl_file, "a") as f:
            for _ in range(10):
                f.write(json.dumps({
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "A" * 3000}]},
                }) + "\n")

        await monitor._poll_once()
        # Many chunks within one ~instant poll → exactly one touch.
        assert session_mgr.touch_active.await_count == 1
        session_mgr.touch_active.assert_awaited_with("ch1")

    @pytest.mark.asyncio
    async def test_throttle_releases_after_window(
        self, monitor, session_mgr, tmp_path,
    ):
        """A second touch after the throttle window elapses fires again."""
        jsonl_file = tmp_path / "sess-123.jsonl"
        _make_jsonl_file(jsonl_file, [])

        binding = _make_binding()
        session_mgr.list_bindings.return_value = [binding]
        monitor.on_message(AsyncMock())
        monitor._find_jsonl_file = MagicMock(return_value=jsonl_file)
        # Drop the throttle to keep the test fast (functionally identical).
        monitor._TOUCH_THROTTLE = 0.05

        await monitor._poll_once()

        with open(jsonl_file, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "first"}]},
            }) + "\n")
        await monitor._poll_once()

        await asyncio.sleep(0.1)  # cross the throttle window

        with open(jsonl_file, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "second"}]},
            }) + "\n")
        await monitor._poll_once()

        assert session_mgr.touch_active.await_count == 2

    @pytest.mark.asyncio
    async def test_per_channel_throttle_independent(
        self, monitor, session_mgr, tmp_path,
    ):
        """Two channels touched in the same instant both get touch_active —
        the throttle is per-channel, not global."""
        f_a = tmp_path / "sess-a.jsonl"
        f_b = tmp_path / "sess-b.jsonl"
        _make_jsonl_file(f_a, [])
        _make_jsonl_file(f_b, [])

        binding_a = _make_binding(channel_id="chA", cli_session_id="sess-a")
        binding_b = _make_binding(channel_id="chB", cli_session_id="sess-b")
        session_mgr.list_bindings.return_value = [binding_a, binding_b]
        monitor.on_message(AsyncMock())

        def _find(b):
            return f_a if b.channel_id == "chA" else f_b
        monitor._find_jsonl_file = MagicMock(side_effect=_find)

        await monitor._poll_once()

        for f, txt in ((f_a, "alpha"), (f_b, "bravo")):
            with open(f, "a") as fh:
                fh.write(json.dumps({
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": txt}]},
                }) + "\n")

        await monitor._poll_once()
        assert session_mgr.touch_active.await_count == 2
        touched = {c.args[0] for c in session_mgr.touch_active.await_args_list}
        assert touched == {"chA", "chB"}

    @pytest.mark.asyncio
    async def test_integration_advances_last_active_at(self, tmp_path):
        """End-to-end through a real SessionManager: last_active_at advances
        after an outbound forward."""
        from gits.core.session import SessionManager

        real_mgr = SessionManager(state_dir=tmp_path)
        await real_mgr.bind(
            platform="discord",
            channel_id="chREAL",
            window_id="@1",
            window_name="test",
            work_dir=str(tmp_path),
        )
        # Backdate so the advance is unambiguous.
        binding = real_mgr.get_binding("chREAL")
        binding.last_active_at = time.time() - 3600
        before = binding.last_active_at

        monitor = JsonlMonitor(session_mgr=real_mgr, poll_interval=0.05)
        jsonl_file = tmp_path / "sess-real.jsonl"
        _make_jsonl_file(jsonl_file, [])

        # Inject a binding the monitor will actually poll. (The real mgr's
        # binding lacks cli_session_id; patch _find_jsonl_file to bypass.)
        fake_b = _make_binding(channel_id="chREAL")
        real_mgr.list_bindings = MagicMock(return_value=[fake_b])
        monitor._find_jsonl_file = MagicMock(return_value=jsonl_file)
        monitor.on_message(AsyncMock())

        await monitor._poll_once()  # prime

        with open(jsonl_file, "a") as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "ping"}]},
            }) + "\n")
        await monitor._poll_once()

        assert real_mgr.get_binding("chREAL").last_active_at > before

    @pytest.mark.asyncio
    async def test_helper_unit_throttle(self, monitor, session_mgr):
        """Direct unit test of the throttle helper — both call sites (JSONL
        and OpenCode) route through it, so this pins the contract."""
        monitor._TOUCH_THROTTLE = 1000.0  # effectively disable to test sequencing

        await monitor._touch_active_throttled("chX")
        await monitor._touch_active_throttled("chX")  # throttled
        await monitor._touch_active_throttled("chY")  # different channel → fires
        assert session_mgr.touch_active.await_count == 2
        called_channels = [
            c.args[0] for c in session_mgr.touch_active.await_args_list
        ]
        assert called_channels == ["chX", "chY"]

    @pytest.mark.asyncio
    async def test_opencode_path_also_touches(
        self, monitor, session_mgr, tmp_path, monkeypatch,
    ):
        """OpenCode forwards are also outbound Discord POSTs — same bug, same
        fix as the JSONL path. Verifies the wiring at line ~809."""
        # Build the directory chain the method expects under a fake $HOME.
        fake_home = tmp_path / "home"
        oc_dir = fake_home / ".local" / "share" / "opencode"
        oc_dir.mkdir(parents=True)
        (oc_dir / "opencode.db").touch()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        # Stub the SQLite reader — first poll is snapshot (returns []), second
        # returns new text. The method tracks per-session timestamps internally.
        reads = iter([
            ([], 0),
            (["opencode reply"], 1),
        ])
        monkeypatch.setattr(
            JsonlMonitor, "_read_opencode_db",
            staticmethod(lambda *a, **kw: next(reads)),
        )

        binding = _make_binding(channel_id="chOC", cli_session_id="sess-oc")
        binding.coding_cli = "opencode"
        monitor.on_message(AsyncMock())

        await monitor._check_opencode_binding(binding)  # snapshot
        session_mgr.touch_active.assert_not_called()

        await monitor._check_opencode_binding(binding)  # new content → touch
        session_mgr.touch_active.assert_awaited_once_with("chOC")
