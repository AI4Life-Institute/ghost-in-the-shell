#!/usr/bin/env python3
"""
POC: WeChat Onboarding via openclaw-weixin-cli

Validates the proposed `ghost weixin` flow end-to-end:
  1. Check npx is available
  2. Check / install openclaw-weixin account
  3. Read account via proposed openclaw.accounts interface
  4. Print result

Run: uv run python poc/weixin_onboarding.py [--relogin]
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── Proposed openclaw.accounts interface (inlined for POC) ────────────────────

_OPENCLAW_DIR = Path("~/.openclaw").expanduser()


def _normalize_account_id(raw: str) -> str:
    """Mirror openclaw's normalizeAccountId: lowercase, non-[a-z0-9_] → '-'."""
    s = raw.strip().lower()
    if not s:
        return "default"
    result = ""
    for ch in s:
        if ch.isalnum() or ch == "_":
            result += ch
        else:
            result += "-"
    # collapse multiple dashes, strip leading/trailing
    while "--" in result:
        result = result.replace("--", "-")
    result = result.strip("-")
    return result[:64] or "default"


def _channel_dir(channel: str) -> Path:
    return _OPENCLAW_DIR / channel


def _accounts_index(channel: str) -> Path:
    return _channel_dir(channel) / "accounts.json"


def _account_file(channel: str, account_id: str) -> Path:
    norm = _normalize_account_id(account_id)
    return _channel_dir(channel) / "accounts" / f"{norm}.json"


def discover(channel: str) -> dict | None:
    """Return first available account for the channel, or None."""
    index = _accounts_index(channel)
    if not index.exists():
        return None
    try:
        ids: list[str] = json.loads(index.read_text())
    except Exception:
        return None
    for raw_id in ids:
        f = _account_file(channel, raw_id)
        if not f.exists():
            # also try raw filename (legacy)
            f_raw = _channel_dir(channel) / "accounts" / f"{raw_id}.json"
            if f_raw.exists():
                f = f_raw
            else:
                continue
        try:
            data = json.loads(f.read_text())
            return {
                "account_id": raw_id,
                "normalized_id": _normalize_account_id(raw_id),
                "token": data.get("token", ""),
                "base_url": data.get("baseUrl", ""),
                "user_id": data.get("userId", ""),
                "saved_at": data.get("savedAt", ""),
            }
        except Exception:
            continue
    return None


# ── POC main ──────────────────────────────────────────────────────────────────

CHANNEL = "openclaw-weixin"
NPX_CMD = ["npx", "-y", "@tencent-weixin/openclaw-weixin-cli@latest", "install"]


def check_npx() -> bool:
    return shutil.which("npx") is not None


def run_install() -> int:
    """Run openclaw-weixin install with inherited TTY. Returns exit code."""
    print("\n[POC] 运行 openclaw-weixin 安装向导...")
    print(f"[POC] 命令: {' '.join(NPX_CMD)}\n")
    result = subprocess.run(NPX_CMD)
    return result.returncode


def wait_for_account(timeout: int = 30) -> dict | None:
    """Poll until account file appears (openclaw restarts gateway after install)."""
    print(f"\n[POC] 等待账号文件写入（最多 {timeout}s）...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        acct = discover(CHANNEL)
        if acct:
            return acct
        time.sleep(1)
        print(".", end="", flush=True)
    print()
    return None


def main() -> None:
    relogin = "--relogin" in sys.argv

    print("=" * 60)
    print("POC: ghost weixin onboarding")
    print("=" * 60)

    # Step 1: Check npx
    print("\n[1/3] 检查 npx...")
    if not check_npx():
        print("✗  npx 未找到。请先安装 Node.js: https://nodejs.org")
        sys.exit(1)
    print("✓  npx 可用")

    # Step 2: Check existing account
    existing = discover(CHANNEL)
    if existing and not relogin:
        print(f"\n[2/3] 已找到现有账号，跳过安装")
        print(f"      account_id  : {existing['account_id']}")
        print(f"      normalized  : {existing['normalized_id']}")
        print(f"      user_id     : {existing['user_id']}")
        print(f"      base_url    : {existing['base_url']}")
        print(f"      saved_at    : {existing['saved_at']}")
        token_preview = existing['token'][:20] + "..." if existing['token'] else "(empty)"
        print(f"      token       : {token_preview}")
        account = existing
    else:
        if relogin:
            print("\n[2/3] --relogin 指定，重新登录...")
        else:
            print("\n[2/3] 未找到账号，开始安装流程...")

        rc = run_install()
        if rc != 0:
            print(f"\n✗  openclaw-weixin install 退出码 {rc}")
            sys.exit(rc)

        account = wait_for_account()
        if account is None:
            print("✗  30s 内未检测到账号文件，安装可能未完成")
            sys.exit(1)

        print(f"\n✓  账号已写入")
        print(f"      account_id : {account['account_id']}")
        print(f"      user_id    : {account['user_id']}")

    # Step 3: Validate discover() works as WeixinAdapter would use it
    print("\n[3/3] 验证 openclaw.accounts.discover() 接口...")
    check = discover(CHANNEL)
    assert check is not None, "discover() returned None after install"
    assert check["token"], "token is empty"
    assert check["base_url"], "base_url is empty"
    print("✓  discover() 返回完整账号数据")
    print(f"      base_url : {check['base_url']}")
    print(f"      token OK : {bool(check['token'])}")

    print("\n✅ POC 通过 — ghost weixin 流程可行")
    print("   下一步: 按 proposal 实现正式代码")


if __name__ == "__main__":
    main()
