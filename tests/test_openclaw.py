"""Tests for src/gits/adapters/browser/openclaw.py.

All tests mock asyncio.create_subprocess_exec — no real openclaw calls.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gits.adapters.browser.openclaw import (
    ActionResult,
    ElementRef,
    NavResult,
    OpenClawError,
    _parse_snapshot,
    click,
    evaluate,
    list_profiles,
    navigate,
    snapshot,
    type_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Return a mock asyncio subprocess that yields the given output."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(
        return_value=(stdout.encode(), stderr.encode())
    )
    proc.kill = MagicMock()
    return proc


def _patch_exec(proc):
    return patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    )


# ---------------------------------------------------------------------------
# navigate
# ---------------------------------------------------------------------------

class TestNavigate:
    @pytest.mark.asyncio
    async def test_parses_json_output(self):
        payload = json.dumps({"url": "https://example.com", "title": "Example Domain"})
        proc = _mock_proc(stdout=payload)
        with _patch_exec(proc):
            result = await navigate("default", "https://example.com")
        assert isinstance(result, NavResult)
        assert result.url == "https://example.com"
        assert result.title == "Example Domain"

    @pytest.mark.asyncio
    async def test_missing_title_defaults_to_empty(self):
        payload = json.dumps({"url": "https://example.com"})
        proc = _mock_proc(stdout=payload)
        with _patch_exec(proc):
            result = await navigate("default", "https://example.com")
        assert result.title == ""

    @pytest.mark.asyncio
    async def test_raises_on_nonzero_exit(self):
        proc = _mock_proc(stderr="connection refused", returncode=1)
        with _patch_exec(proc):
            with pytest.raises(OpenClawError) as exc_info:
                await navigate("default", "https://example.com")
        assert exc_info.value.returncode == 1
        assert "connection refused" in str(exc_info.value)


# ---------------------------------------------------------------------------
# snapshot / _parse_snapshot
# ---------------------------------------------------------------------------

SAMPLE_SNAPSHOT = """\
- button "Search" [ref=e1]
- link "Home page" [ref=e2]
- textbox "Email" [ref=e3]
- checkbox "Remember me" [ref=e4] [checked]
- combobox "Country" [ref=e5]
"""

SAMPLE_SNAPSHOT_COMPACT = """\
button "Search" [ref=e1]
link "Home page" [ref=e2]
textbox "Email" [ref=e3]
"""


class TestParseSnapshot:
    def test_basic_parsing(self):
        elements = _parse_snapshot(SAMPLE_SNAPSHOT)
        assert len(elements) == 5
        assert elements[0] == ElementRef(role="button", label="Search", ref="e1")
        assert elements[1] == ElementRef(role="link", label="Home page", ref="e2")
        assert elements[2] == ElementRef(role="textbox", label="Email", ref="e3")
        assert elements[3] == ElementRef(role="checkbox", label="Remember me", ref="e4")
        assert elements[4] == ElementRef(role="combobox", label="Country", ref="e5")

    def test_compact_lines_without_dash(self):
        elements = _parse_snapshot(SAMPLE_SNAPSHOT_COMPACT)
        assert len(elements) == 3
        assert elements[0].role == "button"
        assert elements[0].ref == "e1"

    def test_empty_text_returns_empty_list(self):
        assert _parse_snapshot("") == []

    def test_lines_without_ref_are_skipped(self):
        text = "heading \"Welcome\"\n- button \"Go\" [ref=e1]\n"
        elements = _parse_snapshot(text)
        assert len(elements) == 1
        assert elements[0].ref == "e1"

    def test_no_label_element(self):
        text = "- button [ref=e10]\n"
        elements = _parse_snapshot(text)
        assert len(elements) == 1
        assert elements[0].role == "button"
        assert elements[0].label == ""
        assert elements[0].ref == "e10"

    def test_numeric_ref(self):
        text = '- link "Docs" [ref=42]\n'
        elements = _parse_snapshot(text)
        assert len(elements) == 1
        assert elements[0].ref == "42"


class TestSnapshot:
    @pytest.mark.asyncio
    async def test_returns_parsed_elements(self):
        proc = _mock_proc(stdout=SAMPLE_SNAPSHOT)
        with _patch_exec(proc):
            elements = await snapshot("default")
        assert len(elements) == 5
        assert all(isinstance(e, ElementRef) for e in elements)

    @pytest.mark.asyncio
    async def test_raises_on_nonzero_exit(self):
        proc = _mock_proc(stderr="browser not running", returncode=1)
        with _patch_exec(proc):
            with pytest.raises(OpenClawError):
                await snapshot("default")


# ---------------------------------------------------------------------------
# click
# ---------------------------------------------------------------------------

class TestClick:
    @pytest.mark.asyncio
    async def test_success(self):
        proc = _mock_proc(stdout="clicked ref e1 on https://example.com")
        with _patch_exec(proc):
            result = await click("default", "e1")
        assert result == ActionResult(ok=True)

    @pytest.mark.asyncio
    async def test_error_returns_action_result_with_error(self):
        proc = _mock_proc(stderr="element not found", returncode=1)
        with _patch_exec(proc):
            result = await click("default", "e99")
        assert result.ok is False
        assert result.error is not None
        assert "element not found" in result.error

    @pytest.mark.asyncio
    async def test_passes_correct_args(self):
        proc = _mock_proc()
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec:
            await click("myprofile", "e5")
        args = mock_exec.call_args[0]
        assert "click" in args
        assert "e5" in args
        assert "--browser-profile" in args
        assert "myprofile" in args


# ---------------------------------------------------------------------------
# type_text
# ---------------------------------------------------------------------------

class TestTypeText:
    @pytest.mark.asyncio
    async def test_success(self):
        proc = _mock_proc(stdout="typed into ref e3")
        with _patch_exec(proc):
            result = await type_text("default", "e3", "hello world")
        assert result == ActionResult(ok=True)

    @pytest.mark.asyncio
    async def test_error_returns_action_result(self):
        proc = _mock_proc(stderr="ref not found", returncode=2)
        with _patch_exec(proc):
            result = await type_text("default", "e99", "text")
        assert result.ok is False
        assert "ref not found" in (result.error or "")

    @pytest.mark.asyncio
    async def test_passes_text_as_separate_arg(self):
        proc = _mock_proc()
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec:
            await type_text("myprofile", "e3", "hello")
        args = mock_exec.call_args[0]
        assert "type" in args
        assert "e3" in args
        assert "hello" in args


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------

class TestEvaluate:
    @pytest.mark.asyncio
    async def test_returns_js_result_string(self):
        proc = _mock_proc(stdout='"hello world"\n')
        with _patch_exec(proc):
            result = await evaluate("default", "() => 'hello world'")
        assert result == '"hello world"'

    @pytest.mark.asyncio
    async def test_raises_on_nonzero_exit(self):
        proc = _mock_proc(stderr="eval error", returncode=1)
        with _patch_exec(proc):
            with pytest.raises(OpenClawError):
                await evaluate("default", "() => throw new Error()")

    @pytest.mark.asyncio
    async def test_passes_fn_flag(self):
        proc = _mock_proc(stdout="null\n")
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec:
            await evaluate("default", "() => null")
        args = mock_exec.call_args[0]
        assert "--fn" in args
        idx = args.index("--fn")
        assert args[idx + 1] == "() => null"


# ---------------------------------------------------------------------------
# OpenClawError
# ---------------------------------------------------------------------------

class TestOpenClawError:
    @pytest.mark.asyncio
    async def test_returncode_is_preserved(self):
        proc = _mock_proc(stderr="fatal error", returncode=127)
        with _patch_exec(proc):
            with pytest.raises(OpenClawError) as exc_info:
                await navigate("default", "https://x.com")
        assert exc_info.value.returncode == 127

    @pytest.mark.asyncio
    async def test_error_message_contains_stderr(self):
        # navigate does not swallow errors, so use it to test error message content
        proc = _mock_proc(stderr="No such profile: badprofile", returncode=1)
        with _patch_exec(proc):
            with pytest.raises(OpenClawError) as exc_info:
                await navigate("badprofile", "https://x.com")
        assert "No such profile: badprofile" in str(exc_info.value)


# ---------------------------------------------------------------------------
# list_profiles
# ---------------------------------------------------------------------------

class TestListProfiles:
    @pytest.mark.asyncio
    async def test_returns_profile_names(self):
        payload = json.dumps({
            "profiles": [
                {"name": "default", "cdpPort": 18800, "running": False},
                {"name": "work", "cdpPort": 18801, "running": True},
            ]
        })
        proc = _mock_proc(stdout=payload)
        with _patch_exec(proc):
            names = await list_profiles()
        assert names == ["default", "work"]

    @pytest.mark.asyncio
    async def test_empty_profiles(self):
        payload = json.dumps({"profiles": []})
        proc = _mock_proc(stdout=payload)
        with _patch_exec(proc):
            names = await list_profiles()
        assert names == []
