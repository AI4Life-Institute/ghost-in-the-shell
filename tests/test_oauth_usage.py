"""Tests for the OAuth Usage API client (Phase 0.8)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gits.core.account import AccountLayout
from gits.core.oauth_usage import (
    DEFAULT_BETA_HEADER,
    DEFAULT_USAGE_URL,
    HttpResponse,
    Usage,
    UsageClient,
    UsageError,
    UsageWindow,
    _parse_usage,
)


# Real-world response sample (2026-04-27 verification)
SAMPLE_RESPONSE = {
    "five_hour": {"utilization": 26.0, "resets_at": "2026-04-28T07:30:00.202122+00:00"},
    "seven_day": {"utilization": 4.0, "resets_at": "2026-05-05T02:00:00.202137+00:00"},
    "seven_day_oauth_apps": None,
    "seven_day_opus": None,
    "seven_day_sonnet": {"utilization": 2.0, "resets_at": "2026-05-05T02:00:01.202143+00:00"},
    "seven_day_cowork": None,
    "seven_day_omelette": {"utilization": 0.0, "resets_at": None},
    "iguana_necktie": None,
    "omelette_promotional": None,
    "extra_usage": {
        "is_enabled": False,
        "monthly_limit": None,
        "used_credits": None,
        "utilization": None,
        "currency": None,
    },
}


def _write_credentials(layout: AccountLayout, account: str, access_token: str = "AT") -> None:
    creds = layout.credentials_file(account)
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": access_token, "refreshToken": "RT"}}))


def _client(tmp_path, http_get=None) -> UsageClient:
    layout = AccountLayout(home=tmp_path)
    return UsageClient(layout=layout, http_get=http_get)


# ----------------------------------------------------------------------
# _parse_usage
# ----------------------------------------------------------------------


def test_parse_usage_real_sample() -> None:
    out = _parse_usage(SAMPLE_RESPONSE)
    assert out.five_hour == UsageWindow(utilization=26.0, resets_at="2026-04-28T07:30:00.202122+00:00")
    assert out.seven_day == UsageWindow(utilization=4.0, resets_at="2026-05-05T02:00:00.202137+00:00")
    assert out.seven_day_opus is None  # null in source
    assert out.seven_day_sonnet == UsageWindow(utilization=2.0, resets_at="2026-05-05T02:00:01.202143+00:00")
    assert out.extra_usage == SAMPLE_RESPONSE["extra_usage"]


def test_parse_usage_unknown_fields_ignored() -> None:
    """Schema drift tolerated."""
    payload = {**SAMPLE_RESPONSE, "future_field_v2": {"something": 1}}
    out = _parse_usage(payload)
    assert out.five_hour is not None  # known field still parsed


def test_parse_usage_missing_fields() -> None:
    out = _parse_usage({})
    assert out.five_hour is None
    assert out.seven_day is None


def test_parse_usage_non_dict_returns_empty() -> None:
    assert _parse_usage(["not a dict"]) == Usage()


def test_parse_usage_window_partial_null() -> None:
    """Window with only utilization, no resets_at."""
    out = _parse_usage({"five_hour": {"utilization": 50}})
    assert out.five_hour == UsageWindow(utilization=50.0, resets_at=None)


# ----------------------------------------------------------------------
# UsageClient.query — happy path
# ----------------------------------------------------------------------


def test_query_success(tmp_path) -> None:
    captured_headers = {}

    def fake_get(url, headers, timeout):
        captured_headers.update(headers)
        assert url == DEFAULT_USAGE_URL
        return HttpResponse(status=200, body=json.dumps(SAMPLE_RESPONSE).encode())

    layout = AccountLayout(home=tmp_path)
    _write_credentials(layout, "alice", access_token="AT-1")
    client = UsageClient(layout=layout, http_get=fake_get)

    result = client.query("alice")
    assert isinstance(result, Usage)
    assert result.five_hour.utilization == 26.0
    # Required headers
    assert captured_headers["Authorization"] == "Bearer AT-1"
    assert captured_headers["anthropic-beta"] == DEFAULT_BETA_HEADER


# ----------------------------------------------------------------------
# Cache
# ----------------------------------------------------------------------


def test_cache_hit_skips_http(tmp_path) -> None:
    calls = []

    def fake_get(url, headers, timeout):
        calls.append(1)
        return HttpResponse(status=200, body=json.dumps(SAMPLE_RESPONSE).encode())

    layout = AccountLayout(home=tmp_path)
    _write_credentials(layout, "alice", access_token="AT-1")
    client = UsageClient(layout=layout, http_get=fake_get, cache_ttl=60.0)
    client.query("alice")
    client.query("alice")
    assert len(calls) == 1


def test_cache_invalidates_on_token_change(tmp_path) -> None:
    calls = []

    def fake_get(url, headers, timeout):
        calls.append(1)
        return HttpResponse(status=200, body=json.dumps(SAMPLE_RESPONSE).encode())

    layout = AccountLayout(home=tmp_path)
    _write_credentials(layout, "alice", access_token="AT-1")
    client = UsageClient(layout=layout, http_get=fake_get)
    client.query("alice")
    # Token rotation: rewrite credentials file with a new token
    _write_credentials(layout, "alice", access_token="AT-2")
    client.query("alice")
    assert len(calls) == 2  # both went over the wire


def test_cache_explicit_invalidate(tmp_path) -> None:
    calls = []

    def fake_get(url, headers, timeout):
        calls.append(1)
        return HttpResponse(status=200, body=json.dumps(SAMPLE_RESPONSE).encode())

    layout = AccountLayout(home=tmp_path)
    _write_credentials(layout, "alice", access_token="AT-1")
    client = UsageClient(layout=layout, http_get=fake_get)
    client.query("alice")
    client.invalidate_cache("alice")
    client.query("alice")
    assert len(calls) == 2


# ----------------------------------------------------------------------
# Error classification
# ----------------------------------------------------------------------


def test_query_500_returns_unavailable_5xx(tmp_path) -> None:
    def fake_get(url, headers, timeout):
        return HttpResponse(status=503, body=b"")
    layout = AccountLayout(home=tmp_path)
    _write_credentials(layout, "alice")
    client = UsageClient(layout=layout, http_get=fake_get)
    result = client.query("alice")
    assert isinstance(result, UsageError)
    assert result.kind == "unavailable_5xx"


def test_query_429_returns_rate_limited(tmp_path) -> None:
    def fake_get(url, headers, timeout):
        return HttpResponse(status=429, body=b"")
    layout = AccountLayout(home=tmp_path)
    _write_credentials(layout, "alice")
    client = UsageClient(layout=layout, http_get=fake_get)
    result = client.query("alice")
    assert isinstance(result, UsageError)
    assert result.kind == "rate_limited"


def test_query_401_stale_credentials(tmp_path) -> None:
    """401 with no auth-not-supported message → stale credentials (refresh externally)."""
    def fake_get(url, headers, timeout):
        return HttpResponse(status=401, body=b'{"error":"invalid_token"}')
    layout = AccountLayout(home=tmp_path)
    _write_credentials(layout, "alice")
    client = UsageClient(layout=layout, http_get=fake_get)
    result = client.query("alice")
    assert isinstance(result, UsageError)
    assert result.kind == "stale_credentials"
    assert "claude --resume" in result.message


def test_query_401_auth_not_supported_is_api_unsupported(tmp_path) -> None:
    """The flag-string returned when beta header is missing maps to api_unsupported."""
    body = json.dumps({
        "type": "error",
        "error": {"type": "authentication_error", "message": "OAuth authentication is currently not supported."},
    }).encode()
    def fake_get(url, headers, timeout):
        return HttpResponse(status=401, body=body)
    layout = AccountLayout(home=tmp_path)
    _write_credentials(layout, "alice")
    client = UsageClient(layout=layout, http_get=fake_get)
    result = client.query("alice")
    assert isinstance(result, UsageError)
    assert result.kind == "api_unsupported"


@pytest.mark.parametrize("status", [404, 410])
def test_query_404_410_api_unsupported(tmp_path, status) -> None:
    def fake_get(url, headers, timeout):
        return HttpResponse(status=status, body=b"")
    layout = AccountLayout(home=tmp_path)
    _write_credentials(layout, "alice")
    client = UsageClient(layout=layout, http_get=fake_get)
    result = client.query("alice")
    assert isinstance(result, UsageError)
    assert result.kind == "api_unsupported"


def test_query_network_error(tmp_path) -> None:
    def fake_get(url, headers, timeout):
        import urllib.error
        raise urllib.error.URLError("connection refused")
    layout = AccountLayout(home=tmp_path)
    _write_credentials(layout, "alice")
    client = UsageClient(layout=layout, http_get=fake_get)
    result = client.query("alice")
    assert isinstance(result, UsageError)
    assert result.kind == "unavailable_network"


def test_query_missing_credentials(tmp_path) -> None:
    layout = AccountLayout(home=tmp_path)
    # No credentials file written
    client = UsageClient(layout=layout, http_get=lambda *a, **kw: pytest.fail("should not call HTTP"))
    result = client.query("ghost")
    assert isinstance(result, UsageError)
    assert result.kind == "missing_credentials"


# ----------------------------------------------------------------------
# Audit invariants (Stale Credentials Reported, Not Refreshed)
# ----------------------------------------------------------------------


def _module_code_tokens() -> set[str]:
    """Parse oauth_usage.py and return all string-literal values + attribute names
    in CODE positions (i.e. excluding the module docstring and excluding nested
    docstrings of classes/functions). This is the audit substrate used by the
    no-POST and no-refresh-token invariant tests."""
    import ast
    src = (Path(__file__).parent.parent / "src" / "gits" / "core" / "oauth_usage.py").read_text()
    tree = ast.parse(src)

    # Identify docstring nodes so we can skip them. A docstring is the first
    # statement of a module/class/function body when that statement is an
    # Expr wrapping a Constant string.
    docstring_nodes: set[int] = set()
    for parent in ast.walk(tree):
        body = getattr(parent, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstring_nodes.add(id(first.value))

    tokens: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_nodes:
                continue
            tokens.add(node.value)
        elif isinstance(node, ast.Attribute):
            tokens.add(node.attr)
        elif isinstance(node, ast.Name):
            tokens.add(node.id)
    return tokens


def test_module_no_post_call_sites() -> None:
    """``oauth_usage`` code must not contain any HTTP POST patterns."""
    tokens = _module_code_tokens()
    assert "POST" not in tokens, "found 'POST' as a literal/attribute"
    assert "post" not in tokens, "found 'post' as a literal/attribute"


def test_module_does_not_read_refresh_token() -> None:
    """``oauth_usage`` code must not reference the refreshToken field."""
    tokens = _module_code_tokens()
    assert "refreshToken" not in tokens, "found refreshToken in code"
    assert "refresh_token" not in tokens, "found refresh_token in code"


def test_module_does_not_open_credentials_for_write(tmp_path) -> None:
    """The module must not write to .credentials.json. We assert this by behavior:
    even when the URL returns 401, no file mutation occurs."""
    layout = AccountLayout(home=tmp_path)
    _write_credentials(layout, "alice", access_token="AT-1")
    creds_path = layout.credentials_file("alice")
    original = creds_path.read_text()
    original_mtime = creds_path.stat().st_mtime

    def fake_get(url, headers, timeout):
        return HttpResponse(status=401, body=b'{"error":"invalid_token"}')

    client = UsageClient(layout=layout, http_get=fake_get)
    result = client.query("alice")
    assert isinstance(result, UsageError)
    # File untouched
    assert creds_path.read_text() == original
    # mtime unchanged within filesystem precision
    assert creds_path.stat().st_mtime == original_mtime


# ----------------------------------------------------------------------
# Env var overrides
# ----------------------------------------------------------------------


def test_env_var_overrides(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GITS_OAUTH_USAGE_URL", "https://other.example/api/usage")
    monkeypatch.setenv("GITS_OAUTH_BETA_HEADER", "future-beta-2099")

    captured = {}

    def fake_get(url, headers, timeout):
        captured["url"] = url
        captured["beta"] = headers.get("anthropic-beta")
        return HttpResponse(status=200, body=b"{}")

    layout = AccountLayout(home=tmp_path)
    _write_credentials(layout, "alice")
    client = UsageClient(layout=layout, http_get=fake_get)
    client.query("alice")
    assert captured["url"] == "https://other.example/api/usage"
    assert captured["beta"] == "future-beta-2099"
