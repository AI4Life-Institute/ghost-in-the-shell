"""OAuth Usage API client (Phase 0.8).

Per openspec change ``add-multi-account-hotswap`` (D8 + D11): ghost queries
the OAuth Usage endpoint to surface live quota state in
``gits account list`` / Discord ``/accounts``. ghost MUST NOT implement
its own OAuth refresh path — claude CLI handles refresh transparently.

Trust surface invariants enforced by this module:

* The module contains **no HTTP POST** call sites — only GETs to the
  Usage endpoint.
* The module **does not read** the ``refreshToken`` field from any
  credentials file.
* The module **does not open** any ``.credentials.json`` file for write.

The endpoint and required ``anthropic-beta`` header were empirically
verified on 2026-04-27 (see ``design.md §Reference §C``). Both are
configurable via environment variables so deployments can adapt if
Anthropic relocates the endpoint or bumps the beta version.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .account import AccountLayout

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UsageWindow:
    """A single usage window (e.g. ``five_hour`` or ``seven_day``)."""

    utilization: float | None
    resets_at: str | None  # ISO 8601 timestamp


@dataclass(frozen=True)
class Usage:
    """Parsed OAuth Usage API response.

    Only the fields ghost cares about are surfaced; unknown fields in the
    server response are silently ignored (schema drift tolerated, per
    spec). Non-null windows mean the server reported usage; ``None``
    means the field was missing or the server returned ``null`` for it.
    """

    five_hour: UsageWindow | None = None
    seven_day: UsageWindow | None = None
    seven_day_opus: UsageWindow | None = None
    seven_day_sonnet: UsageWindow | None = None
    extra_usage: dict[str, Any] | None = None


@dataclass(frozen=True)
class UsageError:
    """Error result. ``kind`` is one of:

    * ``"unavailable_network"`` — connection refused / DNS error / etc.
    * ``"unavailable_5xx"`` — server returned 5xx.
    * ``"rate_limited"`` — server returned 429.
    * ``"stale_credentials"`` — 401 with the beta header present (caller
      should advise running ``claude --resume`` to refresh tokens; ghost
      itself does not refresh).
    * ``"api_unsupported"`` — 410/404, or 401 with the auth-not-supported
      message. Operator should override env vars or upgrade ghost.
    * ``"missing_credentials"`` — credentials file missing or unparseable.
    * ``"unknown"`` — anything else.
    """

    kind: str
    message: str


UsageResult = Usage | UsageError


# ─────────────────────────────────────────────────────────────────────
# Defaults (env-overridable)
# ─────────────────────────────────────────────────────────────────────


DEFAULT_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
DEFAULT_BETA_HEADER = "oauth-2025-04-20"
CACHE_TTL_SECONDS = 60.0
HTTP_TIMEOUT_SECONDS = 8.0


# Type alias for the GET function so tests can inject a fake.
HttpGetFn = Callable[[str, dict[str, str], float], "HttpResponse"]


@dataclass(frozen=True)
class HttpResponse:
    """Minimal HTTP response shape used by this module."""

    status: int
    body: bytes


def _default_http_get(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
    """Default GET implementation backed by stdlib :mod:`urllib`.

    Only HTTP GET is performed — there is no POST path in this module
    (per the trust-surface invariant). The audit-friendliness of this
    function relies on the explicit ``method="GET"`` argument to
    :class:`urllib.request.Request`.
    """
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResponse(status=resp.status, body=resp.read())
    except urllib.error.HTTPError as e:
        # Non-2xx response — return as a normal HttpResponse so caller can branch.
        try:
            body = e.read() if e.fp else b""
        except Exception:
            body = b""
        return HttpResponse(status=e.code, body=body)


# ─────────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────────


def _hash_token(token: str) -> str:
    """Stable short hash used as a cache key — never logged."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _extract_access_token(data: Any) -> str | None:
    """Pull only ``claudeAiOauth.accessToken`` out of a credentials payload.

    Preserves the audit invariant — refreshToken is never returned.
    """
    if not isinstance(data, dict):
        return None
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    tok = oauth.get("accessToken")
    if isinstance(tok, str) and tok:
        return tok
    return None


def _read_keychain_service(service: str) -> str | None:
    """Run ``security find-generic-password -s <service> -w`` and parse.

    Returns the accessToken or None. macOS-only; caller should gate.
    """
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    raw = r.stdout.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return _extract_access_token(data)


class UsageClient:
    """Active OAuth Usage queries with a 60-second per-token cache."""

    def __init__(
        self,
        layout: AccountLayout | None = None,
        *,
        vault: Any = None,
        usage_url: str | None = None,
        beta_header: str | None = None,
        http_get: HttpGetFn | None = None,
        cache_ttl: float = CACHE_TTL_SECONDS,
        timeout: float = HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self._layout = layout or AccountLayout()
        # Optional account vault — when provided, lets the keychain reader
        # pick the right service name for default-routed accounts (which
        # use ``Claude Code-credentials`` without the path-hash suffix).
        # Per ``add-default-account-native-and-refresh``.
        self._vault = vault
        self._usage_url = (
            usage_url
            or os.environ.get("GITS_OAUTH_USAGE_URL")
            or DEFAULT_USAGE_URL
        )
        self._beta_header = (
            beta_header
            or os.environ.get("GITS_OAUTH_BETA_HEADER")
            or DEFAULT_BETA_HEADER
        )
        self._http_get = http_get or _default_http_get
        self._cache_ttl = cache_ttl
        self._timeout = timeout
        # cache key: (account_name, token_hash) → (timestamp, UsageResult)
        self._cache: dict[tuple[str, str], tuple[float, UsageResult]] = {}

    # -- public API ---------------------------------------------------

    def query(self, account_name: str) -> UsageResult:
        """Query usage for an account. Returns :class:`Usage` or :class:`UsageError`."""
        access_token = self._read_access_token(account_name)
        if access_token is None:
            return UsageError(
                kind="missing_credentials",
                message=(
                    f"credentials file for account '{account_name}' is missing "
                    "or contains no accessToken"
                ),
            )

        token_hash = _hash_token(access_token)
        cache_key = (account_name, token_hash)
        now = time.time()
        hit = self._cache.get(cache_key)
        if hit is not None and (now - hit[0]) < self._cache_ttl:
            return hit[1]

        result = self._fetch(access_token)
        self._cache[cache_key] = (now, result)
        return result

    def invalidate_cache(self, account_name: str | None = None) -> None:
        """Drop cached entries — useful after a known token rotation."""
        if account_name is None:
            self._cache.clear()
            return
        keys = [k for k in self._cache if k[0] == account_name]
        for k in keys:
            self._cache.pop(k, None)

    # -- internals ----------------------------------------------------

    def _read_access_token(self, account_name: str) -> str | None:
        """Read ``claudeAiOauth.accessToken`` for an account.

        Tries the macOS keychain entry first (where claude writes
        refreshed tokens — the live source), then falls back to the
        on-disk ``.credentials.json`` file (which is often stale because
        claude doesn't always write refreshed tokens back to disk on
        macOS). On non-macOS the keychain step is a no-op.

        AUDIT INVARIANT: this method **must not** read the
        ``refreshToken`` field. Both readers below explicitly pull only
        ``oauth.get("accessToken")``.
        """
        return (
            self._read_keychain_token(account_name)
            or self._read_file_token(account_name)
        )

    def _read_file_token(self, account_name: str) -> str | None:
        """Read access token from the account's on-disk credentials file."""
        path = self._layout.credentials_file(account_name)
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return _extract_access_token(data)

    def _read_keychain_token(self, account_name: str) -> str | None:
        """Read access token from the per-CONFIG_DIR keychain entry on macOS.

        Claude derives the keychain service name from
        ``sha256(CLAUDE_CONFIG_DIR)[:8]`` for non-default invocations, and
        uses ``Claude Code-credentials`` (no suffix) for native
        ``~/.claude/`` invocations. For default-routed accounts (per
        ``add-default-account-native-and-refresh``), we prefer the
        no-suffix service; otherwise we prefer the suffix-derived service.
        Both are tried in order so we surface a live token wherever it lives.
        """
        if sys.platform != "darwin":
            return None
        for service in self._keychain_service_candidates(account_name):
            tok = _read_keychain_service(service)
            if tok:
                return tok
        return None

    def _keychain_service_candidates(self, account_name: str) -> list[str]:
        """Return keychain service names to try, in priority order."""
        is_default = False
        if self._vault is not None:
            try:
                is_default = (self._vault.load().default == account_name)
            except Exception:
                pass
        config_dir = str(self._layout.account_dir(account_name))
        suffix = hashlib.sha256(config_dir.encode()).hexdigest()[:8]
        suffix_svc = f"Claude Code-credentials-{suffix}"
        default_svc = "Claude Code-credentials"
        # Default-routed accounts read from ~/.claude/ → no-suffix service.
        # Isolated accounts read from ~/.claude-<name>/ → suffix service.
        return [default_svc, suffix_svc] if is_default else [suffix_svc, default_svc]

    def _fetch(self, access_token: str) -> UsageResult:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "anthropic-beta": self._beta_header,
        }
        try:
            resp = self._http_get(self._usage_url, headers, self._timeout)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return UsageError(
                kind="unavailable_network",
                message=f"network error: {e}",
            )
        return self._classify(resp)

    @staticmethod
    def _classify(resp: HttpResponse) -> UsageResult:
        if 500 <= resp.status < 600:
            return UsageError(kind="unavailable_5xx", message=f"HTTP {resp.status}")
        if resp.status == 429:
            return UsageError(kind="rate_limited", message="HTTP 429 (Anthropic rate limit)")
        if resp.status in (404, 410):
            return UsageError(
                kind="api_unsupported",
                message=f"HTTP {resp.status} — Usage endpoint may have moved or been removed",
            )
        if resp.status == 401:
            import contextlib
            text = ""
            with contextlib.suppress(Exception):
                text = resp.body.decode("utf-8", errors="replace")
            if "OAuth authentication is currently not supported" in text:
                return UsageError(
                    kind="api_unsupported",
                    message=(
                        "401 with 'OAuth authentication is currently not supported' — "
                        "the beta header may need to be updated; set "
                        "GITS_OAUTH_BETA_HEADER or upgrade ghost"
                    ),
                )
            return UsageError(
                kind="stale_credentials",
                message=(
                    "401 from Usage endpoint — access token is stale; "
                    "run claude --resume on this account to let claude CLI refresh"
                ),
            )
        if 200 <= resp.status < 300:
            try:
                payload = json.loads(resp.body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as e:
                return UsageError(
                    kind="unknown",
                    message=f"could not decode response body: {e}",
                )
            return _parse_usage(payload)
        return UsageError(kind="unknown", message=f"HTTP {resp.status}")


def _parse_usage(payload: Any) -> Usage:
    """Parse a successful Usage response into the :class:`Usage` shape.

    Tolerates schema drift: unknown top-level keys are ignored; missing
    fields default to ``None``; non-dict windows degrade to ``None``.
    """
    if not isinstance(payload, dict):
        return Usage()

    def _window(value: Any) -> UsageWindow | None:
        if not isinstance(value, dict):
            return None
        util = value.get("utilization")
        resets = value.get("resets_at")
        if util is None and resets is None:
            return None
        return UsageWindow(
            utilization=float(util) if isinstance(util, (int, float)) else None,
            resets_at=resets if isinstance(resets, str) else None,
        )

    extra = payload.get("extra_usage")
    if not isinstance(extra, dict):
        extra = None

    return Usage(
        five_hour=_window(payload.get("five_hour")),
        seven_day=_window(payload.get("seven_day")),
        seven_day_opus=_window(payload.get("seven_day_opus")),
        seven_day_sonnet=_window(payload.get("seven_day_sonnet")),
        extra_usage=extra,
    )
