"""Pure formatter for the captured ``/usage`` panel (task [[s8wq7p]]).

Takes the raw ``tmux capture-pane -p`` output from a throwaway claude
session, trims the spawn banner / tab-bar / spawn-session zeros block /
footer, and wraps the result in a Discord code fence with a header that
preserves the "local-machine estimate" disclaimer context.

Kept out of ``engine.py`` so the trim rules are unit-testable without
any tmux / Discord plumbing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Discord's hard limit is 2000; reserve headroom for the header line and
# the surrounding code-fence (``` + newline + ``` = 7 chars).
_INLINE_BUDGET = 1900

_SENTINEL_START = "Current session"
_SENTINEL_END = "Esc to cancel"


@dataclass
class UsagePanelResult:
    header: str
    body: str
    inline: bool


def trim_usage_panel(raw: str) -> str:
    """Trim the raw capture down to the panel body.

    Rules (in order):
    1. Drop everything before the first line containing ``Current session``.
    2. If a line containing ``Esc to cancel`` is found, drop it and
       everything after. Otherwise keep through end.
    3. ``rstrip()`` each remaining line.
    4. Collapse runs of 2+ blank lines to a single blank line.
    5. Strip a single trailing blank line.

    If ``Current session`` is not found, returns ``""`` — the caller maps
    that to the "Capture timed out" error path (likely a login prompt).
    """
    lines = raw.splitlines()

    start = None
    for i, line in enumerate(lines):
        if _SENTINEL_START in line:
            start = i
            break
    if start is None:
        return ""

    end = len(lines)
    for i in range(start, len(lines)):
        if _SENTINEL_END in lines[i]:
            end = i
            break

    kept = [line.rstrip() for line in lines[start:end]]

    collapsed: list[str] = []
    prev_blank = False
    for line in kept:
        is_blank = line == ""
        if is_blank and prev_blank:
            continue
        collapsed.append(line)
        prev_blank = is_blank

    while collapsed and collapsed[-1] == "":
        collapsed.pop()

    return "\n".join(collapsed)


def format_usage_panel(
    raw: str,
    account: str,
    now: datetime,
) -> UsagePanelResult:
    """Trim and frame the captured panel for posting to Discord.

    Returns ``UsagePanelResult`` with the header (always sent), the
    trimmed body, and an ``inline`` flag — ``True`` if the fully formatted
    message fits within Discord's per-message budget, ``False`` if the
    caller should fall back to an attachment.

    An empty trimmed body produces ``UsagePanelResult(header, "", True)``;
    the caller is expected to detect the empty body and emit the
    "Capture timed out" error reply instead of a real panel post.
    """
    body = trim_usage_panel(raw)
    header = (
        f"**`/usage` for `{account}`** — captured {now.strftime('%H:%M')} "
        "local; this is claude's local-machine estimate, not Anthropic-side "
        "quota"
    )
    fenced_len = len(header) + len("\n```\n") + len(body) + len("\n```")
    inline = fenced_len <= _INLINE_BUDGET
    return UsagePanelResult(header=header, body=body, inline=inline)
