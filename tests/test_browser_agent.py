"""Tests for BrowserAgent — think-act loop with mocked dependencies."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# We need ANTHROPIC_API_KEY set before importing the agent module.
import os
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_claude_response(action: str, params: dict, reasoning: str = "test") -> MagicMock:
    """Return a mock anthropic response object carrying a JSON decision."""
    content_block = MagicMock()
    content_block.text = json.dumps({
        "action": action,
        "params": params,
        "reasoning": reasoning,
    })
    response = MagicMock()
    response.content = [content_block]
    return response


def _make_db(tmp_path: Path):
    """Return a real GitsDB backed by a temp SQLite file."""
    from gits.storage.sqlite import GitsDB
    return GitsDB(db_path=tmp_path / "test.db")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBrowserAgentNavigateClickDone:
    """Agent runs navigate → click → done (3 steps)."""

    async def test_three_step_loop(self, tmp_path):
        from gits.adapters.browser.agent import BrowserAgent
        from gits.storage.sqlite import GitsDB, TaskRepo

        # Sequence: navigate, click, done
        claude_responses = [
            _make_claude_response("navigate", {"url": "https://example.com"}, "go there"),
            _make_claude_response("click", {"ref": "e1", "label": "Submit"}, "click submit"),
            _make_claude_response("done", {"summary": "All done."}, "finished"),
        ]

        notify_events = []

        async def notify_cb(tid, event, data):
            notify_events.append((event, data))

        async with _make_db(tmp_path) as db:
            task_id = await TaskRepo(db.conn).create(goal="Test goal", profile="Test Profile")

            with (
                patch("gits.adapters.browser.agent.AsyncAnthropic") as mock_anthr,
                patch("gits.adapters.browser.openclaw.snapshot", new=AsyncMock(return_value=[])),
                patch("gits.adapters.browser.openclaw.navigate", new=AsyncMock(
                    return_value=MagicMock(url="https://example.com", title="Example")
                )),
                patch("gits.adapters.browser.openclaw.click", new=AsyncMock(
                    return_value=MagicMock(ok=True)
                )),
            ):
                mock_client = MagicMock()
                mock_client.messages.create = AsyncMock(side_effect=claude_responses)
                mock_anthr.return_value = mock_client

                agent = BrowserAgent(db=db, profile="Test Profile", notify_cb=notify_cb)
                result = await agent.run(task_id=task_id, goal="Test goal")

        assert result["status"] == "done"
        assert result["summary"] == "All done."

        # Check notify events: 3 step events + 1 done event
        step_events = [e for e in notify_events if e[0] == "step"]
        assert len(step_events) == 3
        assert step_events[0][1]["action"] == "navigate"
        assert step_events[1][1]["action"] == "click"
        assert step_events[2][1]["action"] == "done"

        done_events = [e for e in notify_events if e[0] == "done"]
        assert len(done_events) == 1
        assert done_events[0][1]["summary"] == "All done."


class TestBrowserAgentAskUser:
    """Agent handles ask_user → task becomes needs_review."""

    async def test_ask_user_sets_needs_review(self, tmp_path):
        from gits.adapters.browser.agent import BrowserAgent
        from gits.storage.sqlite import GitsDB, MemoryRepo, TaskRepo

        claude_responses = [
            _make_claude_response(
                "ask_user",
                {"message": "What is the login password?"},
                "need credentials",
            ),
        ]

        notify_events = []

        async def notify_cb(tid, event, data):
            notify_events.append((event, data))

        async with _make_db(tmp_path) as db:
            task_id = await TaskRepo(db.conn).create(goal="Login to site", profile="P")

            with (
                patch("gits.adapters.browser.agent.AsyncAnthropic") as mock_anthr,
                patch("gits.adapters.browser.openclaw.snapshot", new=AsyncMock(return_value=[])),
            ):
                mock_client = MagicMock()
                mock_client.messages.create = AsyncMock(side_effect=claude_responses)
                mock_anthr.return_value = mock_client

                agent = BrowserAgent(db=db, profile="P", notify_cb=notify_cb)
                result = await agent.run(task_id=task_id, goal="Login to site")

            # Verify task status
            task = await TaskRepo(db.conn).get(task_id)
            assert task["status"] == "needs_review"

            # Verify hitl_message stored in observations
            hitl = await MemoryRepo(db.conn).get(task_id, "hitl_message")
            assert hitl == "What is the login password?"

        assert result["status"] == "needs_review"
        assert result["message"] == "What is the login password?"

        ask_events = [e for e in notify_events if e[0] == "ask_user"]
        assert len(ask_events) == 1


class TestBrowserAgentArtifactSaving:
    """Agent saves artifact to correct path via save_artifact action."""

    async def test_save_artifact_written_to_disk(self, tmp_path):
        from gits.adapters.browser.agent import BrowserAgent
        from gits.storage.sqlite import ArtifactRepo, GitsDB, TaskRepo

        artifact_content = "col1,col2\nval1,val2\n"
        claude_responses = [
            _make_claude_response(
                "save_artifact",
                {"type": "csv", "filename": "results.csv", "content": artifact_content},
                "saving data",
            ),
            _make_claude_response("done", {"summary": "Artifact saved."}, "done"),
        ]

        fake_home = tmp_path / "home"
        fake_home.mkdir()

        async with _make_db(tmp_path) as db:
            task_id = await TaskRepo(db.conn).create(goal="Extract data", profile="P")

            with (
                patch("gits.adapters.browser.agent.AsyncAnthropic") as mock_anthr,
                patch("gits.adapters.browser.openclaw.snapshot", new=AsyncMock(return_value=[])),
                patch("gits.adapters.browser.agent.Path.home", return_value=fake_home),
            ):
                mock_client = MagicMock()
                mock_client.messages.create = AsyncMock(side_effect=claude_responses)
                mock_anthr.return_value = mock_client

                agent = BrowserAgent(db=db, profile="P", notify_cb=None)
                result = await agent.run(task_id=task_id, goal="Extract data")

            # Check artifact recorded in DB
            artifacts = await ArtifactRepo(db.conn).list_for_task(task_id)
            assert len(artifacts) == 1
            assert artifacts[0]["filename"] == "results.csv"
            assert artifacts[0]["type"] == "csv"

        # Check file on disk
        artifact_path = fake_home / ".gits" / "artifacts" / task_id / "results.csv"
        assert artifact_path.exists()
        assert artifact_path.read_text() == artifact_content

        assert result["status"] == "done"


class TestBrowserAgentMaxSteps:
    """Agent stops at max_steps and sets task to needs_review."""

    async def test_max_steps_reached(self, tmp_path):
        from gits.adapters.browser.agent import BrowserAgent
        from gits.storage.sqlite import GitsDB, TaskRepo

        # Always respond with a benign navigate action (never done).
        navigate_response = _make_claude_response(
            "navigate", {"url": "https://example.com"}, "keep going"
        )

        async with _make_db(tmp_path) as db:
            task_id = await TaskRepo(db.conn).create(goal="Infinite loop", profile="P")

            with (
                patch("gits.adapters.browser.agent.AsyncAnthropic") as mock_anthr,
                patch("gits.adapters.browser.openclaw.snapshot", new=AsyncMock(return_value=[])),
                patch("gits.adapters.browser.openclaw.navigate", new=AsyncMock(
                    return_value=MagicMock(url="https://example.com", title="Ex")
                )),
            ):
                # Return the same response object repeatedly via side_effect list.
                mock_client = MagicMock()
                mock_client.messages.create = AsyncMock(return_value=navigate_response)
                mock_anthr.return_value = mock_client

                agent = BrowserAgent(db=db, profile="P", notify_cb=None)
                result = await agent.run(task_id=task_id, goal="Infinite loop", max_steps=3)

            # Task should be needs_review after hitting max_steps
            task = await TaskRepo(db.conn).get(task_id)
            assert task["status"] == "needs_review"

        assert result["status"] == "needs_review"
        assert result["reason"] == "max_steps"
