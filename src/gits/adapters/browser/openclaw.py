"""OpenClaw browser control wrapper.

Wraps each openclaw CLI primitive as an async Python function using
asyncio.create_subprocess_exec. All commands time out after 30 seconds.

Snapshot --labels uses a role-based snapshot that returns text with
[ref=eXX] markers per element. Parsed into list[ElementRef].
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass


OPENCLAW_BIN = "openclaw"
DEFAULT_TIMEOUT = 30.0


class OpenClawError(Exception):
    """Raised when openclaw exits with a non-zero return code."""

    def __init__(self, message: str, returncode: int) -> None:
        super().__init__(message)
        self.returncode = returncode


@dataclass
class ElementRef:
    role: str
    label: str
    ref: str


@dataclass
class NavResult:
    title: str
    url: str


@dataclass
class ActionResult:
    ok: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

async def _run(
    *args: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Run openclaw with the given args and return stdout as a string.

    Raises OpenClawError on non-zero exit.
    """
    proc = await asyncio.create_subprocess_exec(
        OPENCLAW_BIN,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise OpenClawError(
            f"openclaw timed out after {timeout}s: {' '.join(args)}", -1
        )

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        detail = stderr.strip() or stdout.strip()
        raise OpenClawError(
            f"openclaw exited {proc.returncode}: {detail}",
            proc.returncode,
        )

    return stdout


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def navigate(profile: str, url: str) -> NavResult:
    """Navigate the browser to *url* using *profile*."""
    raw = await _run(
        "browser",
        "--browser-profile", profile,
        "--json",
        "navigate",
        url,
    )
    data = json.loads(raw)
    return NavResult(
        title=data.get("title") or "",
        url=data.get("url") or url,
    )


async def snapshot(profile: str) -> list[ElementRef]:
    """Capture a role snapshot with labels and return parsed ElementRef list.

    Uses --interactive so only actionable elements are listed, keeping the
    ref list concise for the agent.
    """
    raw = await _run(
        "browser",
        "--browser-profile", profile,
        "snapshot",
        "--interactive",
        "--labels",
    )
    return _parse_snapshot(raw)


def _parse_snapshot(text: str) -> list[ElementRef]:
    """Parse role snapshot text into ElementRef objects.

    Each line may look like:
      - button "Submit" [ref=e12]
      - link "Home" [ref=e3]
      - textbox "Search" [ref=e7]
      - checkbox "Remember me" [ref=e9] [checked]

    We extract role, label (name), and ref from each matching line.
    Lines without [ref=...] are skipped (static/non-interactive text).
    """
    elements: list[ElementRef] = []
    # Pattern: optional indent+dash, role token, optional quoted name, [ref=eNN]
    pattern = re.compile(
        r"^\s*-?\s*"                           # optional indent / dash
        r"(?P<role>\w[\w\s]*?)\s+"             # role (e.g. "button", "text box")
        r"(?:\"(?P<label>[^\"]*)\"\s+)?"       # optional "label"
        r"\[ref=(?P<ref>[^\]]+)\]",            # [ref=eXX]
        re.MULTILINE,
    )
    for m in pattern.finditer(text):
        elements.append(
            ElementRef(
                role=m.group("role").strip(),
                label=m.group("label") or "",
                ref=m.group("ref").strip(),
            )
        )
    return elements


async def click(profile: str, ref: str) -> ActionResult:
    """Click the element identified by *ref*."""
    try:
        await _run(
            "browser",
            "--browser-profile", profile,
            "click",
            ref,
        )
        return ActionResult(ok=True)
    except OpenClawError as exc:
        return ActionResult(ok=False, error=str(exc))


async def type_text(profile: str, ref: str, text: str) -> ActionResult:
    """Type *text* into the element identified by *ref*."""
    try:
        await _run(
            "browser",
            "--browser-profile", profile,
            "type",
            ref,
            text,
        )
        return ActionResult(ok=True)
    except OpenClawError as exc:
        return ActionResult(ok=False, error=str(exc))


async def evaluate(profile: str, js: str) -> str:
    """Evaluate a JS function expression and return the JSON-encoded result."""
    raw = await _run(
        "browser",
        "--browser-profile", profile,
        "evaluate",
        "--fn", js,
    )
    return raw.strip()


async def extract_text(profile: str) -> str:
    """Return the plain text content of the current page body."""
    return await evaluate(profile, "() => document.body.innerText")


async def list_profiles() -> list[str]:
    """Return the names of all configured browser profiles."""
    raw = await _run("browser", "--json", "profiles")
    data = json.loads(raw)
    profiles = data.get("profiles", [])
    return [p["name"] for p in profiles if isinstance(p, dict) and "name" in p]
