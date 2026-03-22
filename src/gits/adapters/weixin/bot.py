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

logger = logging.getLogger("gits.weixin")

# ── openclaw-weixin account paths ──────────────────────────────────────────────
_OPENCLAW_WEIXIN_DIR = Path("~/.openclaw/openclaw-weixin").expanduser()
_ACCOUNTS_DIR = _OPENCLAW_WEIXIN_DIR / "accounts"

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


# ── Account discovery ──────────────────────────────────────────────────────────

def discover_account() -> dict | None:
    """Return the first available openclaw-weixin account, or None."""
    accounts_json = _OPENCLAW_WEIXIN_DIR / "accounts.json"
    if not accounts_json.exists():
        return None
    try:
        account_ids: list[str] = json.loads(accounts_json.read_text())
    except Exception:
        return None
    for account_id in account_ids:
        acct_file = _ACCOUNTS_DIR / f"{account_id}.json"
        if not acct_file.exists():
            continue
        try:
            data = json.loads(acct_file.read_text())
            return {
                "account_id": account_id,
                "token": data["token"],
                "base_url": data["baseUrl"].rstrip("/"),
                "user_id": data.get("userId", ""),
            }
        except Exception:
            continue
    return None


def _load_sync_buf(account_id: str) -> str:
    sync_file = _ACCOUNTS_DIR / f"{account_id}.sync.json"
    if sync_file.exists():
        try:
            data = json.loads(sync_file.read_text())
            return data.get("get_updates_buf", "")
        except Exception:
            pass
    return ""


def _save_sync_buf(account_id: str, buf: str) -> None:
    try:
        sync_file = _ACCOUNTS_DIR / f"{account_id}.sync.json"
        sync_file.write_text(json.dumps({"get_updates_buf": buf}))
    except Exception:
        pass


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
        if text:
            await self._adapter.send_message(
                self.channel_id, OutgoingMessage(text=str(text))
            )

    async def defer(self, **kwargs: Any) -> None:
        pass


# ── Adapter ────────────────────────────────────────────────────────────────────

class WeixinAdapter(PlatformAdapter):
    """WeChat IM Bot adapter.

    Polls ilinkai long-poll API for incoming messages and sends replies.
    Token + base URL are auto-discovered from ~/.openclaw/openclaw-weixin/.
    """

    def __init__(self, default_path: str | None = None) -> None:
        self._default_path_val = default_path
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
        self._sync_buf: str = _load_sync_buf(self._account_id)

        # context_token cache: user_id → context_token (required for replies)
        self._context_tokens: dict[str, str] = {}

        self._message_callbacks: list[MessageCallback] = []
        self._button_callbacks: list[ButtonCallback] = []
        self._engine: Any = None
        self._running = False
        self._session: aiohttp.ClientSession | None = None

        logger.info(
            "WeixinAdapter: account=%s base_url=%s", self._account_id, self._base_url
        )

    def set_engine(self, engine: Any) -> None:
        self._engine = engine

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

        if msg.text:
            for chunk in _split_text(msg.text, 3800):
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
            _save_sync_buf(self._account_id, new_buf)

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

        text = _extract_text(raw)
        logger.info("WeChat msg from=%s text=%r", from_user, text)

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
        # Auto-bind to default path if configured
        default_path = self._default_path()
        if default_path:
            logger.info("WeChat: auto-binding %s to default path %s", user_id, default_path)
            iact = _WeixinInteraction(self, user_id)
            await self._engine.handle_bind(user_id, default_path, iact)
            # After binding, forward the original message if it was plain text
            if text and not text.startswith("/"):
                binding = self._engine.session_mgr.get_binding(user_id)
                if binding:
                    incoming = IncomingMessage(
                        platform="weixin",
                        channel_id=user_id,
                        user_id=user_id,
                        text=text,
                    )
                    for cb in self._message_callbacks:
                        await cb(incoming)
            return

        # No default path — reply with guidance
        await self._send_text(
            user_id,
            "👋 Ghost 已就绪！\n\n发送 /bind <项目路径> 绑定目录\n发送 /ss 截图终端\n发送 /help 查看所有命令",
            self._context_tokens.get(user_id),
        )

    def _default_path(self) -> str | None:
        """Return the configured default path if it exists."""
        from pathlib import Path
        path = self._default_path_val or ""
        if path:
            p = Path(path).expanduser().resolve()
            if p.is_dir():
                return str(p)
        return None

    async def _handle_command(self, user_id: str, text: str) -> None:
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        iact = _WeixinInteraction(self, user_id)
        eng = self._engine

        try:
            if cmd == "/bind":
                await eng.handle_bind(user_id, arg or None, iact)
            elif cmd == "/unbind":
                await eng.handle_unbind(user_id, iact)
            elif cmd in ("/ss", "/screenshot"):
                await eng.handle_screenshot(user_id, iact)
            elif cmd == "/info":
                await eng.handle_status(user_id, iact)
            elif cmd == "/keys":
                await eng.handle_keys(user_id, arg, iact)
            elif cmd == "/enter":
                await eng.handle_enter(user_id, iact)
            elif cmd == "/esc":
                await eng.handle_esc(user_id, iact)
            elif cmd == "/new":
                await eng.handle_new(user_id, iact)
            elif cmd == "/done":
                await eng.handle_done(user_id, iact)
            elif cmd == "/bash":
                await eng.handle_bash(user_id, arg, iact)
            elif cmd == "/model":
                await eng.handle_model(user_id, arg, iact)
            elif cmd in ("/help", "/?"):
                await iact.send(_HELP_TEXT)
            else:
                await iact.send(f"未知命令: {cmd}\n发送 /help 查看可用命令")
        except Exception:
            logger.exception("WeixinAdapter: command %s failed", cmd)
            await iact.send(f"命令执行失败: {cmd}")

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
        except Exception as exc:
            logger.error("WeixinAdapter send error: %s", exc)

    # ── Class utilities ────────────────────────────────────────────────────────

    @staticmethod
    def is_available() -> bool:
        """Return True if an openclaw-weixin account exists."""
        return (_OPENCLAW_WEIXIN_DIR / "accounts.json").exists()


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


def _split_text(text: str, limit: int) -> list[str]:
    """Split text into chunks no longer than limit characters."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


_HELP_TEXT = """\
Ghost 微信命令:
/bind <路径>  绑定项目目录
/unbind       解除绑定
/ss           截图终端
/enter        发送 Enter
/esc          发送 Escape
/keys <按键>  发送按键
/bash <命令>  执行 bash 命令
/new          新建会话
/done         结束会话
/model <名称> 切换模型
/info         查看状态
直接发文字    转发到终端"""
