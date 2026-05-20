"""Static assertion: ghost MUST NOT invoke `claude` subprocess for quota checks.

Per the spec ``Detection Without CLI Quota API``, the only place ghost is
permitted to spawn the ``claude`` binary as a subprocess is the
``gits subscription add`` flow (which runs ``claude auth login`` interactively).

This is a guard against future regressions where someone might wire in
``claude auth status`` or ``claude -p ping`` as a quota probe.
"""

import re
from pathlib import Path

import pytest


SRC = Path(__file__).parent.parent / "src" / "gits"
ALLOWED_FILES = {
    # subscription.py — credential management spawns claude (DEPRECATED;
    # see openspec change ``add-multi-account-hotswap``):
    #   • add_subscription / _run_login_subprocess: OAuth login flow.
    #   • fetch_claude_identity: one-shot `claude auth status --json` read at
    #     vault-add time so the captured account's email/orgId can be shown
    #     to the user. NOT a quota probe.
    "core/subscription.py": [
        "add_subscription",
        "_run_login_subprocess",
        "fetch_claude_identity",
    ],
    # cli_account.py — `gits account add` runs `claude auth login` with
    # CLAUDE_CONFIG_DIR set to the new per-account directory. This is the
    # ONLY place the new account-management code spawns claude. Quota /
    # load detection in the new design reads local JSONL transcripts (see
    # gits.core.account_load) — never a `claude` subprocess.
    "cli_account.py": [
        "cmd_add",
    ],
}

# Patterns that indicate we're spawning claude
SPAWN_PATTERNS = [
    re.compile(r"create_subprocess_exec\([^)]*['\"]claude['\"]"),
    re.compile(r"subprocess\.(?:Popen|run|call|check_output|check_call)\([^)]*['\"]claude['\"]"),
    re.compile(r'\["claude",'),
]


def _python_files(root: Path):
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def test_no_unsanctioned_claude_invocation():
    offenders: list[str] = []
    for path in _python_files(SRC):
        rel = path.relative_to(SRC).as_posix()
        try:
            text = path.read_text()
        except OSError:
            continue
        for pattern in SPAWN_PATTERNS:
            for match in pattern.finditer(text):
                # Resolve the enclosing function (best-effort)
                pre = text[: match.start()]
                fn_match = re.findall(r"^\s*(?:async\s+)?def (\w+)", pre, re.M)
                fn_name = fn_match[-1] if fn_match else "<module>"
                if rel in ALLOWED_FILES and fn_name in ALLOWED_FILES[rel]:
                    continue  # whitelisted
                offenders.append(f"{rel}::{fn_name} — match: {match.group(0)[:60]!r}")
    assert not offenders, (
        "Ghost MUST NOT invoke `claude` outside the subscription add flow. "
        "Offenders found:\n  " + "\n  ".join(offenders)
    )
