"""Tests for terminal_parser -- Claude Code UI detection and prompt parsing."""

from __future__ import annotations

import textwrap

import pytest

from gits.core.terminal_parser import (
    InteractiveUIContent,
    PromptInfo,
    PromptOption,
    extract_interactive_content,
    extract_prompt_options,
    is_interactive_ui,
    parse_status_line,
    strip_pane_chrome,
)

# ---------------------------------------------------------------------------
# Realistic pane captures (test fixtures)
# ---------------------------------------------------------------------------

PANE_PERMISSION_PROMPT = textwrap.dedent("""\
     Bash command

       tail -30 /tmp/gits_bot.log 2>&1
       Check latest bot logs

     Do you want to proceed?
     \u276f 1. Yes
       2. Yes, allow reading from tmp/ from this project
       3. No

     Esc to cancel \u00b7 Tab to amend \u00b7 ctrl+e to explain
""")

PANE_PERMISSION_PROMPT_NO_ESC = textwrap.dedent("""\
     Bash command

       tail -30 /tmp/gits_bot.log 2>&1

     Do you want to proceed?
     \u276f 1. Yes
       2. Yes, allow reading from tmp/ from this project
       3. No
""")

PANE_BUSY = textwrap.dedent("""\
    \u276f hi

    \u23fa \u4f60\u597d\uff01Bot \u8dd1\u7740\u5462

      Reading 1 file\u2026 (ctrl+o to expand)
      \u23bf  tail -30 /tmp/gits_bot.log 2>&1

    \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
""")

PANE_IDLE = textwrap.dedent("""\
    some output here

    \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    \u276f\u0020
    \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
      esc to interrupt
""")

PANE_SPINNER = textwrap.dedent("""\
    some output

    \u273b Generating response...
    \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
""")

PANE_EXIT_PLAN = textwrap.dedent("""\
    Would you like to proceed?
      1. Implement the feature
      2. Add tests
      3. Update docs

    Esc to cancel
""")

PANE_RESTORE_CHECKPOINT = textwrap.dedent("""\
    Restore the code to the state before the last edit?
      File: src/main.py
      Changes: 15 lines added, 3 removed

    Enter to continue
""")

PANE_EDIT_APPROVAL = textwrap.dedent("""\
     Edit

       src/gits/core/engine.py

     Do you want to make this edit?
     \u276f 1. Yes
       2. Yes, and don't ask again for this file
       3. No

     Esc to cancel
""")

PANE_CREATE_FILE = textwrap.dedent("""\
     Write

       src/gits/core/new_module.py

     Do you want to create src/gits/core/new_module.py?
     \u276f 1. Yes
       2. No

     Esc to cancel
""")

PANE_SETTINGS = textwrap.dedent("""\
    Settings: General | Usage | Permissions   tab to cycle
      Model: claude-opus-4-6-20250514
      Theme: dark

    Esc to cancel
""")


# ---------------------------------------------------------------------------
# is_interactive_ui
# ---------------------------------------------------------------------------


class TestIsInteractiveUI:
    def test_permission_prompt(self) -> None:
        assert is_interactive_ui(PANE_PERMISSION_PROMPT) is True

    def test_permission_prompt_no_esc(self) -> None:
        assert is_interactive_ui(PANE_PERMISSION_PROMPT_NO_ESC) is True

    def test_exit_plan(self) -> None:
        assert is_interactive_ui(PANE_EXIT_PLAN) is True

    def test_restore_checkpoint(self) -> None:
        assert is_interactive_ui(PANE_RESTORE_CHECKPOINT) is True

    def test_settings(self) -> None:
        assert is_interactive_ui(PANE_SETTINGS) is True

    def test_idle_not_interactive(self) -> None:
        assert is_interactive_ui(PANE_IDLE) is False

    def test_busy_not_interactive(self) -> None:
        assert is_interactive_ui(PANE_BUSY) is False

    def test_empty_not_interactive(self) -> None:
        assert is_interactive_ui("") is False

    def test_plain_text_not_interactive(self) -> None:
        assert is_interactive_ui("Hello world\nHow are you?") is False

    def test_edit_approval(self) -> None:
        assert is_interactive_ui(PANE_EDIT_APPROVAL) is True

    def test_create_file(self) -> None:
        assert is_interactive_ui(PANE_CREATE_FILE) is True


# ---------------------------------------------------------------------------
# extract_interactive_content
# ---------------------------------------------------------------------------


class TestExtractInteractiveContent:
    def test_permission_prompt_name(self) -> None:
        result = extract_interactive_content(PANE_PERMISSION_PROMPT)
        assert result is not None
        assert result.name == "PermissionPrompt"

    def test_permission_prompt_includes_question(self) -> None:
        result = extract_interactive_content(PANE_PERMISSION_PROMPT)
        assert result is not None
        assert "Do you want to proceed?" in result.content

    def test_permission_prompt_includes_esc(self) -> None:
        result = extract_interactive_content(PANE_PERMISSION_PROMPT)
        assert result is not None
        assert "Esc to cancel" in result.content

    def test_exit_plan_name(self) -> None:
        result = extract_interactive_content(PANE_EXIT_PLAN)
        assert result is not None
        assert result.name == "ExitPlanMode"

    def test_restore_checkpoint_name(self) -> None:
        result = extract_interactive_content(PANE_RESTORE_CHECKPOINT)
        assert result is not None
        assert result.name == "RestoreCheckpoint"

    def test_settings_name(self) -> None:
        result = extract_interactive_content(PANE_SETTINGS)
        assert result is not None
        assert result.name == "Settings"

    def test_none_for_empty(self) -> None:
        assert extract_interactive_content("") is None

    def test_none_for_plain_text(self) -> None:
        assert extract_interactive_content("just some text\nnothing special") is None

    def test_permission_no_esc_uses_numbered_fallback(self) -> None:
        result = extract_interactive_content(PANE_PERMISSION_PROMPT_NO_ESC)
        assert result is not None
        assert result.name == "PermissionPrompt"

    def test_edit_approval_name(self) -> None:
        result = extract_interactive_content(PANE_EDIT_APPROVAL)
        assert result is not None
        # Could match PermissionPrompt (for "Do you want to make this edit")
        assert result.name in ("PermissionPrompt", "BashApproval")

    def test_separators_shortened(self) -> None:
        long_sep = "\u2500" * 66
        text = f"Do you want to proceed?\n  option a\n  option b\n{long_sep}\nEsc to cancel"
        result = extract_interactive_content(text)
        assert result is not None
        assert "\u2500" * 66 not in result.content
        assert "\u2500\u2500\u2500\u2500\u2500" in result.content


# ---------------------------------------------------------------------------
# parse_status_line
# ---------------------------------------------------------------------------


class TestParseStatusLine:
    def test_spinner_detected(self) -> None:
        result = parse_status_line(PANE_SPINNER)
        assert result == "busy"

    def test_idle_no_spinner(self) -> None:
        assert parse_status_line(PANE_IDLE) == "idle"

    def test_empty_pane(self) -> None:
        assert parse_status_line("") is None

    def test_no_chrome(self) -> None:
        assert parse_status_line("just text\nno chrome here") is None

    def test_various_spinners(self) -> None:
        sep = "\u2500" * 30
        for spinner in ["\u00b7", "\u273b", "\u273d", "\u2736", "\u2733", "\u2722"]:
            text = f"output\n{spinner} Working...\n{sep}\n"
            result = parse_status_line(text)
            assert result == "busy"

    def test_blank_lines_above_chrome(self) -> None:
        sep = "\u2500" * 30
        text = f"output\n\u273b Reading files\n\n{sep}\n"
        result = parse_status_line(text)
        assert result == "busy"


# ---------------------------------------------------------------------------
# strip_pane_chrome
# ---------------------------------------------------------------------------


class TestStripPaneChrome:
    def test_strips_chrome(self) -> None:
        sep = "\u2500" * 30
        lines = ["line 1", "line 2", sep, "\u276f ", sep, "  esc to interrupt"]
        result = strip_pane_chrome(lines)
        assert result == ["line 1", "line 2"]

    def test_no_chrome_returns_all(self) -> None:
        lines = ["line 1", "line 2", "line 3"]
        assert strip_pane_chrome(lines) == lines

    def test_empty_list(self) -> None:
        assert strip_pane_chrome([]) == []

    def test_short_dashes_not_chrome(self) -> None:
        lines = ["line 1", "\u2500\u2500\u2500", "line 3"]
        assert strip_pane_chrome(lines) == lines

    def test_chrome_only_in_last_10(self) -> None:
        """Chrome detection only looks at last 10 lines."""
        sep = "\u2500" * 30
        many_lines = [f"line {i}" for i in range(20)] + [sep, "\u276f "]
        result = strip_pane_chrome(many_lines)
        assert len(result) == 20


# ---------------------------------------------------------------------------
# extract_prompt_options
# ---------------------------------------------------------------------------


class TestExtractPromptOptions:
    def test_basic_permission_prompt(self) -> None:
        result = extract_prompt_options(PANE_PERMISSION_PROMPT)
        assert result is not None
        assert len(result.options) == 3
        assert result.options[0] == PromptOption(number=1, label="Yes")
        assert result.options[1] == PromptOption(
            number=2, label="Yes, allow reading from tmp/ from this project"
        )
        assert result.options[2] == PromptOption(number=3, label="No")

    def test_tool_context_bash(self) -> None:
        result = extract_prompt_options(PANE_PERMISSION_PROMPT)
        assert result is not None
        assert "Bash command" in result.tool_context
        assert "tail -30" in result.tool_context

    def test_edit_approval_options(self) -> None:
        result = extract_prompt_options(PANE_EDIT_APPROVAL)
        assert result is not None
        assert len(result.options) == 3
        assert result.options[0].label == "Yes"
        assert "don't ask again" in result.options[1].label

    def test_create_file_options(self) -> None:
        result = extract_prompt_options(PANE_CREATE_FILE)
        assert result is not None
        assert len(result.options) == 2
        assert result.options[0].label == "Yes"
        assert result.options[1].label == "No"

    def test_tool_context_write(self) -> None:
        result = extract_prompt_options(PANE_CREATE_FILE)
        assert result is not None
        assert "Write" in result.tool_context

    def test_no_options_returns_none(self) -> None:
        assert extract_prompt_options("just text\nno prompts here") is None

    def test_empty_returns_none(self) -> None:
        assert extract_prompt_options("") is None

    def test_prompt_no_esc(self) -> None:
        result = extract_prompt_options(PANE_PERMISSION_PROMPT_NO_ESC)
        assert result is not None
        assert len(result.options) == 3

    def test_option_with_cursor_marker(self) -> None:
        text = textwrap.dedent("""\
            Do you want to proceed?
            \u276f 1. Yes
              2. No

            Esc to cancel
        """)
        result = extract_prompt_options(text)
        assert result is not None
        assert result.options[0].label == "Yes"
        assert result.options[1].label == "No"

    def test_options_without_cursor_marker(self) -> None:
        text = textwrap.dedent("""\
            Do you want to proceed?
              1. Yes
              2. No

            Esc to cancel
        """)
        result = extract_prompt_options(text)
        assert result is not None
        assert len(result.options) == 2


# ---------------------------------------------------------------------------
# Integration / edge cases
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_busy_pane_not_interactive_but_has_chrome(self) -> None:
        """Busy pane has chrome but is not an interactive UI."""
        assert is_interactive_ui(PANE_BUSY) is False
        # But it doesn't have a spinner *right* above chrome in this fixture
        # (the spinner would appear on the status line)

    def test_idle_pane_has_chrome(self) -> None:
        lines = PANE_IDLE.splitlines()
        stripped = strip_pane_chrome(lines)
        assert len(stripped) < len(lines)

    def test_bash_approval_detected(self) -> None:
        text = textwrap.dedent("""\
            Bash command
              rm -rf /tmp/test

            Esc to cancel
        """)
        result = extract_interactive_content(text)
        assert result is not None
        assert result.name == "BashApproval"

    def test_plan_mode_with_ctrl_g(self) -> None:
        text = textwrap.dedent("""\
            Would you like to proceed?
              Step 1: Do this
              Step 2: Do that

            ctrl-g to edit in vim
        """)
        result = extract_interactive_content(text)
        assert result is not None
        assert result.name == "ExitPlanMode"

    def test_plan_mode_with_claude_prefix(self) -> None:
        text = textwrap.dedent("""\
            Claude has written up a plan for this change.
              Step 1: Do this
              Step 2: Do that

            Esc to cancel
        """)
        result = extract_interactive_content(text)
        assert result is not None
        assert result.name == "ExitPlanMode"

    def test_select_model_settings(self) -> None:
        text = textwrap.dedent("""\
            Select model
              claude-opus-4-6-20250514
              claude-sonnet-4-20250514

            Type to filter
        """)
        result = extract_interactive_content(text)
        assert result is not None
        assert result.name == "Settings"

    def test_extract_options_preserves_order(self) -> None:
        text = textwrap.dedent("""\
            Do you want to proceed?
            \u276f 1. Alpha
              2. Beta
              3. Gamma
              4. Delta

            Esc to cancel
        """)
        result = extract_prompt_options(text)
        assert result is not None
        labels = [o.label for o in result.options]
        assert labels == ["Alpha", "Beta", "Gamma", "Delta"]

    def test_tool_context_empty_when_no_tool(self) -> None:
        text = textwrap.dedent("""\
            Do you want to proceed?
            \u276f 1. Yes
              2. No

            Esc to cancel
        """)
        result = extract_prompt_options(text)
        assert result is not None
        assert result.tool_context == ""
