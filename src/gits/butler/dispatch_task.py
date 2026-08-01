"""``ghost butler dispatch <task-ref>`` — vault-aware task dispatcher.

Ported from vault's ``Tools/dispatch-task/dispatch.py`` (G-1, [[u78tma]]).
Orchestrates: resolve task ref → preflight → create thread (/bind as first
message) → post pointer message → atomic frontmatter writeback → rollback
(archive thread) on any failure between thread-creation and writeback →
lint → summary.

In-process throughout — talks to Discord via :mod:`gits.butler.http.api`
rather than shelling out to ``ghost butler``/``ghost discord`` subcommands.

Task-page schema fields (hardcoded here pending G-2 [[6n0iua]] which moves
the spec to ``ghost/docs/task-schema.md``):
  id, project, status, personas, cli, thread, dispatched, dispatch_msg_id,
  owner, account, model
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import socket
import sys
import time

from . import identity
from .http import api

_FM_DELIM = "---\n"


# ── frontmatter ──────────────────────────────────────────────────────────────


def parse_frontmatter(path: str) -> dict[str, str]:
    """Parse YAML frontmatter into a flat dict[str→str].

    Minimal — values are raw strings with surrounding quotes stripped;
    lists are kept as-is for the caller to parse. Same semantics as the
    vault original."""
    with open(path) as f:
        text = f.read()
    if not text.startswith(_FM_DELIM):
        sys.exit(f"ghost butler dispatch: {path} has no YAML frontmatter")
    end = text.find(_FM_DELIM, len(_FM_DELIM))
    if end == -1:
        sys.exit(f"ghost butler dispatch: {path} frontmatter has no closing ---")
    fm_block = text[len(_FM_DELIM):end]
    fields: dict[str, str] = {}
    for line in fm_block.splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        fields[k] = v
    return fields


def writeback_frontmatter_atomic(path: str, updates: dict[str, str]) -> None:
    """Update specific frontmatter fields in place, atomic via rename.

    - Existing field → in-place line replacement
    - Missing field  → insert before the closing ---
    - Body after closing --- preserved byte-for-byte
    - Atomic: write <path>.tmp then os.rename (POSIX-atomic on same fs).
      Any exception before rename leaves the original file untouched.
    """
    with open(path) as f:
        text = f.read()
    if not text.startswith(_FM_DELIM):
        raise ValueError(f"{path} has no YAML frontmatter")
    end = text.find(_FM_DELIM, len(_FM_DELIM))
    if end == -1:
        raise ValueError(f"{path} frontmatter has no closing ---")

    fm_block = text[len(_FM_DELIM):end]
    body = text[end:]
    fm_lines = fm_block.splitlines(keepends=True)

    seen: set[str] = set()
    for i, line in enumerate(fm_lines):
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            fm_lines[i] = f"{key}: {updates[key]}\n"
            seen.add(key)
    for key, value in updates.items():
        if key not in seen:
            fm_lines.append(f"{key}: {value}\n")

    new_text = _FM_DELIM + "".join(fm_lines) + body

    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(new_text)
    os.rename(tmp, path)


# ── git toplevel ─────────────────────────────────────────────────────────────


def _vault_root(cwd: str | None = None) -> str:
    """Vault root = git toplevel of the caller's cwd."""
    top = identity.run_git("rev-parse", "--show-toplevel", cwd=cwd)
    if not top:
        sys.exit("ghost butler dispatch: not in a git worktree")
    return top


# ── MACHINES.md placeholder resolution ───────────────────────────────────────


def _parse_machines(vault_root: str) -> list[tuple[str, dict[str, str]]]:
    """Parse MACHINES.md → [(machine_name, {<placeholder>: path, …}), …]"""
    path = os.path.join(vault_root, "MACHINES.md")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        text = f.read()
    machines: list[tuple[str, dict[str, str]]] = []
    name: str | None = None
    mapping: dict[str, str] | None = None
    for line in text.splitlines():
        m = re.match(r"^###\s+Machine:\s+`?([\w.-]+)`?", line)
        if m:
            if name and mapping:
                machines.append((name, mapping))
            name = m.group(1)
            mapping = {}
            continue
        if mapping is not None:
            m = re.match(r"^\|\s*`?(<[^>]+>)`?\s*\|\s*`?([^`|]+?)`?\s*\|", line)
            if m:
                placeholder = m.group(1)
                value = m.group(2).strip()
                if value and not value.startswith("_"):
                    mapping[placeholder] = value
    if name and mapping:
        machines.append((name, mapping))
    return machines


def _resolve_placeholder(value: str, vault_root: str) -> str:
    """Resolve any <placeholder> references in ``value`` via MACHINES.md.

    Matches socket.gethostname() against ``### Machine: <name>`` headers;
    falls back to the first snapshot with a stderr warning (rather than
    failing hard — matches the vault original, friendlier on a fresh
    machine, low risk in a 2-person team).
    """
    if "<" not in value or ">" not in value:
        return value
    machines = _parse_machines(vault_root)
    if not machines:
        return value
    hostname = socket.gethostname()
    target = None
    for name, mapping in machines:
        if name == hostname or name in hostname or hostname.startswith(name):
            target = mapping
            break
    if target is None:
        fallback_name, target = machines[0]
        print(
            f"ghost butler dispatch: warning — hostname {hostname!r} matches "
            f"no MACHINES.md section, falling back to {fallback_name!r}",
            file=sys.stderr,
        )
    result = value
    for placeholder, path in target.items():
        result = result.replace(placeholder, os.path.expanduser(path))
    return result


# ── task resolution ──────────────────────────────────────────────────────────


def resolve_task_file(task_ref: str, vault_root: str) -> str:
    """Find a task .md by 6-char id (preferred) or filename fragment.

    Halts on 0 matches or >1 matches — silent ambiguity is the main thing
    this codification prevents.

    Both branches treat ``-`` and `` `` as interchangeable separators so the
    same query works against the legacy space-form (``2026-05-17 abc123
    title.md``) and the current dash-form (``2026-05-17-abc123-title.md``)
    filenames."""
    projects_dir = os.path.join(vault_root, "Projects")

    if re.fullmatch(r"[a-z0-9]{6}", task_ref):
        pat = re.compile(r"[ \-]" + re.escape(task_ref) + r"[ \-]")
        matches = [
            os.path.join(root, fn)
            for root, _, files in os.walk(projects_dir)
            for fn in files
            if fn.endswith(".md") and pat.search(fn)
        ]
    else:
        needle = re.sub(r"[ \-]+", " ", task_ref.lower())
        matches = [
            os.path.join(root, fn)
            for root, _, files in os.walk(projects_dir)
            for fn in files
            if fn.endswith(".md")
            and needle in re.sub(r"[ \-]+", " ", fn.lower())
        ]

    if len(matches) == 1:
        return matches[0]
    if not matches:
        sys.exit(f"ghost butler dispatch: no task file matches {task_ref!r}")
    sys.exit(
        f"ghost butler dispatch: {len(matches)} files match {task_ref!r}:\n  "
        + "\n  ".join(matches)
    )


def resolve_work_dir(project_name: str, vault_root: str) -> str:
    """Read project README ``local_path:``, resolve <placeholders>."""
    readme = os.path.join(vault_root, "Projects", project_name, "README.md")
    if not os.path.exists(readme):
        sys.exit(f"ghost butler dispatch: project README not found: {readme}")
    with open(readme) as f:
        text = f.read()
    m = re.search(r"^local_path:\s*(\S+)", text, re.MULTILINE)
    if not m:
        sys.exit(f"ghost butler dispatch: no `local_path:` in {readme}")
    return _resolve_placeholder(m.group(1).strip(), vault_root)


_NEW_FORM_FNAME = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]{6}-(.+)$")
_OLD_FORM_FNAME = re.compile(r"^\d{4}-\d{2}-\d{2} [a-z0-9]{6} (.+)$")


def thread_title(task_path: str) -> str:
    """Strip date prefix, id, and .md for the Discord thread name.

    Handles both filename forms:
      - Old (legacy):  ``2026-05-17 abc123 Foo Bar.md`` → ``"Foo Bar"``
      - New (current): ``2026-05-17-abc123-foo-bar.md`` → ``"foo bar"``

    The new form intentionally returns lowercase with ``-`` → `` `` only,
    no Title Case — the dash-slug carries no casing information so any
    transformation would be a fabrication. Old-form titles containing
    literal dashes (e.g. ``... abc123 dash-separated names.md``) are
    preserved byte-for-byte, since old-form titles never collapsed."""
    name = re.sub(r"\.md$", "", os.path.basename(task_path))
    if m := _NEW_FORM_FNAME.match(name):
        return m.group(1).replace("-", " ")
    if m := _OLD_FORM_FNAME.match(name):
        return m.group(1)
    return name


# ── home channel ─────────────────────────────────────────────────────────────


def read_home_channel(cwd: str | None = None) -> tuple[str, str]:
    """Return ``(channel_id, guild_id)`` from the caller's worktree binding."""
    binding = identity.load_binding(cwd=cwd)
    cid = binding.get("channel_id")
    gid = binding.get("guild_id")
    if not cid or not gid:
        sys.exit(
            "ghost butler dispatch: no home channel bound for this worktree.\n"
            "  Run: ghost butler bind <channel_id>"
        )
    return cid, gid


# ── preflight ────────────────────────────────────────────────────────────────


def preflight(fm: dict[str, str]) -> None:
    status = fm.get("status", "")
    if status != "draft":
        sys.exit(
            f"ghost butler dispatch: status is {status!r}, refusing to "
            f"re-dispatch (must be 'draft' — change status manually if you "
            f"really want to)"
        )

    personas = fm.get("personas", "").strip()
    if not personas or personas in ("[]", "null", "None"):
        sys.exit(
            "ghost butler dispatch: task page has no `personas:` field "
            "(schema requires it; add e.g. `personas: [senior engineer]`)"
        )

    status_code, _ = api("/users/@me")
    if status_code != 200:
        sys.exit(
            f"ghost butler dispatch: bot identity check (/users/@me) failed "
            f"with HTTP {status_code} — check ~/.gits/config.env token"
        )


# ── pointer message ──────────────────────────────────────────────────────────


_PERSONA_TAIL = (
    "Approach this task with all these lenses at once — flag tradeoffs as a "
    "PM would, design as the architect, audit risks as the reviewer."
)

# Delivery-method section headings, matched as a *prefix* of the heading text
# (task [[dsptpl]]). Prefix and not equality because real headings carry
# parenthetical suffixes — `## 交付方式（本票自己的 —— 显式覆盖派单尾巴）` is the
# heading that motivated this ticket, and an equality match misses it outright.
_DELIVERY_HEADING_PREFIXES = ("交付方式", "delivery")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)


def page_declares_delivery(task_path: str) -> bool:
    """True when the task page has a delivery-method section.

    Existence only — ghost deliberately does NOT parse what the section
    *says*. Interpreting "diff or PR?" would chain ghost's surface to the
    operator's page-authoring wording forever; the executor already reads the
    whole page (dispatch is pointer-style), so ghost's job is just to step
    aside and say who is authoritative.

    Exception-proof by design: an unreadable/missing/binary page returns
    False, which lands on the conservative diff-only default rather than
    killing the dispatch.
    """
    try:
        with open(task_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return False
    for m in _HEADING_RE.finditer(text):
        heading = m.group(1).strip().lower()
        if heading.startswith(_DELIVERY_HEADING_PREFIXES):
            return True
    return False


# Delivery half of the impl tail — the task page decides this one. Two
# variants, and they degrade into each other: the default branch also carries
# the precedence sentence, so a heading we failed to recognise still resolves
# in the page's favour instead of reproducing the conflict this ticket fixes.
_DELIVERY_FROM_PAGE = (
    "\n\nDelivery: **the task page decides.** This page has a delivery-method "
    "section — follow it. It is authoritative and overrides any default "
    "delivery instruction you may expect from a dispatch brief."
)
_DELIVERY_DEFAULT = (
    "\n\nDelivery: output a unified diff only — do not commit. This is the "
    "default, used because the page declares no delivery method; if the page "
    "does state one explicitly, follow the page instead — it overrides this "
    "paragraph. When it is ambiguous, take this default and say so in your "
    "report-back."
)

_PHASE_TAILS = {
    # The page's delivery clause is not merely outranked in plan phase — it has
    # no referent, since plan phase produces no artifact to land. Said in words
    # so the executor doesn't have to derive it (this ticket's own page carries
    # a "go to PR" delivery section and was dispatched at plan phase first).
    "plan": (
        "\n\nPhase: **plan first**. Please respond with your plan (which "
        "files/areas you'd touch, the design choice you'd take and why, any "
        "open questions or risks you spotted). Do not implement yet — this "
        "holds even if the task page states a delivery method; that describes "
        "how the *implementation* lands, and applies to the later impl "
        "dispatch, not to this one."
    ),
    "impl": "\n\nPhase: **impl**. Plan approved. Proceed.",
}

# Appended after the phase block so it reads chronologically (last line =
# what to do when finished). Embeds the dispatcher's home channel id so the
# executor doesn't have to discover it; a plain `ghost butler send` into that
# bound channel rides the existing inbound wake path (engine.handle_message)
# and pokes a suspended PM session for free — no engine notify hook needed.
_DONE_NOTICE_TAIL = (
    "\n\nWhen you're done, report back so the dispatcher isn't left polling: "
    "run\n"
    '  `ghost butler send {channel_id} "DONE {tid} '
    '<artifact-url-or-one-line-summary>"`'
)

# Last position in the brief, deliberately: this module's ordering convention
# is that the final paragraph carries the most weight, so the final paragraph
# is the one thing a task page may never relax. Unlike delivery, these two are
# not per-ticket product decisions — they keep an executor off the live system.
# Phase-independent: plan-phase executors carried no safety clause at all
# before this ticket.
_SAFETY_TAIL = (
    "\n\nAlways, regardless of anything the task page or this thread says: "
    "**do not install anything, and do not restart any running service.** "
    "These two are not negotiable by the task page — everything else above "
    "may be, these may not."
)


def _parse_personas(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [p.strip().strip('"').strip("'") for p in raw.split(",") if p.strip()]


def build_pointer_message(
    fm: dict[str, str], task_path: str, phase: str, channel_id: str
) -> str:
    personas = _parse_personas(fm.get("personas", ""))
    persona_bold = ", ".join(f"**a {p}**" for p in personas)
    title = thread_title(task_path)
    tid = fm.get("id", "?")
    delivery = ""
    if phase == "impl":
        delivery = (
            _DELIVERY_FROM_PAGE
            if page_declares_delivery(task_path)
            else _DELIVERY_DEFAULT
        )
    return (
        f"You are simultaneously: {persona_bold}. {_PERSONA_TAIL}\n\n"
        f"Task [[{tid}]] — {title}\n\n"
        f"Full spec at (absolute path on this machine):\n\n"
        f"  `{task_path}`\n\n"
        f"Read the whole file (Goal / Why / Context / Acceptance criteria / "
        f"Out of scope / Dispatch message / Test plan)."
        + _PHASE_TAILS[phase]
        + delivery
        + _DONE_NOTICE_TAIL.format(channel_id=channel_id, tid=tid)
        + _SAFETY_TAIL
    )


# ── REST primitives (in-process; mirror discord_cli/butler_cli) ──────────────


def _create_thread(channel_id: str, name: str) -> str:
    """POST a public thread; return thread id. Exits on REST failure."""
    status, resp = api(
        f"/channels/{channel_id}/threads",
        method="POST",
        body={"name": name, "type": 11, "auto_archive_duration": 1440},
    )
    if status not in (200, 201):
        sys.exit(
            f"ghost butler dispatch: thread create failed [{status}]: {resp}"
        )
    tid = resp.get("id") if isinstance(resp, dict) else None
    if not tid:
        sys.exit(
            f"ghost butler dispatch: thread create returned no id; resp={resp!r}"
        )
    return tid


def _archive_thread(thread_id: str) -> None:
    """Best-effort archive for rollback. Never raises."""
    try:
        api(
            f"/channels/{thread_id}",
            method="PATCH",
            body={"archived": True, "locked": False},
        )
    except Exception:
        pass


def _post_message(channel_id: str, content: str) -> str | None:
    """POST a message; return message id, or None on failure."""
    status, resp = api(
        f"/channels/{channel_id}/messages",
        method="POST",
        body={"content": content},
    )
    if status not in (200, 201):
        return None
    return resp.get("id") if isinstance(resp, dict) else None


# ── account resolution ──────────────────────────────────────────────────────


def _pick_account_or_none() -> str | None:
    """Resolve ``--account auto``: pick least-loaded launchable account.

    Returns ``None`` when the multi-account vault is uninitialized, has
    ≤1 account, or no account passes the credential gate — caller
    omits ``--account=`` from the ``/bind`` message and the engine
    keeps legacy ``manifest.default`` behavior.
    """
    try:
        from ..config import Settings
        from ..core.account import AccountLayout
        from ..core.account_load import pick_account
        from ..core.account_vault import AccountVault
    except Exception as e:  # pragma: no cover — import wiring
        print(
            f"ghost butler dispatch: account picker imports failed ({e}); "
            f"continuing without --account",
            file=sys.stderr,
        )
        return None
    try:
        settings = Settings()
        layout = AccountLayout()
        vault = AccountVault(settings.state_dir, layout=layout)
        return pick_account(vault, layout=layout)
    except Exception as e:
        print(
            f"ghost butler dispatch: account picker failed ({e}); "
            f"continuing without --account",
            file=sys.stderr,
        )
        return None


def _warn_if_benched(name: str) -> None:
    """Warn (stderr) when an explicitly pinned account is benched.

    Warn-but-proceed (task [[5wuazc]]): an explicit ``--account <name>``
    pin is a conscious per-action operator override, mirroring the
    existing ``--account`` precedence philosophy. Best-effort and
    exception-proof — dispatch must never die on a vault hiccup, and an
    unknown name is left to the downstream ``/bind`` validation.
    """
    try:
        import datetime as _dt

        from ..config import Settings
        from ..core.account import AccountLayout
        from ..core.account_load import bench_warning
        from ..core.account_vault import AccountVault

        settings = Settings()
        vault = AccountVault(settings.state_dir, layout=AccountLayout())
        entry = vault.get(name)
        if entry is None:
            return
        warning = bench_warning(entry, _dt.datetime.now(_dt.UTC).timestamp())
        if warning:
            print(f"ghost butler dispatch: [warn] {warning}", file=sys.stderr)
    except Exception as e:
        print(
            f"ghost butler dispatch: bench check for --account={name} failed "
            f"({e}); continuing",
            file=sys.stderr,
        )


# ── lint ─────────────────────────────────────────────────────────────────────


def lint(task_path: str, tid: str, expected_status: str) -> list[str]:
    """Return a list of failure strings (empty = pass).

    Lint failure does NOT roll back the dispatch — the thread + writeback
    may have fully landed and lint may flag transient Discord latency.
    Brief retry on the message-count check soaks up the common false
    positive."""
    failures: list[str] = []
    fm = parse_frontmatter(task_path)

    for field in ("thread", "dispatched", "dispatch_msg_id", "owner"):
        if not fm.get(field):
            failures.append(f"frontmatter `{field}:` missing or empty")

    if fm.get("status") != expected_status:
        failures.append(
            f"frontmatter `status:` is {fm.get('status')!r}, expected "
            f"{expected_status!r}"
        )

    thread_val = fm.get("thread", "")
    m = re.search(r"/(\d{15,})\)", thread_val)
    if not m:
        failures.append(
            f"frontmatter `thread:` has no extractable thread id "
            f"(got {thread_val!r})"
        )
    elif m.group(1) != tid:
        failures.append(
            f"frontmatter `thread:` tid {m.group(1)} != dispatched tid {tid}"
        )

    deadline = time.time() + 10
    count = 0
    while time.time() < deadline:
        status, body = api(
            f"/channels/{tid}/messages", query={"limit": 10, "after": None}
        )
        if status == 200 and isinstance(body, list):
            count = len(body)
            if count >= 2:
                break
        time.sleep(2)
    if count < 2:
        failures.append(
            f"thread {tid} read-back returned {count} messages "
            f"(expected ≥ 2 after 10s)"
        )

    return failures


# ── main flow ────────────────────────────────────────────────────────────────


def dispatch_task(
    task_ref: str,
    phase: str,
    cwd: str | None = None,
    send_decorated=None,
    account: str | None = None,
    model: str | None = None,
) -> None:
    """Orchestrate the full dispatch. ``send_decorated`` is injected by
    :mod:`gits.butler.butler_cli` to avoid a circular import; signature is
    ``(target_id: str, content: str, *, cwd: str | None) -> str`` returning
    the new message id (raises on REST failure)."""
    if cwd is None:
        cwd = os.getcwd()
    if send_decorated is None:  # late import to keep this module importable standalone
        from .butler_cli import send_decorated as send_decorated  # noqa: F811

    vault_root = _vault_root(cwd=cwd)
    task_path = resolve_task_file(task_ref, vault_root)
    fm = parse_frontmatter(task_path)

    project = fm.get("project")
    if not project:
        sys.exit(
            f"ghost butler dispatch: {task_path} has no `project:` field"
        )
    cli = fm.get("cli", "claude")

    work_dir = resolve_work_dir(project, vault_root)
    preflight(fm)
    owner, _ = identity.resolve_user(cwd=cwd)
    if owner is None:
        sys.exit(
            "ghost butler dispatch: cannot determine owner identity.\n"
            "  Same chain as `ghost butler whoami`. Provide one of:\n"
            "    BUTLER_USER=<name> env var\n"
            "    cd into a personal worktree "
            "(<name>/work branch or vault-<name> dir)"
        )
    channel_id, guild_id = read_home_channel(cwd=cwd)

    title = thread_title(task_path)
    pointer = build_pointer_message(fm, task_path, phase, channel_id)

    # Account resolution: flag > auto-picker. The task-page `account:` field
    # is NEVER read for resolution — it is a write-only record (see writeback
    # below). `--account auto` (or omitted) ⇒ auto-picker; any other value pins.
    flag = (account or "").strip() or None
    if flag and flag != "auto":
        resolved_account: str | None = flag
        account_source = "flag"
        _warn_if_benched(flag)
    else:
        resolved_account = _pick_account_or_none()
        account_source = "auto" if resolved_account else "auto (no candidate)"

    # Model resolution (openspec add-dispatch-model-pin): flag > task-page
    # `model:` field > none. Unlike `account:`, the page field IS read on
    # input — model grade is a property of the task, declared by its author.
    model_flag = (model or "").strip() or None
    page_model = (fm.get("model") or "").strip() or None
    if model_flag:
        resolved_model: str | None = model_flag
        model_source = "flag"
    elif page_model:
        resolved_model = page_model
        model_source = "task page"
    else:
        resolved_model = None
        model_source = ""

    # Fail fast — before any thread is created.
    if resolved_model:
        try:
            from ..core.account import validate_model_name
            validate_model_name(resolved_model)
        except ValueError as e:
            sys.exit(
                f"ghost butler dispatch: invalid model name "
                f"(source: {model_source}): {e}"
            )

    # Validate any concrete name before sending it on /bind.
    if resolved_account:
        try:
            from ..core.account import validate_account_name
            validate_account_name(resolved_account)
        except Exception as e:
            sys.exit(
                f"ghost butler dispatch: invalid account name "
                f"{resolved_account!r} (source: {account_source}): {e}"
            )

    # Step 1: create thread (no rollback target yet on failure)
    tid = _create_thread(channel_id, title)

    # Step 2: post /bind as the first message (decorated). Rollback on failure.
    bind_msg = f"/bind {work_dir} {cli}"
    if resolved_account:
        bind_msg += f" --account={resolved_account}"
    if resolved_model:
        bind_msg += f" --model={resolved_model}"
    try:
        bind_mid = send_decorated(tid, bind_msg, cwd=cwd)
    except SystemExit as e:
        _archive_thread(tid)
        sys.exit(
            f"ghost butler dispatch: /bind send to thread {tid} failed "
            f"({e}); archived thread"
        )
    if not bind_mid:
        _archive_thread(tid)
        sys.exit(
            f"ghost butler dispatch: /bind send to thread {tid} returned no "
            f"message id; archived thread"
        )

    # Give the bot ~2s to ack /bind before posting the substantive pointer.
    time.sleep(2)

    # Step 3: pointer (rollback on failure)
    try:
        pointer_msg_id = send_decorated(tid, pointer, cwd=cwd)
    except SystemExit as e:
        _archive_thread(tid)
        sys.exit(
            f"ghost butler dispatch: pointer send to thread {tid} failed "
            f"({e}); archived thread"
        )
    if not pointer_msg_id:
        _archive_thread(tid)
        sys.exit(
            f"ghost butler dispatch: pointer send to thread {tid} returned "
            f"no message id; archived thread"
        )

    # Step 4: atomic frontmatter writeback (rollback on failure)
    today = datetime.date.today().isoformat()
    status_value = "dispatched (plan-phase)" if phase == "plan" else "dispatched"
    thread_url = f"https://discord.com/channels/{guild_id}/{tid}"
    updates = {
        "thread": f'"[{title}]({thread_url})"',
        "dispatched": today,
        "dispatch_msg_id": pointer_msg_id,
        "owner": owner,
        "status": status_value,
    }
    # `account:` is a write-only record: always stamp the account actually
    # used, overwriting any stale/author-set value so the page faithfully
    # reflects the most recent dispatch. (Nothing is written when the picker
    # found no candidate — the page stays clean, matching legacy.)
    if resolved_account:
        updates["account"] = resolved_account
    # `model:` mirrors the account stamp: record what this dispatch actually
    # used (the flag may have overridden a stale page value). Nothing is
    # written when no model was pinned.
    if resolved_model:
        updates["model"] = resolved_model
    try:
        writeback_frontmatter_atomic(task_path, updates)
    except Exception as e:
        _archive_thread(tid)
        sys.exit(
            f"ghost butler dispatch: frontmatter writeback failed ({e}); "
            f"archived thread {tid}; task page unchanged"
        )

    # Step 5: lint (non-rollback; exits non-zero if any check fails)
    failures = lint(task_path, tid, status_value)

    print()
    print(f"✓ dispatched [[{fm.get('id')}]] \"{title}\"")
    print(f"  thread:  {tid}")
    print(f"  bound:   {work_dir} (cli: {cli})")
    print(f"  channel: {channel_id}")
    print(f"  owner:   {owner}")
    print(f"  phase:   {phase}  (status → {status_value!r})")
    if phase == "impl":
        # Surface which side the delivery clause landed on, so a page that
        # forgot its delivery section shows up here rather than silently
        # shipping the diff-only default.
        if page_declares_delivery(task_path):
            print("  delivery: task page (delivery section found)")
        else:
            print("  delivery: diff-only (default — page declares none)")
    if resolved_account:
        print(f"  account: {resolved_account} (source: {account_source})")
    else:
        print("  account: (legacy default — no --account on /bind)")
    if resolved_model:
        print(f"  model:   {resolved_model} (source: {model_source})")
    print("  frontmatter: thread, dispatched, dispatch_msg_id, owner, status updated")
    extra_stamps = [
        k for k, used in (("account", resolved_account), ("model", resolved_model))
        if used
    ]
    if extra_stamps:
        print(f"               + {', '.join(extra_stamps)}")
    if failures:
        print()
        print("✗ lint FAILED:")
        for f in failures:
            print(f"  - {f}")
        print()
        print("  Dispatch landed (thread exists, frontmatter written) but one")
        print("  or more sanity checks failed. Investigate before relying on it.")
        sys.exit(1)
    print("  lint:    OK (all checks pass)")
    print()
    print(f"Next: ghost butler read-thread {tid}")


def cmd_dispatch(args: argparse.Namespace) -> None:
    """argparse entrypoint; thin wrapper around :func:`dispatch_task`."""
    dispatch_task(
        args.task_ref,
        args.phase,
        account=getattr(args, "account", None),
        model=getattr(args, "model", None),
    )
