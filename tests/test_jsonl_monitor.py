"""Tests for JsonlMonitor — JSONL polling, parsing, and callback firing."""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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


class TestSessionSwitchGuard:
    """Background jobs must not hijack a channel's session when its file is still present."""

    @pytest.mark.asyncio
    async def test_proposed_session_rejected_when_current_file_exists(self, tmp_path):
        projects = tmp_path / "projects"
        current_sid = "current-session-111"
        bg_sid = "background-session-999"

        # Create the current session's JSONL file
        project_dir = projects / "-tmp-guard"
        project_dir.mkdir(parents=True)
        (project_dir / f"{current_sid}.jsonl").write_text(
            json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "real work"}]},
            }) + "\n"
        )

        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(
            channel_id="ch-guard",
            cli_session_id=current_sid,
            work_dir="/tmp/guard",
        )
        binding.window_id = "@6"
        session_mgr.list_bindings.return_value = [binding]

        monitor = JsonlMonitor(
            session_mgr=session_mgr,
            poll_interval=0.05,
            projects_path=projects,
        )
        # Background job wrote bg_sid into session_map for this window
        monitor._read_session_map = lambda: {
            "gits:@6": {"session_id": bg_sid, "cwd": "/tmp/guard"}
        }

        await monitor._poll_once()

        # session_id must NOT have been updated — file still exists
        session_mgr.update_cli_session_id.assert_not_called()
        assert binding.cli_session_id == current_sid

    @pytest.mark.asyncio
    async def test_proposed_session_accepted_when_current_file_gone(self, tmp_path):
        projects = tmp_path / "projects"
        old_sid = "gone-session-000"
        new_sid = "new-session-111"

        # Create the new session's JSONL file (old file is absent)
        project_dir = projects / "-tmp-accept"
        project_dir.mkdir(parents=True)
        (project_dir / f"{new_sid}.jsonl").write_text(
            json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "fresh start"}]},
            }) + "\n"
        )
        # old_sid.jsonl is intentionally NOT created

        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(
            channel_id="ch-accept",
            cli_session_id=old_sid,
            work_dir="/tmp/accept",
        )
        binding.window_id = "@8"
        session_mgr.list_bindings.return_value = [binding]

        monitor = JsonlMonitor(
            session_mgr=session_mgr,
            poll_interval=0.05,
            projects_path=projects,
        )
        monitor._read_session_map = lambda: {
            "gits:@8": {"session_id": new_sid, "cwd": "/tmp/accept"}
        }

        await monitor._poll_once()

        # Old file is gone → new session accepted
        session_mgr.update_cli_session_id.assert_called_once_with("ch-accept", new_sid)
        assert binding.cli_session_id == new_sid


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


# -- Mtime cross-window contamination ----------------------------------------


class TestMtimeCrossWindowContamination:
    """mtime strategy must not cross-contaminate sessions between windows sharing a project dir."""

    @pytest.mark.asyncio
    async def test_mtime_overruled_when_session_map_disagrees(self, tmp_path):
        """mtime result for @28 that matches @27's session should be rejected;
        session_map fallback must correct @28 to its own session."""
        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        ch27 = _make_binding(channel_id="ch-27", cli_session_id=None, work_dir="/tmp/ai4stock")
        ch27.window_id = "@27"
        ch28 = _make_binding(channel_id="ch-28", cli_session_id=None, work_dir="/tmp/ai4stock")
        ch28.window_id = "@28"
        session_mgr.list_bindings.return_value = [ch27, ch28]

        session_map = {
            "gits:@27": {"session_id": "sess-27"},
            "gits:@28": {"session_id": "sess-28"},
        }

        monitor = JsonlMonitor(session_mgr=session_mgr, poll_interval=0.05)
        monitor._tmux = MagicMock()  # enable pane detection block
        monitor._read_session_map = lambda: session_map
        # mtime picks sess-27 for both windows (the bug scenario)
        monitor._detect_session_via_pane = AsyncMock(return_value=("sess-27", "mtime"))

        await monitor._poll_once()

        calls = {args[0]: args[1] for args, _ in session_mgr.update_cli_session_id.call_args_list}
        # ch-27: mtime result matches session_map → accepted
        assert calls.get("ch-27") == "sess-27"
        # ch-28: mtime result disagrees with session_map → session_map corrects it
        assert calls.get("ch-28") == "sess-28"
        # ch-28 must NOT have been updated to sess-27
        wrong_calls = [
            args for args, _ in session_mgr.update_cli_session_id.call_args_list
            if args[0] == "ch-28" and args[1] == "sess-27"
        ]
        assert wrong_calls == []

    @pytest.mark.asyncio
    async def test_mtime_overruled_when_binding_already_has_wrong_session(self, tmp_path):
        """If ch-28 already holds a session that mtime keeps returning (from a prior
        cross-window steal), session_map must correct it.

        Setup: two channels share the same project dir (triggers mtime_uncertain).
        The stolen session does NOT appear in session_map for any window — this is
        the realistic post-correction-cycle state where @27's session was already
        updated but ch-28 is stuck.  mtime_uncertain fires (session_map assigns @28
        to correct_sid but mtime says stolen_sid, and stolen_sid is absent from the
        map), so Step 2 bypasses the file-existence guard and corrects ch-28.
        """
        projects = tmp_path / "projects"
        stolen_sid = "stolen-sess"
        correct_sid = "correct-sess"

        # Create the stolen session's JSONL file (so file-existence guard would normally block
        # WITHOUT the mtime_uncertain bypass)
        project_dir = projects / "-tmp-ai4stock"
        project_dir.mkdir(parents=True)
        (project_dir / f"{stolen_sid}.jsonl").write_text("{}\n")

        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        # ch-27 and ch-28 share the same work_dir → triggers other_channels_same_dir
        ch27 = _make_binding(channel_id="ch-27", cli_session_id=stolen_sid, work_dir="/tmp/ai4stock")
        ch27.window_id = "@27"
        ch28 = _make_binding(channel_id="ch-28", cli_session_id=stolen_sid, work_dir="/tmp/ai4stock")
        ch28.window_id = "@28"
        session_mgr.list_bindings.return_value = [ch27, ch28]

        # @27 has no entry in session_map (e.g., already corrected in a previous cycle),
        # @28 is correctly assigned correct_sid.  stolen_sid does NOT appear as any
        # window's value → detected_in_map=False → mtime_uncertain fires for ch-28.
        session_map = {
            "gits:@28": {"session_id": correct_sid},
        }

        monitor = JsonlMonitor(
            session_mgr=session_mgr,
            poll_interval=0.05,
            projects_path=projects,
        )
        monitor._tmux = MagicMock()  # enable pane detection block
        monitor._read_session_map = lambda: session_map
        # mtime keeps returning stolen_sid for ch-28 (contamination persists)
        monitor._detect_session_via_pane = AsyncMock(return_value=(stolen_sid, "mtime"))

        await monitor._poll_once()

        # session_map must have corrected ch-28 to correct_sid
        calls = {args[0]: args[1] for args, _ in session_mgr.update_cli_session_id.call_args_list}
        assert calls.get("ch-28") == correct_sid, "ch-28 must be corrected to correct_sid"
        assert ch28.cli_session_id == correct_sid

    @pytest.mark.asyncio
    async def test_pane_file_detection_overrules_session_map(self, tmp_path):
        """pane_file strategy is high-confidence; it must win over session_map."""
        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(channel_id="ch-pf", cli_session_id=None, work_dir="/tmp/proj")
        binding.window_id = "@9"
        session_mgr.list_bindings.return_value = [binding]

        session_map = {"gits:@9": {"session_id": "sess-old"}}

        monitor = JsonlMonitor(session_mgr=session_mgr, poll_interval=0.05)
        monitor._tmux = MagicMock()  # enable pane detection block
        monitor._read_session_map = lambda: session_map
        monitor._detect_session_via_pane = AsyncMock(return_value=("sess-pane-fresh", "pane_file"))

        await monitor._poll_once()

        # pane_file result must have been applied
        session_mgr.update_cli_session_id.assert_called_once_with("ch-pf", "sess-pane-fresh")
        assert binding.cli_session_id == "sess-pane-fresh"

    @pytest.mark.asyncio
    async def test_mtime_accepted_when_session_map_has_no_entry(self, tmp_path):
        """mtime result is accepted when session_map has no entry for this window."""
        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(channel_id="ch-mt", cli_session_id=None, work_dir="/tmp/proj")
        binding.window_id = "@11"
        session_mgr.list_bindings.return_value = [binding]

        monitor = JsonlMonitor(session_mgr=session_mgr, poll_interval=0.05)
        monitor._tmux = MagicMock()  # enable pane detection block
        monitor._read_session_map = lambda: {}  # no session_map entries
        monitor._detect_session_via_pane = AsyncMock(return_value=("sess-mtime", "mtime"))

        await monitor._poll_once()

        # mtime accepted — no session_map entry to contradict it
        session_mgr.update_cli_session_id.assert_called_once_with("ch-mt", "sess-mtime")
        assert binding.cli_session_id == "sess-mtime"

    @pytest.mark.asyncio
    async def test_mtime_accepted_when_session_map_agrees(self, tmp_path):
        """mtime result is accepted when session_map says the same session for this window."""
        session_mgr = MagicMock()
        session_mgr.update_cli_session_id = AsyncMock()

        binding = _make_binding(channel_id="ch-agree", cli_session_id=None, work_dir="/tmp/proj")
        binding.window_id = "@12"
        session_mgr.list_bindings.return_value = [binding]

        session_map = {"gits:@12": {"session_id": "sess-match"}}

        monitor = JsonlMonitor(session_mgr=session_mgr, poll_interval=0.05)
        monitor._tmux = MagicMock()  # enable pane detection block
        monitor._read_session_map = lambda: session_map
        monitor._detect_session_via_pane = AsyncMock(return_value=("sess-match", "mtime"))

        await monitor._poll_once()

        # mtime matches session_map → accepted, no contradiction
        session_mgr.update_cli_session_id.assert_called_once_with("ch-agree", "sess-match")
        assert binding.cli_session_id == "sess-match"


# -- _find_claude_descendant: skip claude -p processes ----------------------


class TestFindClaudeDescendantSkipsPrint:
    """_find_claude_descendant must skip non-interactive claude -p invocations."""

    def test_skips_claude_p_process(self, tmp_path):
        """A claude process with -p in cmdline must be skipped."""
        # We can't fork real processes in unit tests, so we test by patching
        # the /proc filesystem reads.  The logic is: BFS from root_pid,
        # find child with comm="claude", read its cmdline, skip if -p present.
        #
        # We simulate two children:
        #   child 1001: comm="claude", cmdline has "-p" → skip
        #   child 1002: comm="claude", cmdline has no -p → return this one
        from unittest.mock import patch, mock_open
        import io

        read_calls: dict[str, bytes | str] = {
            "/proc/1000/task/1000/children": "1001 1002",
            "/proc/1001/comm": "claude\n",
            "/proc/1001/cmdline": b"claude\x00-p\x00some prompt\x00",
            "/proc/1001/task/1001/children": "",
            "/proc/1002/comm": "claude\n",
            "/proc/1002/cmdline": b"claude\x00--resume\x00sess-abc\x00",
            "/proc/1002/task/1002/children": "",
        }

        def fake_read_text(self_path):
            val = read_calls.get(str(self_path))
            if val is None:
                raise FileNotFoundError(str(self_path))
            if isinstance(val, bytes):
                raise TypeError("use read_bytes for this path")
            return val

        def fake_read_bytes(self_path):
            val = read_calls.get(str(self_path))
            if val is None:
                raise FileNotFoundError(str(self_path))
            if isinstance(val, str):
                return val.encode()
            return val

        with patch.object(Path, "read_text", fake_read_text), \
             patch.object(Path, "read_bytes", fake_read_bytes):
            result = JsonlMonitor._find_claude_descendant(1000)

        assert result == 1002, "Should skip claude -p (pid 1001) and return interactive claude (pid 1002)"

    def test_skips_claude_print_long_flag(self, tmp_path):
        """A claude process with --print in cmdline must be skipped."""
        from unittest.mock import patch

        read_calls: dict[str, bytes | str] = {
            "/proc/2000/task/2000/children": "2001",
            "/proc/2001/comm": "claude\n",
            "/proc/2001/cmdline": b"claude\x00--print\x00query\x00",
            "/proc/2001/task/2001/children": "",
        }

        def fake_read_text(self_path):
            val = read_calls.get(str(self_path))
            if val is None:
                raise FileNotFoundError(str(self_path))
            if isinstance(val, bytes):
                raise TypeError("bytes path")
            return val

        def fake_read_bytes(self_path):
            val = read_calls.get(str(self_path))
            if val is None:
                raise FileNotFoundError(str(self_path))
            if isinstance(val, str):
                return val.encode()
            return val

        with patch.object(Path, "read_text", fake_read_text), \
             patch.object(Path, "read_bytes", fake_read_bytes):
            result = JsonlMonitor._find_claude_descendant(2000)

        assert result is None, "Only claude --print present; no interactive claude → return None"

    def test_interactive_claude_returned(self, tmp_path):
        """A claude process without -p/-print flags is returned normally."""
        from unittest.mock import patch

        read_calls: dict[str, bytes | str] = {
            "/proc/3000/task/3000/children": "3001",
            "/proc/3001/comm": "claude\n",
            "/proc/3001/cmdline": b"claude\x00",
            "/proc/3001/task/3001/children": "",
        }

        def fake_read_text(self_path):
            val = read_calls.get(str(self_path))
            if val is None:
                raise FileNotFoundError(str(self_path))
            if isinstance(val, bytes):
                raise TypeError("bytes path")
            return val

        def fake_read_bytes(self_path):
            val = read_calls.get(str(self_path))
            if val is None:
                raise FileNotFoundError(str(self_path))
            if isinstance(val, str):
                return val.encode()
            return val

        with patch.object(Path, "read_text", fake_read_text), \
             patch.object(Path, "read_bytes", fake_read_bytes):
            result = JsonlMonitor._find_claude_descendant(3000)

        assert result == 3001
