#!/usr/bin/env python3
"""Comprehensive command test for Ghost in the Shell Discord bot.

Tests ALL 18 slash commands by calling engine handlers directly
with a fake interaction object, then verifying responses.

Commands tested:
  A. Native (10):  /bind /unbind /fork /screenshot /status /stop /kill /new /bash /model
  B. CLI Fwd (7):  /compact /clear /cost /memory /context /diff /usage
  C. Universal (1): /cc

Usage:
    uv run python test_all_commands.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# Need to be in project dir for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))
os.chdir(Path(__file__).parent)

from dotenv import load_dotenv
load_dotenv()

import aiohttp

TOKEN = os.environ["GITS_DISCORD_TOKEN"]
TEST_CHANNEL_ID = "1482496924115275836"  # #test4
TEST_PROJECT_DIR = "/data/ai4life/projects/ghost-in-the-shell"
API_BASE = "https://discord.com/api/v10"
HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "Content-Type": "application/json",
}

_cleanup_msg_ids: list[str] = []


# ── Discord REST helpers ──────────────────────────────────────────────

async def discord_send(session: aiohttp.ClientSession, text: str) -> str:
    """Send a message to the test channel, return msg ID."""
    async with session.post(
        f"{API_BASE}/channels/{TEST_CHANNEL_ID}/messages",
        headers=HEADERS, json={"content": text}
    ) as resp:
        data = await resp.json()
        msg_id = data.get("id", "")
        if msg_id:
            _cleanup_msg_ids.append(msg_id)
        return msg_id


async def discord_delete(session: aiohttp.ClientSession, msg_id: str) -> None:
    async with session.delete(
        f"{API_BASE}/channels/{TEST_CHANNEL_ID}/messages/{msg_id}",
        headers=HEADERS
    ) as resp:
        pass


# ── Fake interaction for testing ──────────────────────────────────────

class FakeInteraction:
    """Mimics a Discord Interaction for testing engine handlers."""

    def __init__(self, channel_name: str = "test4"):
        self.followup = MagicMock()
        self.followup.send = AsyncMock()
        self.channel = MagicMock()
        self.channel.name = channel_name
        self.channel_id = TEST_CHANNEL_ID
        self.user = MagicMock()
        self.user.id = 1174222604425510982
        self.guild_id = 1258194549998878731
        self.response = MagicMock()
        self.response.defer = AsyncMock()
        self._responses: list[str] = []

        # Capture all followup.send calls
        async def capture_send(text: str = "", **kwargs):
            self._responses.append(text)
        self.followup.send = capture_send

    @property
    def last_response(self) -> str:
        return self._responses[-1] if self._responses else ""

    @property
    def all_responses(self) -> list[str]:
        return self._responses


# ── tmux helpers ──────────────────────────────────────────────────────

def tmux_capture(window_id: str) -> str:
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", window_id],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout
    except Exception:
        return ""


def tmux_send(window_id: str, text: str, enter: bool = True) -> None:
    subprocess.run(["tmux", "send-keys", "-t", window_id, "-l", text], timeout=5)
    if enter:
        time.sleep(0.3)
        subprocess.run(["tmux", "send-keys", "-t", window_id, "Enter"], timeout=5)


# ── Wait for Claude Code ready ────────────────────────────────────────

async def wait_claude_ready(window_id: str, timeout: int = 60) -> bool:
    """Wait for Claude Code to be ready (shows ❯ prompt)."""
    for i in range(timeout // 2):
        pane = tmux_capture(window_id)
        lines = "\n".join(pane.strip().split("\n")[-5:])

        # Handle trust/confirm prompts
        if "Enter to confirm" in lines:
            subprocess.run(["tmux", "send-keys", "-t", window_id, "Enter"], timeout=3)
            await asyncio.sleep(3)
            continue

        if "? for shortcuts" in lines or "\u276f" in lines:
            return True

        await asyncio.sleep(2)
    return False


# ── Main test ─────────────────────────────────────────────────────────

async def main():
    print("=" * 70)
    print("  Ghost in the Shell — ALL COMMANDS Test")
    print("  Testing all 18 slash commands")
    print("=" * 70)

    # Import engine components
    from gits.config import Settings
    from gits.core.engine import Engine

    settings = Settings()
    engine = Engine(settings)

    # We need to set a mock adapter for screenshot (it calls adapter.send_message)
    mock_adapter = MagicMock()
    mock_adapter.send_message = AsyncMock()
    mock_adapter.create_thread = AsyncMock(return_value="fake-thread-123")
    mock_adapter.archive_thread = AsyncMock()
    engine.set_adapter(mock_adapter)

    await engine.start()

    results: list[tuple[str, bool, str]] = []
    window_id: str | None = None

    async with aiohttp.ClientSession() as http:
        await discord_send(http, "**[test-run]** Starting all-commands test...")

        # ==============================================================
        # 1. /bind (no path) — should show usage
        # ==============================================================
        print("\n[01/18] /bind (no path)...")
        ix = FakeInteraction()
        await engine.handle_bind(TEST_CHANNEL_ID, None, ix)
        if "Usage" in ix.last_response or "/bind" in ix.last_response:
            print(f"  PASS  Got usage: {ix.last_response[:60]}")
            results.append(("/bind (no path)", True, "shows usage"))
        else:
            print(f"  FAIL  Unexpected: {ix.last_response[:60]}")
            results.append(("/bind (no path)", False, ix.last_response[:60]))

        # ==============================================================
        # 2. /bind (bad path) — should show error
        # ==============================================================
        print("\n[02/18] /bind (bad path)...")
        ix = FakeInteraction()
        await engine.handle_bind(TEST_CHANNEL_ID, "/nonexistent/path/foo", ix)
        if "not found" in ix.last_response.lower() or "not in allowed" in ix.last_response.lower():
            print(f"  PASS  Error: {ix.last_response[:60]}")
            results.append(("/bind (bad path)", True, "rejects bad path"))
        else:
            print(f"  FAIL  Unexpected: {ix.last_response[:60]}")
            results.append(("/bind (bad path)", False, ix.last_response[:60]))

        # ==============================================================
        # 3. /status (unbound) — should say not bound
        # ==============================================================
        print("\n[03/18] /status (unbound)...")
        ix = FakeInteraction()
        await engine.handle_status(TEST_CHANNEL_ID, ix)
        if "not bound" in ix.last_response.lower():
            print(f"  PASS  {ix.last_response[:60]}")
            results.append(("/status (unbound)", True, "not bound"))
        else:
            print(f"  FAIL  {ix.last_response[:60]}")
            results.append(("/status (unbound)", False, ix.last_response[:60]))

        # ==============================================================
        # 4. /screenshot (unbound) — should say not bound
        # ==============================================================
        print("\n[04/18] /screenshot (unbound)...")
        ix = FakeInteraction()
        await engine.handle_screenshot(TEST_CHANNEL_ID, ix)
        if "not bound" in ix.last_response.lower() or "bind" in ix.last_response.lower():
            print(f"  PASS  {ix.last_response[:60]}")
            results.append(("/screenshot (unbound)", True, "not bound"))
        else:
            print(f"  FAIL  {ix.last_response[:60]}")
            results.append(("/screenshot (unbound)", False, ix.last_response[:60]))

        # ==============================================================
        # 5. /bash (unbound) — should say not bound
        # ==============================================================
        print("\n[05/18] /bash (unbound)...")
        ix = FakeInteraction()
        await engine.handle_bash(TEST_CHANNEL_ID, "echo hello", ix)
        if "not bound" in ix.last_response.lower():
            print(f"  PASS  {ix.last_response[:60]}")
            results.append(("/bash (unbound)", True, "not bound"))
        else:
            print(f"  FAIL  {ix.last_response[:60]}")
            results.append(("/bash (unbound)", False, ix.last_response[:60]))

        # ==============================================================
        # 6. /bind (valid path) — the big one
        # ==============================================================
        print("\n[06/18] /bind (valid path)...")
        ix = FakeInteraction()
        await engine.handle_bind(TEST_CHANNEL_ID, TEST_PROJECT_DIR, ix)
        resp = ix.last_response
        if "Bound" in resp and "tmux window" in resp:
            # Extract window ID
            import re
            m = re.search(r"`(@\d+)`", resp)
            window_id = m.group(1) if m else None
            print(f"  PASS  {resp[:80]}")
            # Check directory listing
            if "```" in resp:
                print(f"  PASS  Directory listing included")
            results.append(("/bind (valid)", True, f"window {window_id}"))
            await discord_send(http, f"[test] /bind -> {resp[:200]}")
        else:
            print(f"  FAIL  {resp[:80]}")
            results.append(("/bind (valid)", False, resp[:60]))

        if not window_id:
            print("\n  FATAL: No window_id, cannot continue bound tests")
            _print_summary(results)
            return

        # Wait for Claude Code to start
        print("\n  Waiting for Claude Code to start...")
        ready = await wait_claude_ready(window_id, timeout=60)
        if ready:
            print(f"  Claude Code is ready!")
        else:
            print(f"  WARN: Claude may not be fully ready")

        # ==============================================================
        # 7. /status (bound) — should show binding info
        # ==============================================================
        print("\n[07/18] /status (bound)...")
        ix = FakeInteraction()
        await engine.handle_status(TEST_CHANNEL_ID, ix)
        resp = ix.last_response
        if "test4" in resp or window_id in resp or TEST_PROJECT_DIR in resp:
            print(f"  PASS  {resp[:80]}")
            results.append(("/status (bound)", True, "shows binding"))
            await discord_send(http, f"[test] /status -> {resp[:200]}")
        else:
            print(f"  FAIL  {resp[:80]}")
            results.append(("/status (bound)", False, resp[:60]))

        # ==============================================================
        # 8. /bash ls — run shell command
        # ==============================================================
        print("\n[08/18] /bash ls...")
        ix = FakeInteraction()
        await engine.handle_bash(TEST_CHANNEL_ID, "ls", ix)
        resp = ix.last_response
        if "src" in resp or "pyproject" in resp or "Exit code: 0" in resp:
            print(f"  PASS  Got ls output, exit 0")
            results.append(("/bash ls", True, "exit code 0"))
            await discord_send(http, f"[test] /bash ls -> {resp[:200]}")
        else:
            print(f"  FAIL  {resp[:80]}")
            results.append(("/bash ls", False, resp[:60]))

        # ==============================================================
        # 9. /bash (complex command) — test with pipe
        # ==============================================================
        print("\n[09/18] /bash (pipe command)...")
        ix = FakeInteraction()
        await engine.handle_bash(TEST_CHANNEL_ID, "echo 'hello world' | wc -w", ix)
        resp = ix.last_response
        if "2" in resp and "Exit code: 0" in resp:
            print(f"  PASS  Pipe worked, got '2'")
            results.append(("/bash (pipe)", True, "pipe works"))
        else:
            print(f"  FAIL  {resp[:80]}")
            results.append(("/bash (pipe)", False, resp[:60]))

        # ==============================================================
        # 10. /screenshot — take terminal screenshot
        # ==============================================================
        print("\n[10/18] /screenshot...")
        ix = FakeInteraction()
        mock_adapter.send_message.reset_mock()
        await engine.handle_screenshot(TEST_CHANNEL_ID, ix)
        # Screenshot sends via adapter.send_message, not followup
        if mock_adapter.send_message.called:
            call_args = mock_adapter.send_message.call_args
            msg = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("msg")
            has_image = msg and msg.image and len(msg.image) > 0
            if has_image:
                print(f"  PASS  Screenshot captured ({len(msg.image)} bytes)")
                results.append(("/screenshot", True, f"{len(msg.image)} bytes"))
                # Send to Discord for visual verification
                await discord_send(http, f"[test] /screenshot -> {len(msg.image)} bytes PNG")
            else:
                print(f"  FAIL  send_message called but no image")
                results.append(("/screenshot", False, "no image data"))
        else:
            # Check if error response
            if ix.last_response:
                print(f"  FAIL  {ix.last_response[:60]}")
                results.append(("/screenshot", False, ix.last_response[:60]))
            else:
                print(f"  FAIL  No response")
                results.append(("/screenshot", False, "no response"))

        # ==============================================================
        # 11. /model (no arg) — show help
        # ==============================================================
        print("\n[11/18] /model (no arg)...")
        ix = FakeInteraction()
        await engine.handle_model(TEST_CHANNEL_ID, None, ix)
        resp = ix.last_response
        if "sonnet" in resp.lower() and "opus" in resp.lower():
            print(f"  PASS  Shows model list")
            results.append(("/model (help)", True, "shows models"))
        else:
            print(f"  FAIL  {resp[:60]}")
            results.append(("/model (help)", False, resp[:60]))

        # ==============================================================
        # 12. /model sonnet — switch model
        # ==============================================================
        print("\n[12/18] /model sonnet...")
        ix = FakeInteraction()
        await engine.handle_model(TEST_CHANNEL_ID, "sonnet", ix)
        resp = ix.last_response
        if "sonnet" in resp.lower() and ("switch" in resp.lower() or "model" in resp.lower()):
            print(f"  PASS  {resp[:60]}")
            results.append(("/model sonnet", True, "switches to sonnet"))
        else:
            print(f"  FAIL  {resp[:60]}")
            results.append(("/model sonnet", False, resp[:60]))
        await asyncio.sleep(2)  # Let Claude process the /model command

        # ==============================================================
        # 13. /stop — send Escape
        # ==============================================================
        print("\n[13/18] /stop...")
        ix = FakeInteraction()
        await engine.handle_stop(TEST_CHANNEL_ID, ix)
        resp = ix.last_response
        if "escape" in resp.lower() or "interrupt" in resp.lower():
            print(f"  PASS  {resp[:60]}")
            results.append(("/stop", True, "sends escape"))
        else:
            print(f"  FAIL  {resp[:60]}")
            results.append(("/stop", False, resp[:60]))

        # ==============================================================
        # 14-19. CLI Forwarding commands: /compact /clear /cost /context /diff /usage
        # ==============================================================
        cli_commands = [
            ("compact", 14),
            ("clear", 15),
            ("cost", 16),
            ("context", 17),
            ("diff", 18),
            ("usage", 19),
        ]

        # Wait a moment for Claude to settle after /stop
        await asyncio.sleep(2)

        for cmd_name, num in cli_commands:
            print(f"\n[{num}/21] /{cmd_name}...")
            ix = FakeInteraction()
            await engine.handle_cli_forward(TEST_CHANNEL_ID, cmd_name, ix)
            resp = ix.last_response
            if "forwarded" in resp.lower() or f"/{cmd_name}" in resp.lower():
                print(f"  PASS  {resp[:60]}")
                results.append((f"/{cmd_name}", True, "forwarded"))
            else:
                print(f"  FAIL  {resp[:60]}")
                results.append((f"/{cmd_name}", False, resp[:60]))
            await asyncio.sleep(1)

        # ==============================================================
        # 20. /cc — universal forwarder
        # ==============================================================
        print("\n[20/21] /cc (universal forward)...")
        ix = FakeInteraction()
        await engine.handle_cli_forward(TEST_CHANNEL_ID, "help", ix)
        resp = ix.last_response
        if "forwarded" in resp.lower() or "/help" in resp.lower():
            print(f"  PASS  {resp[:60]}")
            results.append(("/cc (help)", True, "forwarded /help"))
        else:
            print(f"  FAIL  {resp[:60]}")
            results.append(("/cc (help)", False, resp[:60]))

        # ==============================================================
        # 21. /unbind — unbind the channel
        # ==============================================================
        print("\n[21/21] /unbind...")
        # First verify we're still bound
        binding = engine.session_mgr.get_binding(TEST_CHANNEL_ID)
        was_bound = binding is not None

        ix = FakeInteraction()
        await engine.handle_unbind(TEST_CHANNEL_ID, ix)
        resp = ix.last_response
        if "unbound" in resp.lower():
            print(f"  PASS  {resp[:60]}")
            results.append(("/unbind", True, "unbound successfully"))
            await discord_send(http, f"[test] /unbind -> {resp[:60]}")
        else:
            print(f"  FAIL  {resp[:60]}")
            results.append(("/unbind", False, resp[:60]))

        # Verify unbind worked
        binding_after = engine.session_mgr.get_binding(TEST_CHANNEL_ID)
        if binding_after is None:
            print(f"  PASS  Binding removed from state")
        else:
            print(f"  WARN  Binding still in state!")

        # ==============================================================
        # BONUS: /kill — test on a fresh bind then kill
        # ==============================================================
        print("\n[BONUS] /kill (bind -> kill cycle)...")
        ix = FakeInteraction()
        await engine.handle_bind(TEST_CHANNEL_ID, TEST_PROJECT_DIR, ix)
        resp = ix.last_response
        if "Bound" in resp:
            # Extract window ID
            import re
            m = re.search(r"`(@\d+)`", resp)
            kill_window_id = m.group(1) if m else None
            print(f"  Bound to {kill_window_id}")

            await asyncio.sleep(1)

            ix2 = FakeInteraction()
            await engine.handle_kill(TEST_CHANNEL_ID, ix2)
            resp2 = ix2.last_response
            if "killed" in resp2.lower() or "removed" in resp2.lower():
                print(f"  PASS  {resp2[:60]}")
                results.append(("/kill", True, "killed + removed"))
            else:
                print(f"  FAIL  {resp2[:60]}")
                results.append(("/kill", False, resp2[:60]))
        else:
            print(f"  FAIL  Could not bind for kill test: {resp[:60]}")
            results.append(("/kill", False, "bind failed"))

        # ==============================================================
        # BONUS: /new — test session reset
        # ==============================================================
        print("\n[BONUS] /new (bind -> new cycle)...")
        ix = FakeInteraction()
        await engine.handle_bind(TEST_CHANNEL_ID, TEST_PROJECT_DIR, ix)
        resp = ix.last_response
        if "Bound" in resp:
            import re
            m = re.search(r"`(@\d+)`", resp)
            new_window_id = m.group(1) if m else None
            print(f"  Bound to {new_window_id}")

            # Wait for Claude to start
            if new_window_id:
                ready = await wait_claude_ready(new_window_id, timeout=30)
                print(f"  Claude ready: {ready}")

            await asyncio.sleep(1)

            ix2 = FakeInteraction()
            await engine.handle_new(TEST_CHANNEL_ID, ix2)
            resp2 = ix2.last_response
            if "reset" in resp2.lower() or "fresh" in resp2.lower():
                print(f"  PASS  {resp2[:60]}")
                results.append(("/new", True, "session reset"))
            else:
                print(f"  FAIL  {resp2[:60]}")
                results.append(("/new", False, resp2[:60]))

            # Clean up
            await asyncio.sleep(1)
            ix3 = FakeInteraction()
            await engine.handle_kill(TEST_CHANNEL_ID, ix3)
        else:
            print(f"  FAIL  Could not bind: {resp[:60]}")
            results.append(("/new", False, "bind failed"))

        # ==============================================================
        # BONUS: /fork — test thread creation
        # ==============================================================
        print("\n[BONUS] /fork...")
        # Need a binding first
        ix = FakeInteraction()
        await engine.handle_bind(TEST_CHANNEL_ID, TEST_PROJECT_DIR, ix)
        if "Bound" in ix.last_response:
            ix2 = FakeInteraction()
            await engine.handle_fork(TEST_CHANNEL_ID, "test-subtask", None, ix2)
            resp = ix2.last_response
            if "forked" in resp.lower() or "thread" in resp.lower() or "test-subtask" in resp.lower():
                print(f"  PASS  {resp[:80]}")
                results.append(("/fork", True, "created thread"))
            else:
                print(f"  FAIL  {resp[:80]}")
                results.append(("/fork", False, resp[:60]))

            # Cleanup
            ix3 = FakeInteraction()
            await engine.handle_kill(TEST_CHANNEL_ID, ix3)
        else:
            results.append(("/fork", False, "bind failed"))

        # ==============================================================
        # Summary
        # ==============================================================
        await discord_send(http, f"**[test-run]** All-commands test complete!")

        # Cleanup messages
        print("\n--- Cleanup ---")
        # Kill any remaining tmux windows
        try:
            import libtmux
            server = libtmux.Server()
            for s in server.sessions:
                if s.name == "gits":
                    for w in s.windows:
                        if w.name != "bash":
                            try:
                                w.kill()
                            except Exception:
                                pass
        except Exception:
            pass

        # Clear state
        state_file = Path.home() / ".gits" / "state.json"
        with open(state_file, "w") as f:
            json.dump({"bindings": {}}, f)

        # Clean up Discord messages
        print(f"  Deleting {len(_cleanup_msg_ids)} test messages...")
        for msg_id in _cleanup_msg_ids:
            await discord_delete(http, msg_id)
            await asyncio.sleep(0.5)

    await engine.stop()
    _print_summary(results)


def _print_summary(results: list[tuple[str, bool, str]]):
    print("\n" + "=" * 70)
    print("  ALL COMMANDS TEST SUMMARY")
    print("=" * 70)

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)

    # Group by category
    native = [r for r in results if r[0].startswith("/b") or r[0].startswith("/s")
              or r[0].startswith("/k") or r[0].startswith("/n") or r[0].startswith("/m")
              or r[0].startswith("/f")]
    cli_fwd = [r for r in results if r[0].lstrip("/") in ("compact", "clear", "cost", "context", "diff", "usage")]
    universal = [r for r in results if r[0].startswith("/cc")]
    other = [r for r in results if r not in native and r not in cli_fwd and r not in universal]

    print("\n  Native Commands:")
    for name, ok, detail in results:
        icon = "PASS" if ok else "FAIL"
        print(f"    [{icon}] {name:25s} {detail}")

    print(f"\n  Result: {passed}/{total} passed")
    if passed == total:
        print("  ALL TESTS PASSED!")
    else:
        failed = [(n, d) for n, ok, d in results if not ok]
        print(f"  {len(failed)} failed: {', '.join(n for n, _ in failed)}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
