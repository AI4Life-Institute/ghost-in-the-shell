"""Terminal output parser -- detects Claude Code UI elements in pane text.

Parses captured tmux pane content to detect:
  - Interactive UIs (AskUserQuestion, ExitPlanMode, Permission Prompt,
    RestoreCheckpoint) via regex-based UIPattern matching with top/bottom
    delimiters.
  - Status line (spinner characters + working text) by scanning from bottom up.
  - Prompt options (numbered choices like "1. Yes") for Discord button creation.

All Claude Code text patterns live here. To support a new UI type or
a changed Claude Code version, edit UI_PATTERNS / STATUS_SPINNERS.

Key functions: is_interactive_ui(), extract_interactive_content(),
parse_status_line(), strip_pane_chrome(), extract_prompt_options().
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class InteractiveUIContent:
    """Content extracted from an interactive UI."""

    content: str  # The extracted display content
    name: str = ""  # Pattern name that matched (e.g. "AskUserQuestion")


@dataclass(frozen=True)
class UIPattern:
    """A text-marker pair that delimits an interactive UI region.

    Extraction scans lines top-down: the first line matching any ``top`` pattern
    marks the start, the first subsequent line matching any ``bottom`` pattern
    marks the end.  Both boundary lines are included in the extracted content.

    ``top`` and ``bottom`` are tuples of compiled regexes -- any single match
    is sufficient.  This accommodates wording changes across Claude Code
    versions (e.g. a reworded confirmation prompt).
    """

    name: str  # Descriptive label (not used programmatically)
    top: tuple[re.Pattern[str], ...]
    bottom: tuple[re.Pattern[str], ...]
    min_gap: int = 2  # minimum lines between top and bottom (inclusive)


@dataclass
class PromptOption:
    """A single numbered option from a permission prompt."""

    number: int
    label: str


@dataclass
class PromptInfo:
    """Structured info extracted from a permission/approval prompt."""

    options: list[PromptOption] = field(default_factory=list)
    tool_context: str = ""  # e.g. "Bash command\n  tail -30 /tmp/..."


# -- UI pattern definitions (order matters -- first match wins) --------

UI_PATTERNS: list[UIPattern] = [
    UIPattern(
        name="ExitPlanMode",
        top=(
            re.compile(r"^\s*Would you like to proceed\?"),
            # v2.1.29+: longer prefix that may wrap across lines
            re.compile(r"^\s*Claude has written up a plan"),
        ),
        bottom=(
            re.compile(r"^\s*ctrl-g to edit in "),
            re.compile(r"^\s*Esc to (cancel|exit)"),
        ),
    ),
    UIPattern(
        name="AskUserQuestion",
        top=(re.compile(r"^\s*\u2190\s+[\u2610\u2714\u2612]"),),  # Multi-tab
        bottom=(),
        min_gap=1,
    ),
    UIPattern(
        name="AskUserQuestion",
        top=(re.compile(r"^\s*[\u2610\u2714\u2612]"),),  # Single-tab
        bottom=(re.compile(r"^\s*Enter to select"),),
        min_gap=1,
    ),
    UIPattern(
        name="PermissionPrompt",
        top=(
            re.compile(r"^\s*Do you want to proceed\?"),
            re.compile(r"^\s*Do you want to make this edit"),
            re.compile(r"^\s*Do you want to create \S"),
            re.compile(r"^\s*Do you want to delete \S"),
        ),
        bottom=(re.compile(r"^\s*Esc to cancel"),),
    ),
    UIPattern(
        # Permission menu with numbered choices (no "Esc to cancel" line)
        name="PermissionPrompt",
        top=(re.compile(r"^\s*\u276f\s*1\.\s*Yes"),),
        bottom=(),
        min_gap=2,
    ),
    UIPattern(
        # Bash command approval
        name="BashApproval",
        top=(
            re.compile(r"^\s*Bash command\s*$"),
            re.compile(r"^\s*This command requires approval"),
        ),
        bottom=(re.compile(r"^\s*Esc to cancel"),),
    ),
    UIPattern(
        name="RestoreCheckpoint",
        top=(re.compile(r"^\s*Restore the code"),),
        bottom=(re.compile(r"^\s*Enter to continue"),),
    ),
    UIPattern(
        name="Settings",
        top=(
            re.compile(r"^\s*Settings:.*tab to cycle"),
            re.compile(r"^\s*Select model"),
        ),
        bottom=(
            re.compile(r"Esc to cancel"),
            re.compile(r"Esc to exit"),
            re.compile(r"Enter to confirm"),
            re.compile(r"^\s*Type to filter"),
        ),
    ),
    # ── Codex CLI patterns ───────────────────────────────────────────
    UIPattern(
        # Codex approval prompt:
        #   Would you like to run the following command?
        #   ...
        #   › 1. Yes, proceed (y)
        #     2. Yes, and don't ask again ... (p)
        #     3. No, ... (esc)
        #   Press enter to confirm or esc to cancel
        name="CodexApproval",
        top=(re.compile(r"^\s*Would you like to run the following command\?"),),
        bottom=(re.compile(r"^\s*Press enter to confirm or esc to cancel"),),
    ),
    UIPattern(
        # Codex numbered choice menu (without "Would you like" preamble)
        name="CodexApproval",
        top=(re.compile(r"^\s*\u203a\s*1\.\s*Yes"),),
        bottom=(re.compile(r"^\s*Press enter to confirm"),),
        min_gap=2,
    ),
    # ── OpenCode patterns ────────────────────────────────────────────
    UIPattern(
        # OpenCode permission prompt:
        #   △ Permission required
        #     ← Access external directory /tmp
        #   ...
        #    Allow once   Allow always   Reject
        name="OpenCodePermission",
        top=(re.compile(r"^\s*\u25b3\s*Permission required"),),
        bottom=(
            re.compile(r"Allow once.*Allow always.*Reject"),
            re.compile(r"enter confirm"),
        ),
    ),
]


# -- Post-processing ---------------------------------------------------

_RE_LONG_DASH = re.compile(r"^─{5,}$")


def _shorten_separators(text: str) -> str:
    """Replace lines of 5+ dash characters with exactly five."""
    return "\n".join(
        "\u2500\u2500\u2500\u2500\u2500" if _RE_LONG_DASH.match(line) else line
        for line in text.split("\n")
    )


# -- Core extraction ----------------------------------------------------


def _try_extract(lines: list[str], pattern: UIPattern) -> InteractiveUIContent | None:
    """Try to extract content matching a single UI pattern.

    When ``pattern.bottom`` is empty, the region extends from the top marker
    to the last non-empty line.
    """
    top_idx: int | None = None
    bottom_idx: int | None = None

    for i, line in enumerate(lines):
        if top_idx is None:
            if any(p.search(line) for p in pattern.top):
                top_idx = i
        elif pattern.bottom and any(p.search(line) for p in pattern.bottom):
            bottom_idx = i
            break

    if top_idx is None:
        return None

    # No bottom patterns -> use last non-empty line as boundary
    if not pattern.bottom:
        for i in range(len(lines) - 1, top_idx, -1):
            if lines[i].strip():
                bottom_idx = i
                break

    if bottom_idx is None or bottom_idx - top_idx < pattern.min_gap:
        return None

    content = "\n".join(lines[top_idx : bottom_idx + 1]).rstrip()
    return InteractiveUIContent(content=_shorten_separators(content), name=pattern.name)


# -- Public API ---------------------------------------------------------


def extract_interactive_content(pane_text: str) -> InteractiveUIContent | None:
    """Extract content from an interactive UI in terminal output.

    Tries each UI pattern in declaration order; first match wins.
    Returns None if no recognizable interactive UI is found.
    """
    if not pane_text:
        return None

    lines = pane_text.strip().split("\n")
    for pattern in UI_PATTERNS:
        result = _try_extract(lines, pattern)
        if result:
            return result
    return None


def is_interactive_ui(pane_text: str) -> bool:
    """Check if terminal currently shows an interactive UI."""
    return extract_interactive_content(pane_text) is not None


# -- Status line parsing ------------------------------------------------

# Spinner characters Claude Code uses in its status line
STATUS_SPINNERS = frozenset(["\u00b7", "\u273b", "\u273d", "\u2736", "\u2733", "\u2722"])


def parse_status_line(pane_text: str) -> str | None:
    """Extract the Claude Code status line from terminal output.

    The status line (spinner + working text) appears immediately above
    the chrome separator (a full line of dash characters).  We locate
    the separator first, then check the line just above it.

    Returns:
        "idle"    -- chrome visible, prompt line (``>``) present: Claude waiting
        "busy"    -- spinner detected above chrome: Claude is working
        None      -- no chrome separator found
    """
    if not pane_text:
        return None

    lines = pane_text.split("\n")

    # Find the chrome separator: topmost ---- line in the last 10 lines
    chrome_idx: int | None = None
    search_start = max(0, len(lines) - 10)
    for i in range(search_start, len(lines)):
        stripped = lines[i].strip()
        if len(stripped) >= 20 and all(c == "\u2500" for c in stripped):
            chrome_idx = i
            break

    if chrome_idx is None:
        return None  # No chrome visible

    # Check lines just above the separator (skip blanks, up to 4 lines)
    for i in range(chrome_idx - 1, max(chrome_idx - 5, -1), -1):
        line = lines[i].strip()
        if not line:
            continue
        if line[0] in STATUS_SPINNERS:
            return line[1:].strip()
        # First non-empty line above separator isn't a spinner -> no status
        return None
    return None


# -- Pane chrome stripping ----------------------------------------------


def strip_pane_chrome(lines: list[str]) -> list[str]:
    """Strip Claude Code's bottom chrome (prompt area + status bar).

    The bottom of the pane looks like::

        ----------  (separator)
        >           (prompt)
        ----------  (separator)
          [Opus 4.6] Context: 34%

    This function finds the topmost separator in the last 10 lines
    and strips everything from there down.
    """
    search_start = max(0, len(lines) - 10)
    for i in range(search_start, len(lines)):
        stripped = lines[i].strip()
        if len(stripped) >= 20 and all(c == "\u2500" for c in stripped):
            return lines[:i]
    return lines


# -- Prompt option extraction (NEW) -------------------------------------

# Matches numbered options: ❯ 1. Yes  /  › 1. Yes, proceed (y)
_RE_OPTION = re.compile(r"^\s*[\u276f\u203a\s]*(\d+)\.\s+(.+)$")


def extract_prompt_options(pane_text: str) -> PromptInfo | None:
    """Parse numbered options and tool context from a permission prompt.

    First detects the interactive UI region using ``extract_interactive_content``,
    then scans only within that region for numbered options like:
        ❯ 1. Yes
          2. Yes, allow reading from tmp/ from this project
          3. No

    Also extracts tool context from lines above the "Do you want to proceed?"
    question, e.g.:
        Bash command
          tail -30 /tmp/gits_bot.log 2>&1

    Returns a PromptInfo with options and tool_context, or None if no
    numbered options are found.
    """
    if not pane_text:
        return None

    # First, detect the interactive UI region so we only parse options
    # within the prompt — not from regular output that happens to have
    # numbered lines (e.g. "1. 先用 /bind /tmp 绑定一个目录").
    ui_content = extract_interactive_content(pane_text)

    # Only scan within detected prompt region — if no interactive UI
    # is found, there are no prompt options to extract.
    if not ui_content:
        return None
    option_lines = ui_content.content.strip().split("\n")

    # -- Extract numbered options from the prompt region only --
    options: list[PromptOption] = []
    for line in option_lines:
        m = _RE_OPTION.match(line)
        if m:
            options.append(PromptOption(number=int(m.group(1)), label=m.group(2).strip()))

    if not options:
        return None

    # -- Extract tool context (lines above "Do you want to proceed?") --
    all_lines = pane_text.strip().split("\n")
    tool_context = _extract_tool_context(all_lines)

    return PromptInfo(options=options, tool_context=tool_context)


# Patterns that mark the start of a tool context block
_RE_TOOL_HEADER = re.compile(
    r"^\s*(Bash command|Read|Write|Edit|Glob|Grep|WebFetch|WebSearch|"
    r"Notebook|TodoWrite|SendMessage|TaskCreate|TaskUpdate)\s*$"
)

# The question line that ends the tool context block
_RE_QUESTION = re.compile(
    r"^\s*Do you want to (proceed|make this edit|create|delete)"
    r"|^\s*Would you like to run the following command\?"
    r"|^\s*\u25b3\s*Permission required"
)


def _extract_tool_context(lines: list[str]) -> str:
    """Extract tool context block from above the permission question.

    Looks for a tool header line (e.g. "Bash command") and collects
    everything from there up to (but not including) the question line.
    """
    question_idx: int | None = None
    for i, line in enumerate(lines):
        if _RE_QUESTION.search(line):
            question_idx = i
            break

    if question_idx is None:
        return ""

    # Scan backwards from question to find tool header
    header_idx: int | None = None
    for i in range(question_idx - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if _RE_TOOL_HEADER.match(lines[i]):
            header_idx = i
            break

    if header_idx is None:
        return ""

    # Collect from header to just before the question, stripping trailing blanks
    context_lines = lines[header_idx:question_idx]
    while context_lines and not context_lines[-1].strip():
        context_lines.pop()

    return "\n".join(context_lines).rstrip()
