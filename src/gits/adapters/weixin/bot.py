"""WeChat IM Bot adapter — long-poll getUpdates + sendMessage via ilinkai API.

Token is auto-discovered from ~/.openclaw/openclaw-weixin/accounts/.
No manual configuration needed if openclaw-weixin is installed and logged in.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import random
import tempfile
import uuid
from base64 import b64encode
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp

from ..base import (
    ButtonCallback,
    IncomingMessage,
    MessageCallback,
    OutgoingMessage,
    PlatformAdapter,
)
from ...openclaw import accounts as _accounts
from . import whitelist as _whitelist

logger = logging.getLogger("gits.weixin")

_WEIXIN_CHANNEL = "openclaw-weixin"

# ── API constants ──────────────────────────────────────────────────────────────
_LONG_POLL_TIMEOUT_S = 35
_API_TIMEOUT_S = 15
_CHANNEL_VERSION = "0.0.0"
_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"

# Proto enum values (from openclaw-weixin source)
_MSG_TYPE_BOT = 2
_MSG_STATE_FINISH = 2
_ITEM_TYPE_TEXT = 1
_ITEM_TYPE_IMAGE = 2
_ITEM_TYPE_VOICE = 3
_UPLOAD_MEDIA_IMAGE = 1


# ── Helpers ────────────────────────────────────────────────────────────────────

def _random_uin() -> str:
    """X-WECHAT-UIN header: random uint32 → decimal string → base64."""
    n = random.randint(0, 0xFFFF_FFFF)
    return b64encode(str(n).encode()).decode()


def _make_headers(token: str, body: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {token}",
        "Content-Length": str(len(body.encode())),
        "X-WECHAT-UIN": _random_uin(),
    }


def _base_info() -> dict:
    return {"channel_version": _CHANNEL_VERSION}


# ── Account discovery (delegates to openclaw.accounts) ─────────────────────────

def discover_account() -> dict | None:
    """Return the first available openclaw-weixin account, or None."""
    return _accounts.discover(_WEIXIN_CHANNEL)


def discover_all_accounts() -> list[dict]:
    """Return all available openclaw-weixin accounts."""
    return _accounts.discover_all(_WEIXIN_CHANNEL)


# ── Fake interaction object for engine compatibility ───────────────────────────

class _WeixinInteraction:
    """Minimal interaction shim so engine handle_* methods can reply."""

    def __init__(self, adapter: "WeixinAdapter", channel_id: str) -> None:
        self._adapter = adapter
        self.channel_id = channel_id
        # engine calls interaction.followup.send(text) or interaction.channel.send(text)
        self.followup = self
        self.channel = self
        # engine uses channel.name as the tmux window name
        self.name = f"wx-{channel_id[:8]}"

    async def send(self, text: str = "", **kwargs: Any) -> None:
        # Suppress generic English confirmations from the engine (WeChat gets a
        # Chinese confirmation + screenshot from _handle_unbound / _handle_command)
        _SKIP = {"Bound successfully.", "Unbound.", "Done."}
        t = str(text).strip() if text else ""
        if not t or t in _SKIP:
            return
        # Suppress the English session picker confirmation — WeChat already shows
        # the numbered list from select_options; this trailing text is redundant.
        if "existing session" in t and "Pick one" in t:
            return
        # Suppress the "please provide a path" error — WeChat _handle_bind handles
        # the no-arg case itself with Chinese guidance.
        if "Please provide a path" in t:
            return
        await self._adapter.send_message(
            self.channel_id, OutgoingMessage(text=t)
        )

    async def defer(self, **kwargs: Any) -> None:
        pass


# ── Adapter ────────────────────────────────────────────────────────────────────

class WeixinAdapter(PlatformAdapter):
    """WeChat IM Bot adapter.

    Polls ilinkai long-poll API for incoming messages and sends replies.
    Token + base URL are auto-discovered from ~/.openclaw/openclaw-weixin/.
    """

    def __init__(
        self,
        default_path: str | None = None,
        account: dict | None = None,
        workspace_root: str | None = None,
    ) -> None:
        self._default_path_val = default_path
        self._workspace_root = workspace_root  # None = no restriction
        if account is None:
            account = discover_account()
        if account is None:
            raise RuntimeError(
                "No openclaw-weixin account found. "
                "Run: npx -y @tencent-weixin/openclaw-weixin-cli@latest install"
            )

        self._account_id: str = account["account_id"]
        self._token: str = account["token"]
        self._base_url: str = account["base_url"]
        self._bot_user_id: str = account["user_id"]

        # Persist get_updates cursor across restarts
        self._sync_buf: str = _accounts.load_sync_buf(_WEIXIN_CHANNEL, self._account_id)

        # context_token cache: user_id → context_token (required for replies)
        self._context_tokens: dict[str, str] = {}

        self._message_callbacks: list[MessageCallback] = []
        self._button_callbacks: list[ButtonCallback] = []
        self._engine: Any = None
        self._running = False
        self._session: aiohttp.ClientSession | None = None
        # Pending numbered select: user_id → list of callback_data values
        self._pending_select: dict[str, list[str]] = {}
        # Pending /bind fuzzy selection: user_id → list of absolute paths
        self._pending_bind: dict[str, list[str]] = {}
        # Track users who have already received the welcome message
        self._greeted_users: set[str] = set()
        # Callback invoked when a new bot account is successfully registered
        self._new_account_cb: Any = None

        logger.info(
            "WeixinAdapter: account=%s base_url=%s", self._account_id, self._base_url
        )

    def set_engine(self, engine: Any) -> None:
        self._engine = engine

    def set_new_account_callback(self, cb: Any) -> None:
        """Register an async callback(account: dict) called when /addbot succeeds."""
        self._new_account_cb = cb

    # ── PlatformAdapter interface ──────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        # Long-poll needs a generous total timeout
        timeout = aiohttp.ClientTimeout(total=_LONG_POLL_TIMEOUT_S + 10)
        self._session = aiohttp.ClientSession(timeout=timeout)
        logger.info("WeixinAdapter started, entering poll loop")
        await self._poll_loop()

    async def stop(self) -> None:
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None

    async def send_message(self, channel_id: str, msg: OutgoingMessage) -> str:
        user_id = channel_id
        ctx = self._context_tokens.get(user_id)

        if msg.image:
            try:
                await self._send_image(user_id, msg.image, ctx)
            except Exception:
                logger.exception("WeixinAdapter: image send failed, falling back to text")
                await self._send_text(user_id, "[截图发送失败，请查看终端]", ctx)
            return ""

        # Convert select_options to numbered text list (WeChat has no dropdowns)
        if msg.select_options:
            _HEADER_ZH = {
                "Resume Session?": "选择要恢复的会话",
                "Select a session to resume…": "选择要恢复的会话",
            }
            lines = []
            if msg.text:
                # Strip markdown, keep first line only as header
                header = msg.text.splitlines()[0].replace("**", "").replace("`", "")
                header = _HEADER_ZH.get(header, header)
                lines.append(header)
                lines.append("")
            _LABEL_ZH = {
                "＋ New Session": "＋ 新会话",
                "+ New Session": "＋ 新会话",
            }
            _DESC_ZH = {
                "Start a fresh session in this directory": "在此目录启动全新会话",
            }
            for i, opt in enumerate(msg.select_options):
                label = _LABEL_ZH.get(opt.label, opt.label)
                raw_desc = opt.description or ""
                desc = f" — {_DESC_ZH.get(raw_desc, raw_desc)}" if raw_desc else ""
                lines.append(f"{i}. {label}{desc}")
            lines.append("\n回复数字选择")
            await self._send_text(user_id, "\n".join(lines), ctx)
            # Store the options so _dispatch can map number → callback_data
            self._pending_select[user_id] = [opt.value for opt in msg.select_options]
            return ""

        if msg.text:
            # Suppress Discord-style bind confirmations (screenshot replaces them)
            text = msg.text
            if "tmux:" in text and ("Bound " in text or "Resuming session" in text):
                return ""
            for chunk in _split_text(text, 3800):
                await self._send_text(user_id, chunk, ctx)

        return ""

    async def edit_message(
        self, channel_id: str, message_id: str, msg: OutgoingMessage
    ) -> None:
        # WeChat doesn't support editing — send as new message
        await self.send_message(channel_id, msg)

    async def delete_message(self, channel_id: str, message_id: str) -> None:
        pass  # Not supported

    def on_message(self, callback: MessageCallback) -> None:
        self._message_callbacks.append(callback)

    def on_button_click(self, callback: ButtonCallback) -> None:
        self._button_callbacks.append(callback)

    async def create_thread(
        self, channel_id: str, title: str, auto_archive_minutes: int = 10080
    ) -> str:
        # WeChat direct messages have no threads; return same channel
        return channel_id

    async def archive_thread(self, thread_id: str) -> None:
        pass

    # ── Long-poll loop ─────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("WeixinAdapter poll error: %s", exc)
                await asyncio.sleep(5)

    async def _poll_once(self) -> None:
        assert self._session is not None
        url = f"{self._base_url}/ilink/bot/getupdates"
        payload = json.dumps(
            {"get_updates_buf": self._sync_buf, "base_info": _base_info()}
        )
        headers = _make_headers(self._token, payload)

        try:
            async with self._session.post(
                url, data=payload, headers=headers
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except asyncio.TimeoutError:
            # Normal for long-poll — server held and then we timed out
            return
        except aiohttp.ServerTimeoutError:
            return

        new_buf: str = data.get("get_updates_buf", "")
        if new_buf:
            self._sync_buf = new_buf
            _accounts.save_sync_buf(_WEIXIN_CHANNEL, self._account_id, new_buf)

        msgs: list[dict] = data.get("msgs") or []
        for raw_msg in msgs:
            try:
                await self._dispatch(raw_msg)
            except Exception:
                logger.exception("WeixinAdapter: error dispatching message")

    # ── Message dispatch ───────────────────────────────────────────────────────

    async def _dispatch(self, raw: dict) -> None:
        from_user = raw.get("from_user_id", "")
        if not from_user:
            return

        # Cache context_token — must be echoed in every reply
        ctx_token: str | None = raw.get("context_token")
        if ctx_token:
            self._context_tokens[from_user] = ctx_token

        # Handle voice messages
        if _is_voice_message(raw):
            ctx = self._context_tokens.get(from_user)
            voice_text = _extract_voice_text(raw)
            if voice_text:
                # WeChat provided transcription — echo it and fall through as text
                await self._send_text(from_user, f"🎙️ 识别到语音：「{voice_text}」", ctx)
            else:
                await self._send_text(
                    from_user,
                    "小鬼暂时无法识别语音内容，建议在手机上用语音转文字后再发过来～",
                    ctx,
                )
                return

        # Handle incoming images — download, save, forward to Claude as @path
        image_item = _extract_image_item(raw)
        if image_item:
            asyncio.create_task(self._handle_incoming_image(from_user, image_item))
            return

        text = _extract_text(raw)
        logger.info("WeChat msg from=%s text=%r", from_user, text)

        # ── Admin bootstrap ─────────────────────────────────────────────────
        # Only auto-promote if this is the initial bot (not locked by /share)
        if _whitelist.is_empty(self._account_id) and not _whitelist.is_locked(self._account_id):
            _whitelist.add_admin(self._account_id, from_user)
            logger.info("WeChat: first user %s set as admin (account=%s)", from_user, self._account_id)

        # /bind fuzzy selection reply
        if text and text.strip().isdigit() and from_user in self._pending_bind:
            paths = self._pending_bind.pop(from_user)
            idx = int(text.strip())
            if 0 <= idx < len(paths):
                iact = _WeixinInteraction(self, from_user)
                if self._engine.session_mgr.get_binding(from_user):
                    self._engine.monitor.stop_polling(from_user)
                    await self._engine.session_mgr.unbind(from_user)
                await self._engine.handle_bind(from_user, paths[idx], iact, mode="bypassPermissions")
            else:
                ctx = self._context_tokens.get(from_user)
                await self._send_text(from_user, f"无效选项，请回复 0~{len(paths)-1}", ctx)
            return

        # Numbered select reply (e.g. user replies "1" to a session picker)
        if text and text.strip().isdigit() and from_user in self._pending_select:
            opts = self._pending_select.pop(from_user)
            idx = int(text.strip())
            if 0 <= idx < len(opts):
                callback_data = opts[idx]
                for cb in self._button_callbacks:
                    await cb(from_user, from_user, callback_data)
            else:
                ctx = self._context_tokens.get(from_user)
                await self._send_text(from_user, f"无效选项，请回复 0~{len(opts)-1}", ctx)
            return

        # Command handling (takes priority)
        if text and text.lstrip().startswith("/") and self._engine:
            await self._handle_command(from_user, text.strip())
            return

        # Check if channel is bound; auto-bind to default path if configured
        if self._engine:
            binding = self._engine.session_mgr.get_binding(from_user)
            if binding is None:
                await self._handle_unbound(from_user, text)
                return

        # Immediately acknowledge receipt so the user knows the message landed
        _ACKS = ["好的👌", "收到", "嗯嗯", "好的，我看看", "收到了", "好"]
        ctx = self._context_tokens.get(from_user)
        await self._send_text(from_user, random.choice(_ACKS), ctx)

        # Forward plain text to engine (tmux bridge)
        incoming = IncomingMessage(
            platform="weixin",
            channel_id=from_user,
            user_id=from_user,
            text=text,
            raw=raw,
        )
        for cb in self._message_callbacks:
            await cb(incoming)

    async def _handle_unbound(self, user_id: str, text: str | None) -> None:
        """Called when a message arrives but the channel has no binding."""
        ctx = self._context_tokens.get(user_id)
        # Send welcome message on first contact
        if user_id not in self._greeted_users:
            self._greeted_users.add(user_id)
            await self._send_text(user_id, _WELCOME_TEXT, ctx)

        # Auto-bind to default path if configured
        default_path = self._default_path()
        if default_path:
            logger.info("WeChat: auto-binding %s to default path %s", user_id, default_path)
            iact = _WeixinInteraction(self, user_id)
            # Restore last session_id if saved by ghost reset-weixin
            import json as _json
            from pathlib import Path as _Path
            _sessions_file = _Path("~/.gits/weixin_sessions.json").expanduser()
            _last_sid: str | None = None
            if _sessions_file.exists():
                try:
                    _last_sid = _json.loads(_sessions_file.read_text()).get(user_id)
                except Exception:
                    pass
            if _last_sid:
                await self._send_text(user_id, "⏳ 找到上次的会话，正在恢复…", ctx)
                await self._engine.handle_bind(
                    user_id, default_path, iact,
                    fresh=True, mode="bypassPermissions",
                    session_id=_last_sid,
                )
                # Wait and check if resume actually succeeded
                await asyncio.sleep(4)
                binding = self._engine.session_mgr.get_binding(user_id)
                if binding:
                    current_cmd = await self._engine.tmux.pane_current_command(
                        binding.window_id
                    )
                    if current_cmd and current_cmd.lower() in {"zsh", "bash", "sh", "fish"}:
                        logger.info(
                            "WeChat: resume failed for %s (CLI exited), retrying fresh",
                            user_id,
                        )
                        await self._send_text(user_id, "⚠️ 会话已过期，正在启动新会话…", ctx)
                        await self._engine.handle_bind(
                            user_id, default_path, iact,
                            fresh=True, mode="bypassPermissions",
                            session_id=None,
                        )
                    else:
                        await self._send_text(user_id, "✅ 已恢复上次会话", ctx)
            else:
                await self._send_text(user_id, "⏳ 正在启动新会话…", ctx)
                await self._engine.handle_bind(
                    user_id, default_path, iact,
                    fresh=True, mode="bypassPermissions",
                    session_id=None,
                )

            # Send help text so user knows what commands are available
            ctx = self._context_tokens.get(user_id)
            await self._send_text(user_id, _HELP_TEXT, ctx)
            # Don't forward the first message — it triggered onboarding,
            # not a task. Let the user send their task after seeing the screenshot.
            return

        # No default path — reply with guidance
        await self._send_text(
            user_id,
            "发送 /bind <项目路径> 告诉小鬼去哪里工作～\n例如：/bind /Users/me/myproject",
            self._context_tokens.get(user_id),
        )

    def _default_path(self) -> str | None:
        """Return the auto-bind path: workspace_root takes priority over default_path."""
        from pathlib import Path
        for candidate in (self._workspace_root, self._default_path_val):
            if candidate:
                p = Path(candidate).expanduser().resolve()
                if p.is_dir():
                    return str(p)
        return None

    async def _handle_bind(self, user_id: str, arg: str, iact: "_WeixinInteraction") -> None:
        """Handle /bind with fuzzy project search under the default/workspace root."""
        from pathlib import Path as _P

        eng = self._engine

        # Auto-unbind existing session before rebinding (silent, no reply)
        if eng.session_mgr.get_binding(user_id):
            eng.monitor.stop_polling(user_id)
            await eng.session_mgr.unbind(user_id)

        # No arg → use default/workspace path and let session picker show
        if not arg:
            default = self._default_path()
            if default:
                await eng.handle_bind(user_id, default, iact, mode="bypassPermissions")
            else:
                ctx = self._context_tokens.get(user_id)
                await self._send_text(
                    user_id,
                    "发送 /bind <项目路径> 告诉小鬼去哪里工作～\n例如：/bind /Users/me/myproject",
                    ctx,
                )
            return

        # Absolute path → workspace boundary check then bind
        if arg.startswith("/") or arg.startswith("~"):
            if self._workspace_root:
                req = _P(arg).expanduser().resolve()
                ws = _P(self._workspace_root).expanduser().resolve()
                try:
                    req.relative_to(ws)
                except ValueError:
                    await iact.send(
                        f"只能在你的工作空间内操作 👇\n{self._workspace_root}\n\n"
                        f"例如：/bind {self._workspace_root}/myproject"
                    )
                    return
            await eng.handle_bind(user_id, arg, iact, mode="bypassPermissions")
            return

        # Fuzzy search under search root
        search_root_str = self._workspace_root or self._default_path_val or ""
        if not search_root_str:
            await eng.handle_bind(user_id, arg, iact, mode="bypassPermissions")
            return

        search_root = _P(search_root_str).expanduser().resolve()
        if not search_root.is_dir():
            await eng.handle_bind(user_id, arg, iact, mode="bypassPermissions")
            return

        matches = sorted(
            [d for d in search_root.iterdir() if d.is_dir() and arg.lower() in d.name.lower()],
            key=lambda d: d.name.lower(),
        )

        if not matches:
            ctx = self._context_tokens.get(user_id)
            await self._send_text(user_id, f"在 {search_root} 下没找到包含 '{arg}' 的目录", ctx)
            return

        if len(matches) == 1:
            await eng.handle_bind(user_id, str(matches[0]), iact, mode="bypassPermissions")
            return

        # Multiple matches — let user pick
        lines = [f"找到 {len(matches)} 个目录，回复数字选择：\n"]
        for i, d in enumerate(matches):
            lines.append(f"{i}. {d.name}")
        ctx = self._context_tokens.get(user_id)
        await self._send_text(user_id, "\n".join(lines), ctx)
        self._pending_bind[user_id] = [str(d) for d in matches]

    async def _forward_and_screenshot(self, user_id: str, cli_command: str) -> None:
        """Send a CLI slash command to tmux, wait, press Esc, then screenshot."""
        eng = self._engine
        binding = eng.session_mgr.get_binding(user_id)
        if binding is None:
            ctx = self._context_tokens.get(user_id)
            await self._send_text(user_id, "还没绑定会话，先发 /bind <路径>", ctx)
            return

        cmd = f"/{cli_command}"
        from ...core.engine import _submit_keys_for_cli  # type: ignore[attr-defined]
        submit = _submit_keys_for_cli(binding.coding_cli)
        await eng.tmux.send_text(binding.window_id, cmd, submit_keys=submit)
        await asyncio.sleep(2)

        iact = _WeixinInteraction(self, user_id)
        # Screenshot 1: show the command output
        await eng.handle_screenshot(user_id, iact)

        # Then dismiss and screenshot 2: confirm back to normal
        ctx = self._context_tokens.get(user_id)
        await self._send_text(user_id, "看完啦～帮你退出切回正常状态 👻", ctx)
        await eng.tmux.send_keys(binding.window_id, "Escape")
        await asyncio.sleep(0.5)
        await eng.handle_screenshot(user_id, iact)

    async def _handle_command(self, user_id: str, text: str) -> None:
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        iact = _WeixinInteraction(self, user_id)
        eng = self._engine

        try:
            if cmd == "/bind":
                await self._handle_bind(user_id, arg, iact)
            elif cmd in ("/ss", "/screenshot", "/s"):
                await eng.handle_screenshot(user_id, iact)
            elif cmd in ("/info", "/i"):
                await eng.handle_status(user_id, iact)
            elif cmd == "/keys":
                await eng.handle_keys(user_id, arg, iact)
            elif cmd in ("/enter", "/e"):
                await eng.handle_enter(user_id, iact)
            elif cmd in ("/esc", "/x"):
                await eng.handle_esc(user_id, iact)
            elif cmd == "/bash":
                await eng.handle_bash(user_id, arg, iact)
            elif cmd == "/mode":
                _mode = arg or "bypassPermissions"
                if _mode == "yolo":
                    _mode = "bypassPermissions"
                await eng.handle_mode(user_id, _mode, iact)
            elif cmd == "/model":
                await eng.handle_model(user_id, arg, iact)
            elif cmd == "/share":
                if not _whitelist.is_admin(self._account_id, user_id):
                    await iact.send("只有管理员才能共享小鬼～")
                else:
                    grant_admin = arg.strip().lower() == "admin"
                    asyncio.create_task(self._do_addbot_login(user_id, grant_admin=grant_admin))
            elif cmd in ("/compact", "/c"):
                await self._forward_and_screenshot(user_id, "compact")
            elif cmd == "/clear":
                await self._forward_and_screenshot(user_id, "clear")
            elif cmd in ("/cost", "/price"):
                await self._forward_and_screenshot(user_id, "cost")
            elif cmd in ("/context", "/ctx"):
                await self._forward_and_screenshot(user_id, "context")
            elif cmd == "/diff":
                await self._forward_and_screenshot(user_id, "diff")
            elif cmd == "/usage":
                await self._forward_and_screenshot(user_id, "usage")
            elif cmd in ("/ctrlc", "/q", "/cancel", "/abort"):
                await eng.handle_keys(user_id, "C-c", iact)
            elif cmd in ("/help", "/?"):
                await iact.send(_HELP_TEXT)
            else:
                await iact.send(f"小鬼不认识这个命令: {cmd}\n发送 /help 看看能做什么～")
        except Exception:
            logger.exception("WeixinAdapter: command %s failed", cmd)
            await iact.send(f"哎呀，{cmd} 执行出错了，看看终端是啥情况？")

    # ── /addbot login flow ─────────────────────────────────────────────────────

    async def _do_addbot_login(self, admin_user_id: str, grant_admin: bool = False) -> None:
        """Fetch a WeChat login QR code, send it to admin, poll for completion.

        grant_admin: if False (default), the new bot is locked — its first user
        will NOT be auto-promoted to admin and cannot /share further.
        If True (/share admin), the first user of the new bot becomes admin.
        """
        _ILINKAI = "https://ilinkai.weixin.qq.com"
        ctx = self._context_tokens.get(admin_user_id)

        try:
            await self._send_text(
                admin_user_id,
                "⚠️ 注意：/share 是团队协作功能。\n"
                "朋友将在你的机器上运行，可访问文件系统和执行命令。\n"
                "请只共享给你信任的团队成员。\n\n"
                "⏳ 正在获取登录二维码…",
                ctx,
            )

            # 1. Request QR code
            login_timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=login_timeout) as s:
                async with s.get(
                    f"{_ILINKAI}/ilink/bot/get_bot_qrcode?bot_type=3"
                ) as resp:
                    resp.raise_for_status()
                    qr_resp = await resp.json(content_type=None)

            qrcode_id: str = qr_resp.get("qrcode", "")
            qrcode_url: str = (
                qr_resp.get("qrcode_img_content")
                or qr_resp.get("qrcode_url")
                or ""
            )
            if not qrcode_id or not qrcode_url:
                await self._send_text(admin_user_id, "❌ 获取二维码失败，请重试", ctx)
                logger.error("addbot: get_bot_qrcode returned %s", qr_resp)
                return

            # 2. Send login URL + QR code image
            await self._send_text(
                admin_user_id,
                "📱 把下面的链接或二维码发给朋友，让他登录自己的小鬼（2 分钟内有效）",
                ctx,
            )
            await self._send_text(admin_user_id, qrcode_url, ctx)
            await self.send_message(admin_user_id, OutgoingMessage(image=_make_login_qr(qrcode_url)))
            await self._send_text(admin_user_id, "⏳ 等待登录中…", ctx)

            # 3. Long-poll for scan confirmation
            deadline = asyncio.get_event_loop().time() + 120
            status_resp: dict = {}
            confirmed = False
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                while asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(3)
                    try:
                        async with s.get(
                            f"{_ILINKAI}/ilink/bot/get_qrcode_status?qrcode={qrcode_id}"
                        ) as resp:
                            status_resp = await resp.json(content_type=None)
                    except Exception as exc:
                        logger.warning("addbot: poll error: %s", exc)
                        continue

                    status = str(status_resp.get("status", ""))
                    if status in ("confirmed", "success", "2"):
                        confirmed = True
                        break
                    if status in ("expired", "cancelled", "-1"):
                        await self._send_text(admin_user_id, "❌ 二维码已过期，请重新发送 /share", ctx)
                        return

            if not confirmed:
                await self._send_text(admin_user_id, "❌ 等待超时（2 分钟），请重新发送 /share", ctx)
                return

            # 4. Extract credentials
            token: str = status_resp.get("bot_token") or status_resp.get("token") or ""
            new_base_url: str = (
                status_resp.get("baseurl") or status_resp.get("base_url") or _ILINKAI
            ).rstrip("/")
            new_user_id: str = (
                status_resp.get("ilink_user_id") or status_resp.get("user_id") or ""
            )
            account_id: str = (
                status_resp.get("ilink_bot_id") or status_resp.get("bot_id") or new_user_id
            )

            if not token:
                await self._send_text(admin_user_id, "❌ 扫码成功但未获取到 token，请重试", ctx)
                logger.error("addbot: no token in status_resp %s", status_resp)
                return

            # 5. Save account
            import datetime as _dt
            account = {
                "account_id": account_id,
                "token": token,
                "base_url": new_base_url,
                "user_id": new_user_id,
                "saved_at": _dt.datetime.utcnow().isoformat() + "Z",
            }
            _accounts.save(_WEIXIN_CHANNEL, account)
            logger.info("addbot: new account saved: %s (grant_admin=%s)", account_id, grant_admin)

            # Use the same default path as the primary bot (stays within ALLOWED_PATHS)
            ws_path_str = self._default_path() or ""
            if ws_path_str:
                _whitelist.set_workspace(account_id, ws_path_str)
            logger.info("addbot: workspace set to default path: %s", ws_path_str)

            # Lock the new bot unless admin rights were explicitly granted
            if not grant_admin:
                _whitelist.lock(account_id)

            # 6. Notify caller to start new adapter
            if self._new_account_cb:
                await self._new_account_cb(account)

            ctx = self._context_tokens.get(admin_user_id)
            admin_note = "（已授权管理员权限）" if grant_admin else "（普通使用权限）"
            await self._send_text(
                admin_user_id,
                f"✅ 新微信账号已成功注册！{admin_note}\n"
                f"账号: {account_id}\n"
                f"朋友的小鬼已启动，他发消息就能用了～",
                ctx,
            )

        except Exception:
            logger.exception("WeixinAdapter: /addbot login failed")
            ctx = self._context_tokens.get(admin_user_id)
            await self._send_text(admin_user_id, "❌ 添加账号失败，请查看日志", ctx)

    # ── HTTP send ──────────────────────────────────────────────────────────────

    async def _send_image(
        self, user_id: str, png_bytes: bytes, context_token: str | None
    ) -> None:
        if not context_token:
            logger.warning("WeixinAdapter: no contextToken for %s, cannot send image", user_id)
            return

        assert self._session is not None

        # 1. Write PNG to temp file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png_bytes)
            tmp_path = f.name

        try:
            uploaded = await self._upload_image(tmp_path, user_id)
        finally:
            os.unlink(tmp_path)

        # 3. Build and send image message
        ciphertext_size = _aes_ecb_padded_size(len(png_bytes))
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": user_id,
                "client_id": f"gits-{uuid.uuid4().hex[:16]}",
                "message_type": _MSG_TYPE_BOT,
                "message_state": _MSG_STATE_FINISH,
                "item_list": [{
                    "type": _ITEM_TYPE_IMAGE,
                    "image_item": {
                        "media": {
                            "encrypt_query_param": uploaded["download_param"],
                            "aes_key": b64encode(uploaded["aes_key_hex"].encode()).decode(),
                            "encrypt_type": 1,
                        },
                        "mid_size": ciphertext_size,
                    },
                }],
                "context_token": context_token,
            },
            "base_info": _base_info(),
        }
        payload = json.dumps(body)
        headers = _make_headers(self._token, payload)

        send_timeout = aiohttp.ClientTimeout(total=_API_TIMEOUT_S)
        async with self._session.post(
            f"{self._base_url}/ilink/bot/sendmessage",
            data=payload,
            headers=headers,
            timeout=send_timeout,
        ) as resp:
            resp.raise_for_status()
        logger.info("WeixinAdapter: image sent to %s", user_id)

    async def _upload_image(self, file_path: str, user_id: str) -> dict:
        """Upload image to WeChat CDN. Returns dict with download_param and aes_key_hex."""
        assert self._session is not None

        raw = Path(file_path).read_bytes()
        raw_size = len(raw)
        raw_md5 = hashlib.md5(raw).hexdigest()
        aes_key = os.urandom(16)
        file_size = _aes_ecb_padded_size(raw_size)
        filekey = uuid.uuid4().hex

        # 1. Get upload URL
        body = json.dumps({
            "filekey": filekey,
            "media_type": _UPLOAD_MEDIA_IMAGE,
            "to_user_id": user_id,
            "rawsize": raw_size,
            "rawfilemd5": raw_md5,
            "filesize": file_size,
            "no_need_thumb": True,
            "aeskey": aes_key.hex(),
            "base_info": _base_info(),
        })
        headers = _make_headers(self._token, body)
        upload_timeout = aiohttp.ClientTimeout(total=_API_TIMEOUT_S)

        async with self._session.post(
            f"{self._base_url}/ilink/bot/getuploadurl",
            data=body,
            headers=headers,
            timeout=upload_timeout,
        ) as resp:
            resp.raise_for_status()
            url_resp = await resp.json(content_type=None)

        upload_param = url_resp.get("upload_param")
        if not upload_param:
            raise RuntimeError(f"getuploadurl returned no upload_param: {url_resp}")

        # 2. Encrypt and upload to CDN
        ciphertext = _aes_ecb_encrypt(raw, aes_key)
        cdn_url = (
            f"{_CDN_BASE_URL}/upload"
            f"?encrypted_query_param={quote(upload_param)}"
            f"&filekey={quote(filekey)}"
        )

        async with self._session.post(
            cdn_url,
            data=ciphertext,
            headers={"Content-Type": "application/octet-stream"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as cdn_resp:
            cdn_resp.raise_for_status()
            download_param = cdn_resp.headers.get("x-encrypted-param")

        if not download_param:
            raise RuntimeError("CDN upload response missing x-encrypted-param header")

        return {"download_param": download_param, "aes_key_hex": aes_key.hex()}

    async def _handle_incoming_image(self, user_id: str, image_item: dict) -> None:
        """Download an incoming image, save to /tmp, forward @path to Claude CLI."""
        ctx = self._context_tokens.get(user_id)
        eng = self._engine
        binding = eng.session_mgr.get_binding(user_id) if eng else None
        if binding is None:
            await self._send_text(user_id, "收到图片，但还没绑定会话，先发 /bind <路径>～", ctx)
            return

        await self._send_text(user_id, "📷 收到图片，下载中…", ctx)
        try:
            img_path = await self._download_image(image_item)
        except Exception as exc:
            logger.exception("WeixinAdapter: image download failed")
            await self._send_text(user_id, f"图片下载失败了：{exc}", ctx)
            return

        # Forward as @path so Claude CLI sees the image
        from ...core.engine import _submit_keys_for_cli  # type: ignore[attr-defined]
        submit = _submit_keys_for_cli(binding.coding_cli)
        await eng.tmux.send_text(binding.window_id, f"@{img_path}", submit_keys=submit)
        logger.info("WeixinAdapter: forwarded image %s to window %s", img_path, binding.window_id)

    async def _download_image(self, image_item: dict) -> str:
        """Download and decrypt a WeChat CDN image. Returns local file path."""
        assert self._session is not None
        logger.info("WeixinAdapter: incoming image_item=%s", json.dumps(image_item))

        media = image_item.get("media", {})
        encrypt_param = media.get("encrypt_query_param", "")
        aes_key_b64 = media.get("aes_key", "")

        if not encrypt_param or not aes_key_b64:
            raise ValueError(f"image_item missing fields — media keys: {list(media.keys())}")

        # aes_key is b64(hex_string) — decode to raw bytes
        from base64 import b64decode
        aes_key = bytes.fromhex(b64decode(aes_key_b64).decode())

        cdn_url = f"{_CDN_BASE_URL}/download?encrypted_query_param={quote(encrypt_param)}"
        logger.info("WeixinAdapter: downloading image from %s", cdn_url)
        async with self._session.get(
            cdn_url,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            logger.info("WeixinAdapter: CDN response status=%s headers=%s", resp.status, dict(resp.headers))
            resp.raise_for_status()
            ciphertext = await resp.read()
        logger.info("WeixinAdapter: downloaded %d bytes, decrypting…", len(ciphertext))

        plaintext = _aes_ecb_decrypt(ciphertext, aes_key)

        img_path = f"/tmp/gits_wx_{uuid.uuid4().hex}.png"
        Path(img_path).write_bytes(plaintext)
        logger.info("WeixinAdapter: image saved to %s (%d bytes)", img_path, len(plaintext))
        return img_path

    async def _send_text(
        self, user_id: str, text: str, context_token: str | None
    ) -> None:
        if not context_token:
            logger.warning(
                "WeixinAdapter: no contextToken for %s — cannot send reply. "
                "Send a message to the bot first to establish context.",
                user_id,
            )
            return

        assert self._session is not None
        url = f"{self._base_url}/ilink/bot/sendmessage"
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": user_id,
                "client_id": f"gits-{uuid.uuid4().hex[:16]}",
                "message_type": _MSG_TYPE_BOT,
                "message_state": _MSG_STATE_FINISH,
                "item_list": [{"type": _ITEM_TYPE_TEXT, "text_item": {"text": text}}],
                "context_token": context_token,
            },
            "base_info": _base_info(),
        }
        payload = json.dumps(body)
        headers = _make_headers(self._token, payload)

        try:
            send_timeout = aiohttp.ClientTimeout(total=_API_TIMEOUT_S)
            async with self._session.post(
                url, data=payload, headers=headers, timeout=send_timeout
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
                base = data.get("base_resp", {})
                ret = base.get("ret", 0)
                if ret != 0:
                    logger.error(
                        "WeixinAdapter text send failed: ret=%s err=%s user=%s",
                        ret, base.get("err_msg", ""), user_id,
                    )
                else:
                    logger.info("WeixinAdapter: text sent to %s", user_id)
        except Exception as exc:
            logger.error("WeixinAdapter send error: %s", exc)

    # ── Class utilities ────────────────────────────────────────────────────────

    @property
    def account_id(self) -> str:
        return self._account_id

    def knows_user(self, user_id: str) -> bool:
        """Return True if this adapter has seen a message from user_id."""
        return user_id in self._context_tokens

    @staticmethod
    def is_available() -> bool:
        """Return True if an openclaw-weixin account exists."""
        return _accounts.discover(_WEIXIN_CHANNEL) is not None


# ── Module-level helpers ───────────────────────────────────────────────────────

def _extract_text(raw: dict) -> str | None:
    """Extract text from a WeChat message's item_list."""
    for item in raw.get("item_list") or []:
        if item.get("type") == _ITEM_TYPE_TEXT:
            return item.get("text_item", {}).get("text")
        if item.get("type") == _ITEM_TYPE_VOICE:
            voice = item.get("voice_item", {})
            if voice.get("text"):
                return voice["text"]
    return None


def _is_voice_message(raw: dict) -> bool:
    """Return True if the message contains a voice item."""
    for item in raw.get("item_list") or []:
        if item.get("type") == _ITEM_TYPE_VOICE:
            return True
    return False


def _extract_voice_text(raw: dict) -> str | None:
    """Return transcribed text from a voice item, or None if unavailable."""
    for item in raw.get("item_list") or []:
        if item.get("type") == _ITEM_TYPE_VOICE:
            return item.get("voice_item", {}).get("text") or None
    return None


def _aes_ecb_padded_size(plaintext_size: int) -> int:
    """AES-128-ECB ciphertext size with PKCS7 padding (same as JS aesEcbPaddedSize)."""
    return math.ceil((plaintext_size + 1) / 16) * 16


def _aes_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt bytes with AES-128-ECB + PKCS7 padding."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding

    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    enc = cipher.encryptor()
    return enc.update(padded) + enc.finalize()


def _aes_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """Decrypt AES-128-ECB + PKCS7 padded bytes."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding

    cipher = Cipher(algorithms.AES(key), modes.ECB())
    dec = cipher.decryptor()
    padded = dec.update(ciphertext) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def _extract_image_item(raw: dict) -> dict | None:
    """Return the image_item dict if the message contains an image, else None."""
    for item in raw.get("item_list") or []:
        if item.get("type") == _ITEM_TYPE_IMAGE:
            return item.get("image_item")
    return None



def _make_login_qr(url: str) -> bytes:
    """Generate a QR code PNG from a WeChat login URL."""
    import io
    import qrcode  # type: ignore[import-untyped]

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _split_text(text: str, limit: int) -> list[str]:
    """Split text into chunks no longer than limit characters."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


_WELCOME_TEXT = """\
👻 嗨～我是小鬼！

专门帮你盯着 AI 写代码的小精灵 ✨

💰 龙虾按量一直扣
小鬼靠你订阅走
Claude 买了不白花
AI 跑路你喝茶 🧋

发消息就能开始，发 /help 看看我能做什么 🐾"""

_HELP_TEXT = """\
👻 小鬼命令表:
/bind <路径>  绑定项目目录
/s            截图看看终端
/i            当前状态
/e            回车
/x            Esc
/q            中断（Ctrl-C）
/c            压缩上下文窗口
/clear        清除对话历史
/cost         查看 token 用量
/ctx          查看上下文使用量
/diff         查看代码变更
/usage        查看 API 用量
/keys <按键>  发送按键序列
/bash <命令>  执行 shell 命令
/mode [模式]  切换权限模式（yolo/auto/default），默认 yolo
/model <名称> 切换模型
/share        团队协作：共享本机给信任的成员（仅管理员）
/share admin  同上，并允许对方也能 /share
直接发文字 → 转发到终端"""
