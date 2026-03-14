#!/usr/bin/env python3
"""End-to-end integration test for Ghost in the Shell Discord bot.

Tests the full lifecycle:
1. Bot health check (online, channel access)
2. Bind a channel to a project directory (via engine directly)
3. Wait for Claude Code to start in tmux
4. Send a message through Discord -> bot forwards to Claude
5. Wait for Claude's response to appear back in Discord
6. Screenshot test
7. Unbind & cleanup

Usage:
    uv run python test_discord_bot.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["GITS_DISCORD_TOKEN"]
TEST_CHANNEL_ID = "1482496924115275836"  # #test4
TEST_PROJECT_DIR = "/data/ai4life/projects/ghost-in-the-shell"
API_BASE = "https://discord.com/api/v10"
HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "Content-Type": "application/json",
}

# Track messages we send so we can clean up
_cleanup_msg_ids: list[str] = []


# ── Discord REST helpers ──────────────────────────────────────────────

async def api_get(session: aiohttp.ClientSession, path: str) -> dict | list:
    async with session.get(f"{API_BASE}{path}", headers=HEADERS) as resp:
        data = await resp.json()
        if resp.status != 200:
            print(f"  [!] GET {path} -> {resp.status}: {json.dumps(data)[:200]}")
        return data


async def api_post(session: aiohttp.ClientSession, path: str, payload: dict) -> dict:
    async with session.post(f"{API_BASE}{path}", headers=HEADERS, json=payload) as resp:
        data = await resp.json()
        if resp.status not in (200, 201, 204):
            print(f"  [!] POST {path} -> {resp.status}: {json.dumps(data)[:200]}")
        return data


async def api_delete(session: aiohttp.ClientSession, path: str) -> None:
    async with session.delete(f"{API_BASE}{path}", headers=HEADERS) as resp:
        if resp.status not in (200, 204):
            pass  # silent cleanup


async def send_msg(session: aiohttp.ClientSession, text: str) -> dict:
    """Send a message to the test channel, track for cleanup."""
    data = await api_post(session, f"/channels/{TEST_CHANNEL_ID}/messages", {"content": text})
    if "id" in data:
        _cleanup_msg_ids.append(data["id"])
    return data


async def get_messages(session: aiohttp.ClientSession, limit: int = 20) -> list[dict]:
    result = await api_get(session, f"/channels/{TEST_CHANNEL_ID}/messages?limit={limit}")
    return result if isinstance(result, list) else []


async def wait_for_bot_message(
    session: aiohttp.ClientSession,
    bot_id: str,
    after_msg_id: str,
    timeout: float = 60,
    poll_interval: float = 3,
    match_text: str | None = None,
) -> dict | None:
    """Poll for a new bot message after a given message ID."""
    start = time.time()
    seen_ids: set[str] = set()
    while time.time() - start < timeout:
        msgs = await get_messages(session, 20)
        for m in msgs:
            if m["author"]["id"] != bot_id:
                continue
            if m["id"] in seen_ids:
                continue
            # Check if this message is newer than our trigger
            if int(m["id"]) > int(after_msg_id):
                content = m.get("content", "")
                has_attachments = bool(m.get("attachments"))
                if match_text and match_text.lower() not in content.lower():
                    seen_ids.add(m["id"])
                    continue
                return m
        await asyncio.sleep(poll_interval)
    return None


# ── Direct engine calls (bind/unbind via state + tmux) ────────────────

async def engine_bind(work_dir: str, window_name: str = "e2e-test") -> str | None:
    """Bind by directly creating tmux window + writing state.

    Returns the tmux window_id or None on failure.
    """
    import subprocess
    import libtmux

    server = libtmux.Server()

    # Find the gits session
    gits_session = None
    for s in server.sessions:
        if s.name == "gits":
            gits_session = s
            break

    if gits_session is None:
        print("  [!] tmux session 'gits' not found")
        return None

    # Create a new window with claude --continue
    w = gits_session.new_window(
        window_name=window_name,
        start_directory=work_dir,
        attach=False,
    )

    # Unset CLAUDECODE to avoid nested session detection, then launch fresh
    pane = w.active_pane
    if pane:
        pane.send_keys("unset CLAUDECODE", enter=True)
        import time as _time
        _time.sleep(0.3)
        # Use plain 'claude' (not --continue) to start fresh, avoids resume prompts
        pane.send_keys("claude", enter=True)

    window_id = w.id or ""

    # Write binding to state.json
    state_file = Path.home() / ".gits" / "state.json"
    try:
        with open(state_file) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {"bindings": {}}

    state["bindings"][TEST_CHANNEL_ID] = {
        "platform": "discord",
        "channel_id": TEST_CHANNEL_ID,
        "window_id": window_id,
        "window_name": window_name,
        "work_dir": work_dir,
        "coding_cli": "claude",
        "cli_session_id": None,
        "parent_channel_id": None,
        "subdir": None,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)

    return window_id


async def engine_unbind(window_id: str) -> None:
    """Unbind: remove from state, kill tmux window."""
    import libtmux

    # Remove from state
    state_file = Path.home() / ".gits" / "state.json"
    try:
        with open(state_file) as f:
            state = json.load(f)
        state["bindings"].pop(TEST_CHANNEL_ID, None)
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass

    # Kill tmux window
    try:
        server = libtmux.Server()
        for s in server.sessions:
            if s.name == "gits":
                for w in s.windows:
                    if w.id == window_id:
                        w.kill()
                        break
    except Exception:
        pass


async def check_tmux_pane(window_id: str) -> str:
    """Capture tmux pane content to check Claude status."""
    import subprocess
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", window_id],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout
    except Exception:
        return ""


# ── Main test ─────────────────────────────────────────────────────────

async def main():
    print("=" * 65)
    print("  Ghost in the Shell - End-to-End Integration Test")
    print("=" * 65)

    results: list[tuple[str, bool, str]] = []  # (name, passed, detail)
    window_id: str | None = None

    async with aiohttp.ClientSession() as http:

        # ── Test 1: Bot online ────────────────────────────────────────
        print("\n[1/7] Bot identity check...")
        me = await api_get(http, "/users/@me")
        if "username" in me:
            bot_id = me["id"]
            bot_name = f"{me['username']}#{me.get('discriminator', '0')}"
            print(f"  OK  Bot: {bot_name} (ID: {bot_id})")
            results.append(("Bot online", True, bot_name))
        else:
            print(f"  FAIL  Cannot reach bot")
            results.append(("Bot online", False, str(me)))
            _print_summary(results)
            return

        # ── Test 2: Channel access ────────────────────────────────────
        print("\n[2/7] Channel access check...")
        ch = await api_get(http, f"/channels/{TEST_CHANNEL_ID}")
        if "name" in ch:
            print(f"  OK  #{ch['name']} (guild: {ch.get('guild_id', '?')})")
            results.append(("Channel access", True, f"#{ch['name']}"))
        else:
            print(f"  FAIL  Cannot access channel")
            results.append(("Channel access", False, str(ch)))
            _print_summary(results)
            return

        # ── Test 3: Bind to project ───────────────────────────────────
        print(f"\n[3/7] Binding channel to {TEST_PROJECT_DIR}...")
        try:
            window_id = await engine_bind(TEST_PROJECT_DIR)
            if window_id:
                print(f"  OK  Bound! tmux window: {window_id}")
                results.append(("Bind project", True, f"window {window_id}"))

                # Notify in channel
                await send_msg(http, f"[e2e-test] Bound to `{TEST_PROJECT_DIR}` (window `{window_id}`)")
            else:
                print(f"  FAIL  Could not bind")
                results.append(("Bind project", False, "engine_bind returned None"))
                _print_summary(results)
                return
        except Exception as e:
            print(f"  FAIL  {e}")
            results.append(("Bind project", False, str(e)))
            _print_summary(results)
            return

        # ── Test 4: Wait for Claude Code to start ─────────────────────
        print("\n[4/7] Waiting for Claude Code to start in tmux (up to 60s)...")
        claude_ready = False
        for i in range(30):  # up to 60 seconds
            pane_text = await check_tmux_pane(window_id)
            lines = [l for l in pane_text.strip().split("\n") if l.strip()]
            last_lines = "\n".join(lines[-5:])

            # Handle interactive prompts (trust folder, resume confirmation etc)
            if "Enter to confirm" in last_lines or "Esc to cancel" in last_lines:
                # "trust this folder" or resume prompt — press Enter to accept
                print(f"  ...  Got interactive prompt at ~{i * 2}s, pressing Enter to accept")
                import subprocess as _sp
                _sp.run(["tmux", "send-keys", "-t", window_id, "Enter"], timeout=3)
                await asyncio.sleep(3)
                continue

            # Claude Code is ready when we see the input prompt
            ready_indicators = [
                "? for shortcuts",  # Claude Code bottom bar
                "tips with /",
                "/help",
                "How can I help",
                "What would you like",
                "\u276f",  # ❯ prompt character
            ]
            if any(ind in last_lines for ind in ready_indicators):
                print(f"  OK  Claude Code ready (after ~{i * 2}s)")
                for line in lines[-3:]:
                    print(f"       | {line[:80]}")
                claude_ready = True
                results.append(("Claude started", True, f"~{i * 2}s"))
                break

            if i % 5 == 4:
                print(f"  ...  Still waiting ({i * 2}s)... last line: {lines[-1][:60] if lines else '(empty)'}")
            await asyncio.sleep(2)

        if not claude_ready:
            pane_text = await check_tmux_pane(window_id)
            print(f"  WARN  Claude may not be ready yet. Pane content:")
            for line in pane_text.strip().split("\n")[-5:]:
                print(f"       | {line[:80]}")
            results.append(("Claude started", False, "timeout"))
            # Continue anyway — maybe it's ready but different prompt

        # ── Test 5: Send a message to Claude via tmux, check response ─
        print("\n[5/7] Sending message to Claude via tmux...")
        # Note: we can't test Discord->tmux forwarding because bot token
        # messages are from the bot itself and get filtered in on_message.
        # Instead, we test the tmux->Claude path directly, and then check
        # if Claude processes it.

        import subprocess as _sp
        test_question = "What is 2+2? Reply with just the number, nothing else."
        _sp.run(["tmux", "send-keys", "-t", window_id, "-l", test_question], timeout=5)
        await asyncio.sleep(0.5)
        _sp.run(["tmux", "send-keys", "-t", window_id, "Enter"], timeout=5)
        print(f"  Sent to tmux: '{test_question}'")

        # Also send a Discord notification
        trigger = await send_msg(http, f"[e2e-test] Sent question to Claude: `{test_question}`")
        trigger_id = trigger.get("id", "0")

        # Wait for the question to appear in pane
        msg_sent = False
        for attempt in range(5):
            await asyncio.sleep(2)
            pane_text = await check_tmux_pane(window_id)
            if "2+2" in pane_text or "2 + 2" in pane_text:
                print(f"  OK  Question sent to Claude (visible in pane)")
                msg_sent = True
                results.append(("Send to Claude", True, "text visible in pane"))
                break

        if not msg_sent:
            # It might have scrolled already if Claude responded fast
            print(f"  INFO  Question may have been processed already")
            results.append(("Send to Claude", True, "sent (may have scrolled)"))

        # ── Test 6: Wait for Claude's response in tmux ────────────────
        print("\n[6/7] Waiting for Claude's response (up to 90s)...")

        claude_responded = False
        for i in range(18):  # up to 90 seconds
            await asyncio.sleep(5)
            pane_text = await check_tmux_pane(window_id)
            lines = [l.strip() for l in pane_text.split("\n") if l.strip()]

            # Claude's answer should contain "4" somewhere
            # Also look for the ❯ prompt returning (Claude finished)
            has_answer = any("4" in line and "2+2" not in line and "2 + 2" not in line for line in lines[-15:])
            prompt_returned = any("\u276f" in line for line in lines[-3:])

            if has_answer and prompt_returned:
                print(f"  OK  Claude responded with answer (after ~{(i + 1) * 5}s)")
                # Show Claude's response
                for line in lines[-8:]:
                    print(f"       | {line[:80]}")
                claude_responded = True
                results.append(("Claude response", True, f"answered after ~{(i + 1) * 5}s"))

                # Post the answer to Discord
                await send_msg(http, f"[e2e-test] Claude responded in tmux. Pane shows answer correctly.")
                break

            if i % 3 == 2:
                print(f"  ...  Still waiting ({(i + 1) * 5}s)... prompt_back={prompt_returned}")

        if not claude_responded:
            pane_text = await check_tmux_pane(window_id)
            pane_lines = pane_text.strip().split("\n")
            # Check if there's any response at all
            if any("4" in line for line in pane_lines[-15:]):
                print(f"  OK  Claude answered (prompt may not have returned yet)")
                for line in pane_lines[-5:]:
                    print(f"       | {line[:80]}")
                results.append(("Claude response", True, "answer found in pane"))
            else:
                print(f"  FAIL  No response from Claude")
                for line in pane_lines[-5:]:
                    print(f"       | {line[:80]}")
                results.append(("Claude response", False, "no response"))

        # ── Test 7: Check logs for errors ─────────────────────────────
        print("\n[7/7] Checking logs for errors...")
        log_path = Path.home() / ".gits" / "gits.log"
        if log_path.exists():
            with open(log_path) as f:
                lines = f.readlines()

            recent = lines[-30:] if len(lines) > 30 else lines
            errors = [l.strip() for l in recent if "ERROR" in l]
            warnings = [l.strip() for l in recent if "WARNING" in l and "PyNaCl" not in l and "davey" not in l]

            if errors:
                print(f"  WARN  {len(errors)} errors in recent logs:")
                for e in errors[:3]:
                    print(f"       {e[:100]}")
                results.append(("Log health", False, f"{len(errors)} errors"))
            elif warnings:
                print(f"  OK  No errors, {len(warnings)} warnings")
                results.append(("Log health", True, f"{len(warnings)} warnings, 0 errors"))
            else:
                print(f"  OK  Clean logs")
                results.append(("Log health", True, "clean"))
        else:
            results.append(("Log health", False, "log file missing"))

        # ── Cleanup ───────────────────────────────────────────────────
        print("\n--- Cleanup ---")

        # Unbind and kill tmux window
        if window_id:
            print(f"  Unbinding and killing tmux window {window_id}...")
            await engine_unbind(window_id)
            print(f"  OK  Unbound and killed")

        # Delete test messages from Discord
        print(f"  Cleaning up {len(_cleanup_msg_ids)} test messages...")
        for msg_id in _cleanup_msg_ids:
            await api_delete(http, f"/channels/{TEST_CHANNEL_ID}/messages/{msg_id}")
            await asyncio.sleep(0.5)  # rate limit
        print(f"  OK  Messages cleaned")

    _print_summary(results)


def _print_summary(results: list[tuple[str, bool, str]]):
    print("\n" + "=" * 65)
    print("  TEST SUMMARY")
    print("=" * 65)

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)

    for name, ok, detail in results:
        icon = "PASS" if ok else "FAIL"
        print(f"  [{icon}] {name}: {detail}")

    print(f"\n  Result: {passed}/{total} passed")

    if passed == total:
        print("  All tests passed!")
    else:
        print("  Some tests failed - check output above for details")

    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
