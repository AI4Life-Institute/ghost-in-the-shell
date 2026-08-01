"""Tests for the core-OS ticket origination guard (Ghost task corehk).

Covers the spec's success story:
  1. unmandated line opening a builder-os ticket        -> refused, actionably
  2. same line, request carries the consent reference   -> allowed
  3. the line holding the standing mandate              -> never blocked
  4. everything else (other repos, non-create verbs)    -> never blocked
plus the mandatory fail-closed behaviour: the guard refuses on its own
errors instead of allowing through.
"""

import json
import subprocess
import sys

import pytest

from gits.hooks import core_os_ticket as g


CORE = "AI4Life-Institute/builder-os"
OTHER = "AI4Life-Institute/ghost-in-the-shell"
CONSENT = (
    "principal_ref=liang "
    "utterance_ref=https://discord.com/channels/1258194549998878731/153301/1533010149977886760"
)

# Env with no BUTLER_USER, so identity comes from the resolver stub.
UNMANDATED = {}
MANDATED = {"BUTLER_USER": "liang-builder"}


def _line(name):
    """A stubbed identity resolver; keeps the tests off git/the filesystem."""
    return lambda cwd, env=None: name


def _eval(command, *, line="harry-er-ai-analyst", env=None, cwd=None, config_path=None):
    """Evaluate one command.

    ``config_path=None`` by default: tests must never read this machine's real
    ``~/.gits/config.env``, or the live grant would leak in and mask exactly
    the regression these tests exist to catch.
    """
    return g.evaluate(
        "Bash",
        {"command": command},
        cwd,
        env=env or {},
        config_path=config_path,
        line_resolver=_line(line),
    )


# --- Case 1: unmandated origination is refused ------------------------------

def test_unmandated_gh_issue_create_is_refused():
    allow, msg = _eval(f'gh issue create --repo {CORE} --title "fix scheduler"')
    assert allow is False
    # The refusal must state all three things the spec requires.
    assert "core OS" in msg
    assert "consent" in msg.lower()
    assert "discord.com/channels" in msg
    assert "principal_ref" in msg and "utterance_ref" in msg


def test_refusal_declares_its_own_coverage_ceiling():
    """The claim must never be broader than the mechanism."""
    _, msg = _eval(f"gh issue create --repo {CORE} --title x")
    assert "NOT a security boundary" in msg
    assert "web UI" in msg
    assert "forgeable" in msg


@pytest.mark.parametrize(
    "command",
    [
        f'gh issue create --repo {CORE} --title "x"',
        f"gh issue create -R {CORE} --title x",
        f"gh api repos/{CORE}/issues -X POST -f title=x",
        f"gh api repos/{CORE}/issues -f title=x",
        f"curl -X POST https://api.github.com/repos/{CORE}/issues -d '{{}}'",
        f'cd /tmp && gh issue create --repo {CORE} --title "x" && echo done',
    ],
)
def test_origination_paths_all_refused(command):
    allow, _ = _eval(command)
    assert allow is False, command


def test_origination_from_inside_a_builder_os_checkout_is_refused():
    """`gh issue create` with no --repo infers the repo from the checkout."""
    allow, _ = _eval("gh issue create --title x", cwd="/Users/sharon/src/builder-os")
    assert allow is False


# --- Case 2: consent reference lets it through ------------------------------

def test_request_carrying_consent_is_allowed():
    allow, _ = _eval(
        f'gh issue create --repo {CORE} --title "fix scheduler" --body "{CONSENT}"'
    )
    assert allow is True


def test_partial_consent_is_not_consent():
    """principal_ref alone, or a non-Discord utterance_ref, does not pass."""
    allow, msg = _eval(f'gh issue create --repo {CORE} --body "principal_ref=liang"')
    assert allow is False
    assert "utterance_ref" in msg
    allow, _ = _eval(
        f'gh issue create --repo {CORE} --body "principal_ref=liang '
        f'utterance_ref=https://example.com/nope"'
    )
    assert allow is False


def test_compact_relay_reference_is_not_consent():
    """A forwarded reference must never satisfy the consent check (task
    [[gldref]]).

    ghost appends a compact reference to *every* message it relays into a
    session (``gits.core.utterance_ref``). Pasting a permalink is an act of
    consent -- someone went and got that link; forwarding is not. Accepting
    the compact form here would let a forwarded message file a core-OS ticket
    with no human having agreed to anything, so this guard takes full
    permalinks only. That is a consent boundary, not a format detail.
    """
    from gits.adapters.base import IncomingMessage
    from gits.core.utterance_ref import format_ref, parse_ref, permalink

    # Built by the real producer, not hand-written, so this stays true if the
    # relay format changes.
    relayed = format_ref(
        IncomingMessage(
            platform="discord",
            guild_id="1258194549998878731",
            channel_id="153301",
            user_id="liang",
            text="可以合",
            message_id="1533010149977886760",
        )
    )
    compact = parse_ref(relayed)
    assert compact.guild_id  # the ref does carry a guild now...

    for body in (
        f'principal_ref=liang utterance_ref={relayed}',
        f"principal_ref=liang utterance_ref=discord:"
        f"{compact.guild_id}/{compact.channel_id}/{compact.message_id}",
    ):
        allow, msg = _eval(f'gh issue create --repo {CORE} --body "{body}"')
        assert allow is False, body
        assert "utterance_ref" in msg
        assert g.consent_refs(body)[1] is None, body

    # ...and the permalink a human would paste from it still passes, so the
    # refusal above is about provenance, not about the guild being unusable.
    allow, _ = _eval(
        f'gh issue create --repo {CORE} --body "principal_ref=liang '
        f'utterance_ref={permalink(compact)}"'
    )
    assert allow is True


# --- Case 3: the mandated line is never blocked -----------------------------

def test_mandated_line_is_never_blocked():
    allow, _ = _eval(
        f"gh issue create --repo {CORE} --title x",
        line="liang-builder",
        env={"GHOST_CORE_OS_MANDATE": "liang-builder"},
    )
    assert allow is True


# --- The mandate default must GRANT NOTHING ---------------------------------
# Every other branch of this guard is fail-closed; a non-empty default would be
# the single granting default, and would bake a deployment fact into source.

def test_unconfigured_mandate_grants_nobody():
    """No configuration ⇒ nobody is mandated ⇒ even liang-builder is refused."""
    assert g.mandated_lines(env={}, config_path=None) == ()
    allow, msg = _eval(f"gh issue create --repo {CORE} --title x", line="liang-builder")
    assert allow is False
    assert "none configured" in msg


def test_configured_line_is_allowed_via_config_env(tmp_path):
    """The grant lives in ghost's own config.env, and the guard reads it."""
    cfg = tmp_path / "config.env"
    cfg.write_text("GITS_DEFAULT_PATH=/tmp\nGHOST_CORE_OS_MANDATE=liang-builder\n")
    assert g.mandated_lines(env={}, config_path=str(cfg)) == ("liang-builder",)
    allow, _ = _eval(
        f"gh issue create --repo {CORE} --title x",
        line="liang-builder",
        config_path=str(cfg),
    )
    assert allow is True
    # ...and only that line.
    allow, _ = _eval(
        f"gh issue create --repo {CORE} --title x",
        line="harry-er-ai-analyst",
        config_path=str(cfg),
    )
    assert allow is False


def test_repos_default_stays_non_empty():
    """The opposite direction: an empty repo set would silently disable the hook."""
    assert g.core_os_repos(env={}, config_path=None) == (CORE.lower(),)


def test_unreadable_config_env_means_unconfigured_means_deny(tmp_path):
    allow, _ = _eval(
        f"gh issue create --repo {CORE} --title x",
        line="liang-builder",
        config_path=str(tmp_path / "does-not-exist.env"),
    )
    assert allow is False


def test_config_env_key_is_declared_on_settings():
    """A key that config.env forbids would break every Settings() construction,
    not just this hook — so the field must exist on the model."""
    from gits.config import Settings

    cfg = Settings.model_fields
    assert "ghost_core_os_mandate" in cfg
    assert "ghost_core_os_repos" in cfg


def test_mandate_comes_from_config_not_a_hardcoded_session_id():
    """The contract says 'any Builder, not only me' — so it must be configurable."""
    env = {"GHOST_CORE_OS_MANDATE": "kathy,weiliu"}
    allow, _ = _eval(f"gh issue create --repo {CORE} --title x", line="kathy", env=env)
    assert allow is True
    allow, _ = _eval(
        f"gh issue create --repo {CORE} --title x", line="liang-builder", env=env
    )
    assert allow is False


def test_unresolved_identity_is_treated_as_unmandated():
    allow, msg = _eval(f"gh issue create --repo {CORE} --title x", line=None)
    assert allow is False
    assert "unresolved" in msg


# --- Case 4: zero false positives -------------------------------------------

@pytest.mark.parametrize(
    "command",
    [
        # other repos
        f"gh issue create --repo {OTHER} --title x",
        # explicit selector wins over an incidental mention of builder-os
        f'gh issue create --repo {OTHER} --title "sync with builder-os"',
        # non-origination verbs on builder-os
        f"gh issue list --repo {CORE}",
        f"gh issue view 163 --repo {CORE}",
        f"gh issue comment 163 --repo {CORE} --body hi",
        f"gh api repos/{CORE}/issues",
        f"gh pr create --repo {CORE} --title x",
        "git -C /Users/sharon/src/builder-os log --oneline",
        "pytest /Users/sharon/src/builder-os/tests",
        "cat /Users/sharon/src/builder-os/README.md",
    ],
)
def test_no_false_positives(command):
    allow, _ = _eval(command)
    assert allow is True, command


def test_non_bash_tools_are_ignored():
    allow, _ = g.evaluate(
        "Edit", {"file_path": "/Users/sharon/src/builder-os/x.py"}, None, env={}
    )
    assert allow is True


# --- Mandatory: fail closed, with a bounded blast radius --------------------

def test_guard_refuses_when_evaluation_raises():
    def boom(cwd, env=None):
        raise RuntimeError("git exploded")

    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f"gh issue create --repo {CORE} --title x"},
    }
    # Force the failure through the real entry point by breaking the resolver.
    original = g.resolve_line
    g.resolve_line = boom
    try:
        allow, msg = g.check(payload, raw=json.dumps(payload))
    finally:
        g.resolve_line = original
    assert allow is False
    assert "could not evaluate" in msg
    assert "git exploded" in msg


def test_unparseable_payload_mentioning_core_os_is_refused():
    allow, msg = g.check(None, raw=f'{{"command": "gh issue create --repo {CORE}"')
    assert allow is False
    assert "unparseable" in msg


def test_unparseable_payload_not_about_core_os_is_allowed():
    """Fail-closed must not brick every tool call in every session."""
    allow, _ = g.check(None, raw='{"tool_name": "Bash", "tool_input": {"command": "ls"')
    assert allow is True


# --- End-to-end through the installed `gits guard` hook ---------------------

def _run_guard(payload: dict, env: dict, home) -> subprocess.CompletedProcess:
    """Run the real `gits guard` hook with HOME pointed at a temp dir.

    Isolating HOME is what makes these hermetic: the guard resolves its config
    at ``~/.gits/config.env``, so without this the machine's live grant would
    decide the outcome instead of the test.
    """
    import os

    return subprocess.run(
        [sys.executable, "-m", "gits", "guard"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(home), **env},
    )


def _create_payload(cwd):
    return {
        "tool_name": "Bash",
        "tool_input": {"command": f"gh issue create --repo {CORE} --title x"},
        "cwd": str(cwd),
    }


def test_main_refuses_unmandated_core_os_ticket(tmp_path):
    r = _run_guard(
        _create_payload(tmp_path), {"BUTLER_USER": "harry-er-ai-analyst"}, tmp_path
    )
    assert r.returncode == 2
    assert "core OS" in r.stderr
    assert "principal_ref" in r.stderr


def test_main_refuses_when_mandate_is_unconfigured(tmp_path):
    """End-to-end: no config.env at all ⇒ nobody mandated ⇒ refused."""
    r = _run_guard(
        _create_payload(tmp_path), {"BUTLER_USER": "liang-builder"}, tmp_path
    )
    assert r.returncode == 2
    assert "none configured" in r.stderr


def test_main_allows_mandated_line_configured_in_config_env(tmp_path):
    """End-to-end: the grant in ~/.gits/config.env lets that line through."""
    gits_dir = tmp_path / ".gits"
    gits_dir.mkdir()
    (gits_dir / "config.env").write_text("GHOST_CORE_OS_MANDATE=liang-builder\n")
    r = _run_guard(
        _create_payload(tmp_path), {"BUTLER_USER": "liang-builder"}, tmp_path
    )
    assert r.returncode == 0, r.stderr
    # A different line is still refused with the same config in place.
    r = _run_guard(
        _create_payload(tmp_path), {"BUTLER_USER": "harry-er-ai-analyst"}, tmp_path
    )
    assert r.returncode == 2


def test_main_allows_unrelated_command(tmp_path):
    r = _run_guard(
        {"tool_name": "Bash", "tool_input": {"command": "ls -la"}, "cwd": str(tmp_path)},
        {"BUTLER_USER": "harry-er-ai-analyst"},
        tmp_path,
    )
    assert r.returncode == 0, r.stderr
