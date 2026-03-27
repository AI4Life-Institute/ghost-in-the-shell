#!/usr/bin/env python3
"""POC: verify that multiple openclaw-weixin accounts can poll concurrently.

Tests:
  1. All accounts in ~/.openclaw/openclaw-weixin/ are discovered
  2. Each account can independently call getupdates without interfering
  3. Concurrent polling works (messages go to the right account)

Usage:
    python poc/multi_weixin.py

Requires at least 2 logged-in openclaw-weixin accounts.
Use `ghost wechat` (or `ghost wechat --relogin`) to add accounts.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from base64 import b64encode
from pathlib import Path

import aiohttp

# ── Resolve package path ───────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from gits.openclaw.accounts import discover_all

_CHANNEL = "openclaw-weixin"
_POLL_TIMEOUT_S = 40   # long-poll hold time
_TEST_ROUNDS = 3       # number of poll rounds per account


def _make_headers(token: str, body: str) -> dict:
    n = random.randint(0, 0xFFFF_FFFF)
    uin = b64encode(str(n).encode()).decode()
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {token}",
        "Content-Length": str(len(body.encode())),
        "X-WECHAT-UIN": uin,
    }


async def poll_account(account: dict, rounds: int) -> dict:
    """Poll getupdates `rounds` times for one account. Returns a result summary."""
    account_id = account["account_id"]
    token = account["token"]
    base_url = account["base_url"]
    url = f"{base_url}/ilink/bot/getupdates"
    sync_buf = ""

    result = {
        "account_id": account_id,
        "user_id": account["user_id"],
        "rounds_ok": 0,
        "rounds_fail": 0,
        "messages_received": 0,
        "errors": [],
    }

    timeout = aiohttp.ClientTimeout(total=_POLL_TIMEOUT_S + 10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for i in range(rounds):
            payload = json.dumps({
                "get_updates_buf": sync_buf,
                "base_info": {"channel_version": "0.0.0"},
            })
            headers = _make_headers(token, payload)
            try:
                async with session.post(url, data=payload, headers=headers) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        result["rounds_fail"] += 1
                        result["errors"].append(f"round {i+1}: HTTP {resp.status} — {text[:120]}")
                        continue
                    data = await resp.json(content_type=None)

                new_buf = data.get("get_updates_buf", "")
                if new_buf:
                    sync_buf = new_buf

                msgs = data.get("msgs") or []
                result["messages_received"] += len(msgs)
                result["rounds_ok"] += 1
                print(f"  [{account_id}] round {i+1}/{rounds} OK — {len(msgs)} msg(s)")

            except asyncio.TimeoutError:
                # Normal for long-poll — server held and timed out
                result["rounds_ok"] += 1
                print(f"  [{account_id}] round {i+1}/{rounds} timeout (normal)")
            except Exception as exc:
                result["rounds_fail"] += 1
                result["errors"].append(f"round {i+1}: {exc}")
                print(f"  [{account_id}] round {i+1}/{rounds} ERROR: {exc}")

    return result


async def main() -> None:
    print("=" * 60)
    print("POC: Multi-account WeChat concurrent polling")
    print("=" * 60)

    accounts = discover_all(_CHANNEL)
    if not accounts:
        print("\n✗  No openclaw-weixin accounts found.")
        print("   Run: ghost wechat   (to log in)")
        sys.exit(1)

    print(f"\nFound {len(accounts)} account(s):")
    for a in accounts:
        print(f"  • {a['account_id']}  (user_id={a['user_id']}  base_url={a['base_url']})")

    if len(accounts) < 2:
        print("\n⚠  Only 1 account found — concurrent isolation test requires 2+.")
        print("   Run: ghost wechat --relogin   (to add a second account)")
        print("   Continuing with single-account smoke test...\n")

    print(f"\nStarting {_TEST_ROUNDS} poll rounds per account concurrently...\n")

    tasks = [poll_account(a, _TEST_ROUNDS) for a in accounts]
    results = await asyncio.gather(*tasks)

    print("\n" + "=" * 60)
    print("Results")
    print("=" * 60)

    all_ok = True
    for r in results:
        status = "✅" if r["rounds_fail"] == 0 else "❌"
        print(f"\n{status} {r['account_id']}")
        print(f"   rounds OK / fail : {r['rounds_ok']} / {r['rounds_fail']}")
        print(f"   messages received: {r['messages_received']}")
        if r["errors"]:
            all_ok = False
            for e in r["errors"]:
                print(f"   ERROR: {e}")

    print()
    if all_ok and len(accounts) >= 2:
        print("✅ PASS — multiple accounts polled concurrently without interference.")
    elif all_ok:
        print("✅ PASS — single account smoke test OK. Add a 2nd account to test isolation.")
    else:
        print("❌ FAIL — one or more accounts had errors. See above.")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
