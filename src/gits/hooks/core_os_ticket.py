"""PreToolUse guard: refuse core-OS ticket origination without disclosed consent.

Governance background (Ghost task corehk; builder-os contract #163)
-------------------------------------------------------------------
The organizational authority ruled on 2026-08-01:

* only a line holding a **standing improvement mandate** may open tickets
  against **builder-os core OS** on its own;
* any other line must first obtain the Builder's consent, and that request
  must **disclose that the target is core OS code** (any Builder, including
  but not limited to the operator);
* consent obtained without that disclosure does not count.

The contract half of that ruling lives in builder-os #163. **This module is
only the Ghost-side enforcement point**: it intercepts the moment of
origination and refuses when a non-mandated line tries to open a core-OS
ticket without carrying the consent reference.

What this is NOT — read before trusting it
------------------------------------------
**This is an omission guard, not a security boundary.** Its purpose is to stop
"some line casually filed a ticket against core OS and the Builder never knew
it happened". It does **not** stop a determined agent, and it is not designed
to:

* any local agent can edit its own ``settings.json``, uninstall this hook,
  call the GitHub API from a language runtime, or open the issue in a browser;
* the consent reference below is an unauthenticated string in the request —
  it is trivially forgeable. It is a *disclosure receipt*, not a credential.

Coverage ceiling (the refusal message says the same — the claim must never be
broader than the mechanism):

* COVERED: ``Bash`` tool calls that originate a ticket — ``gh issue create``,
  ``gh api ... POST .../issues``, and ``curl`` POSTs to the GitHub issues
  endpoint.
* NOT COVERED: the GitHub web UI, GitHub MCP/tool integrations, direct API
  calls made from inside a program, ``ghost butler dispatch`` of a task page
  that targets builder-os, work done on another machine, and any session that
  has disabled or bypassed this hook.

Fail-closed
-----------
Two prior hooks on this machine failed open and were therefore worthless (an
``if`` matcher that fell through on complex Bash; a guard wired to a broken
venv that ``exit 0``-ed on error). So: **any error while evaluating a payload
that mentions a core-OS repo results in a refusal**, never an allow. The
blast radius of that rule is deliberately bounded — a payload with no core-OS
token in it is not this guard's business and is allowed unexamined.

Design constraints
------------------
* **Stdlib only.** Runs on every Bash call; no ``gits.config`` / ``butler``
  imports (see :mod:`gits.hooks`). The worktree-identity regexes below
  intentionally duplicate ``gits.butler.identity`` rather than import it.
* **No hardcoded session id.** The mandate is configured by *line*, because
  the contract says "any Builder, including but not limited to me".
* **No second credential mechanism.** Consent is expressed with the same
  ``principal_ref`` + ``utterance_ref`` pair the builder-os contract defines.

Configuration — where, and what happens when it is absent
---------------------------------------------------------
Both knobs are read from the process environment first, then from ghost's own
config file ``~/.gits/config.env`` (the same file the rest of ghost reads).
``GHOST_CORE_OS_MANDATE`` is also declared on :class:`gits.config.Settings`,
because ``config.env`` is validated with ``extra='forbid'`` — an undeclared
key there makes *every* ``Settings()`` construction raise, taking the whole
app down, not just this hook.

``GHOST_CORE_OS_MANDATE`` — comma-separated line names holding a standing
core-OS improvement mandate. **Defaults to empty: with nothing configured, no
line is mandated, so every core-OS ticket requires disclosure and consent.**
That direction is deliberate. A non-empty default would be the one *granting*
default in a hook whose every other branch is fail-closed, and it would bake a
deployment fact — which line the organization trusts — into ghost's source,
where it has no business knowing builder-os's org structure.

``GHOST_CORE_OS_REPOS`` — comma-separated ``owner/repo`` treated as core OS.
This one keeps a non-empty default on purpose: empty would mean "no repo is
core OS", turning the hook into a silent no-op, which is fail-open by another
name. The two defaults point in opposite directions because the safe direction
is opposite for each.
"""

from __future__ import annotations

import os
import re

# --- What counts as core OS -------------------------------------------------

# Overridable (comma-separated ``owner/repo``) so this is config, not a
# constant baked into a hook nobody remembers to edit.
_CORE_OS_REPOS_ENV = "GHOST_CORE_OS_REPOS"
_DEFAULT_CORE_OS_REPOS = ("AI4Life-Institute/builder-os",)

# --- Who holds a standing improvement mandate -------------------------------

# A *line* (role), never a session id — the contract is explicit that the
# mandate belongs to a role, not to a person.
#
# Empty by default, and that is the whole point: an unconfigured machine
# grants nobody a standing mandate, so every core-OS ticket needs disclosed
# consent. Grants are deployment config (see module docstring), not source.
_MANDATE_ENV = "GHOST_CORE_OS_MANDATE"
_DEFAULT_MANDATE: tuple[str, ...] = ()

# ghost's own config file — same place as the rest of ghost's settings.
_CONFIG_ENV_PATH = os.path.expanduser("~/.gits/config.env")

# Sentinel so callers can pass ``config_path=None`` to mean "environment
# only" (tests rely on this: reading the real config.env would let this
# machine's actual grant leak in and mask a regression).
_UNSET = object()

# --- Identity: which line is this session? ----------------------------------
# Mirrors gits.butler.identity's two redundant worktree signals.
_WORKTREE_BRANCH_RE = re.compile(r"^([a-z0-9][a-z0-9_-]*)/work$")
_WORKTREE_DIR_RE = re.compile(r"^vault-([a-z0-9][a-z0-9_-]*)$")

# --- Consent: the builder-os contract's own shape ---------------------------
_PRINCIPAL_REF_RE = re.compile(r"\bprincipal_ref\s*[:=]\s*\"?([^\s\"',]+)")
# Only a FULL permalink counts, and that is deliberate — not an oversight to
# be "fixed" by also accepting ghost's compact relay reference
# (``discord:<guild>/<channel>/<message>``, see gits.core.utterance_ref).
#
# Pasting a permalink is an act of consent: someone went and got that link.
# Forwarding is not — ghost appends a compact reference to *every* relayed
# message automatically. Teaching this regex the compact form would let a
# forwarded message file a core-OS ticket by itself, with no human having
# agreed to anything. That is a consent boundary, not a format detail.
_UTTERANCE_REF_RE = re.compile(
    r"\butterance_ref\s*[:=]\s*\"?"
    r"(https://(?:\w+\.)?discord\.com/channels/\d+/\d+/\d+)"
)

# --- Origination detection --------------------------------------------------
_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n")

_GH_ISSUE_CREATE_RE = re.compile(r"^\s*gh\s+issue\s+create\b")
_GH_API_RE = re.compile(r"^\s*gh\s+api\b")
_CURL_RE = re.compile(r"^\s*curl\b")

_POST_FLAG_RE = re.compile(
    r"(?:-X|--request|--method|-f\s+method=)\s*[\"']?POST\b", re.IGNORECASE
)
# gh api defaults to POST when a field flag is present.
_GH_API_FIELD_RE = re.compile(r"\s(?:-f|-F|--field|--raw-field|--input)\b")

_REPO_FLAG_RE = re.compile(r"(?:--repo|-R)[=\s]+[\"']?([\w.\-]+/[\w.\-]+)")
_API_ISSUES_PATH_RE = re.compile(r"repos/([\w.\-]+/[\w.\-]+)/issues\b")


def _read_config_env(path) -> dict:
    """Parse ``KEY=value`` lines out of ghost's config.env. ``{}`` if unusable.

    A deliberately tiny stdlib parser: this module may not import
    ``gits.config`` (pydantic on every Bash call), and it only needs two flat
    string keys. Errors are swallowed here and turn into "not configured",
    which for the mandate means *deny* — the safe direction.
    """
    if path is None:
        return {}
    values: dict = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip("\"'")
    except OSError:
        return {}
    return values


def _setting(key: str, env: dict | None, config_path) -> str:
    """Look up ``key``: process environment first, then ghost's config.env."""
    environ = env if env is not None else os.environ
    raw = environ.get(key)
    if raw:
        return raw
    resolved = _CONFIG_ENV_PATH if config_path is _UNSET else config_path
    return _read_config_env(resolved).get(key, "")


def core_os_repos(env: dict | None = None, config_path=_UNSET) -> tuple[str, ...]:
    """Configured core-OS repos, lowercased ``owner/repo``.

    Falls back to a **non-empty** default: an empty set would mean nothing is
    core OS, i.e. a silently disabled hook.
    """
    raw = _setting(_CORE_OS_REPOS_ENV, env, config_path)
    values = raw.split(",") if raw else _DEFAULT_CORE_OS_REPOS
    return tuple(v.strip().lower() for v in values if v.strip())


def mandated_lines(env: dict | None = None, config_path=_UNSET) -> tuple[str, ...]:
    """Lines holding a standing core-OS improvement mandate.

    Falls back to **empty** — unconfigured means nobody is mandated, so every
    core-OS ticket needs disclosed consent. See the module docstring for why
    this default points the opposite way from :func:`core_os_repos`.
    """
    raw = _setting(_MANDATE_ENV, env, config_path)
    values = raw.split(",") if raw else _DEFAULT_MANDATE
    return tuple(v.strip().lower() for v in values if v.strip())


def resolve_line(cwd: str | None, env: dict | None = None) -> str | None:
    """Best-effort identity of the current line.

    ``BUTLER_USER`` first, then the vault worktree's branch (``<name>/work``)
    or directory (``vault-<name>``). Returns ``None`` when nothing resolves —
    and an unresolved identity is treated as **unmandated**, never as
    mandated. There is deliberately no OS-user fallback: several humans share
    one OS user on this machine.
    """
    environ = env if env is not None else os.environ
    explicit = environ.get("BUTLER_USER")
    if explicit:
        return explicit.strip().lower() or None

    for candidate in _git_signals(cwd):
        m = _WORKTREE_BRANCH_RE.match(candidate) or _WORKTREE_DIR_RE.match(candidate)
        if m:
            return m.group(1).lower()
    return None


def _git_signals(cwd: str | None) -> list[str]:
    """``[branch, toplevel basename]`` for ``cwd``; empty when git is unusable."""
    import subprocess

    if not cwd:
        return []
    signals: list[str] = []
    for args, as_basename in (
        (["rev-parse", "--abbrev-ref", "HEAD"], False),
        (["rev-parse", "--show-toplevel"], True),
    ):
        try:
            out = subprocess.run(
                ["git", "-C", cwd, *args],
                capture_output=True, text=True, timeout=2, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        value = out.stdout.strip()
        if value:
            signals.append(os.path.basename(value) if as_basename else value)
    return signals


def _mentions_core_os(text: str, env: dict | None = None, config_path=_UNSET) -> bool:
    """Cheap pre-filter: does this text name a core-OS repo at all?

    Used both to skip evaluation entirely (the overwhelmingly common case)
    and to bound the blast radius of the fail-closed rule.
    """
    low = (text or "").lower()
    return any(
        repo in low or repo.split("/")[-1] in low
        for repo in core_os_repos(env, config_path)
    )


def _explicit_repo(segment: str) -> str | None:
    """Repo named by an explicit selector (``--repo`` or an API issues path)."""
    m = _REPO_FLAG_RE.search(segment)
    if m:
        return m.group(1).lower()
    m = _API_ISSUES_PATH_RE.search(segment)
    if m:
        return m.group(1).lower()
    return None


def originates_core_os_ticket(
    command: str,
    cwd: str | None = None,
    env: dict | None = None,
    config_path=_UNSET,
) -> bool:
    """True if ``command`` opens a ticket against a configured core-OS repo.

    Reads, comments, and test runs never match: the verb must be a *create*
    (``gh issue create``, or a POST to an ``.../issues`` endpoint). When the
    command names a repo explicitly that name is authoritative — so
    ``gh issue create --repo …/ghost --title "sync with builder-os"`` is a
    ghost ticket, not a core-OS one. Only when no selector is present do we
    fall back to a token scan plus the session's checkout.
    """
    repos = core_os_repos(env, config_path)
    for segment in _SEGMENT_SPLIT_RE.split(command or ""):
        if _GH_ISSUE_CREATE_RE.search(segment):
            is_create = True
        elif _GH_API_RE.search(segment) and _API_ISSUES_PATH_RE.search(segment):
            is_create = bool(
                _POST_FLAG_RE.search(segment) or _GH_API_FIELD_RE.search(segment)
            )
        elif _CURL_RE.search(segment) and _API_ISSUES_PATH_RE.search(segment):
            is_create = bool(_POST_FLAG_RE.search(segment) or "--data" in segment)
        else:
            continue
        if not is_create:
            continue

        explicit = _explicit_repo(segment)
        if explicit is not None:
            if explicit in repos:
                return True
            continue
        # No selector: `gh issue create` infers the repo from the checkout.
        if _mentions_core_os(segment, env, config_path):
            return True
        if cwd and any(repo.split("/")[-1] in cwd.lower() for repo in repos):
            return True
    return False


def consent_refs(command: str) -> tuple[str | None, str | None]:
    """``(principal_ref, utterance_ref)`` carried by the request, if any."""
    p = _PRINCIPAL_REF_RE.search(command or "")
    u = _UTTERANCE_REF_RE.search(command or "")
    return (p.group(1) if p else None, u.group(1) if u else None)


_MESSAGE = """\
BLOCKED by ghost core-OS origination guard: this session is opening a ticket
against core OS code, and the request carries no disclosed consent.

  target       : {repos}
  this line    : {line}
  mandated     : {mandate}
  missing      : {missing}

Why: builder-os is core OS code. Only a line holding a standing improvement
mandate may file against it unilaterally. Every other line must first tell a
Builder — in Discord, in so many words — that **the change targets core OS
code**, and get their consent. Consent given without that disclosure does not
count (builder-os contract #163).

Do this next:
  1. In Discord, tell the Builder what you want to change and state plainly
     that it is builder-os CORE OS code.
  2. When they agree, copy the permalink of their reply.
  3. Re-run the same command with the consent reference in the issue body,
     in the contract's own form:

       principal_ref=<builder-handle>
       utterance_ref=https://discord.com/channels/<guild>/<channel>/<message>

This guard is an ANTI-OMISSION check, NOT a security boundary. It only
inspects Bash `gh issue create`, `gh api ... POST .../issues`, and `curl`
POSTs to the issues endpoint. It does NOT cover the GitHub web UI, GitHub
MCP/tool integrations, API calls made from inside a program, or a
`ghost butler dispatch` of a task page aimed at builder-os. The consent
reference is an unauthenticated string and is trivially forgeable. Do not
treat a passing check as proof that consent was actually obtained."""

_ERROR_MESSAGE = """\
BLOCKED by ghost core-OS origination guard: the guard could not evaluate this
request, and it mentions core OS code.

  detail: {detail}

This guard refuses on its own errors rather than allowing through — two
earlier hooks on this machine failed open and were worthless. If this is a
false block, fix the guard (gits.hooks.core_os_ticket); do not disable it."""


def evaluate(
    tool_name: str | None,
    tool_input: dict | None,
    cwd: str | None,
    *,
    env: dict | None = None,
    config_path=_UNSET,
    line_resolver=None,
) -> tuple[bool, str]:
    """Core decision. Returns ``(allow, message)``; message is empty when allowed.

    Order matters for cost: the cheap "is this even about core OS" filter runs
    before any subprocess or config read, so the common case costs one
    substring scan.

    ``config_path=None`` restricts configuration to ``env`` alone; tests use it
    so this machine's real grant in ``~/.gits/config.env`` cannot leak in.
    """
    if tool_name != "Bash":
        return True, ""
    command = (tool_input or {}).get("command", "") or ""
    if not _mentions_core_os(command, env, config_path) and not _mentions_core_os(
        cwd or "", env, config_path
    ):
        return True, ""
    if not originates_core_os_ticket(command, cwd=cwd, env=env, config_path=config_path):
        return True, ""

    mandate = mandated_lines(env, config_path)
    # Resolved late (not as a default arg) so a monkeypatched ``resolve_line``
    # is honoured — the fail-closed test depends on being able to break it.
    line = (line_resolver or resolve_line)(cwd, env)
    if line and line in mandate:
        return True, ""

    principal, utterance = consent_refs(command)
    if principal and utterance:
        return True, ""

    missing = ", ".join(
        label
        for label, present in (("principal_ref", principal), ("utterance_ref", utterance))
        if not present
    )
    return False, _MESSAGE.format(
        repos=", ".join(core_os_repos(env, config_path)),
        line=line or "unresolved (treated as unmandated)",
        mandate=", ".join(mandate) or "none configured (GHOST_CORE_OS_MANDATE is unset)",
        missing=missing,
    )


def check(payload: dict | None, raw: str = "") -> tuple[bool, str]:
    """Guard entry point. Returns ``(allow, message)``. Never raises.

    Fail-closed, bounded: if evaluation blows up — or the payload could not be
    parsed at all — we refuse **only when the raw request text mentions a
    core-OS repo**. Anything else is not this guard's business and is allowed
    untouched, so a malformed payload cannot brick every tool call.
    """
    try:
        if not isinstance(payload, dict):
            if _mentions_core_os(raw):
                return False, _ERROR_MESSAGE.format(detail="unparseable hook payload")
            return True, ""
        return evaluate(
            payload.get("tool_name"),
            payload.get("tool_input") or {},
            payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR"),
        )
    except Exception as exc:  # noqa: BLE001 - deliberate: refuse on any error
        if _mentions_core_os(raw) or _mentions_core_os(str(payload)):
            return False, _ERROR_MESSAGE.format(detail=f"{type(exc).__name__}: {exc}")
        return True, ""
