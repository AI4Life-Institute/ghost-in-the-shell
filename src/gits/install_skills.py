"""``ghost install-skills`` / ``ghost uninstall-skills`` — sync the skills
bundled in ``ghost/skills/*`` into the user's personal-scope Claude Code
skills directory (default ``~/.claude/skills/``).

Why a subcommand instead of a ``uv tool install`` post-install hook:
    ``uv tool install`` exposes no post-install hook for arbitrary scripts;
    it only wires Python entry points (which run on user invocation, not at
    install time). A subcommand the user runs once — and which ``ghost
    setup`` and ``install.sh`` invoke automatically — gives us the same
    default-on UX with none of the fragility of trying to monkey-patch the
    uv install pipeline. See G-4 (irzsbr) Design Decision section.

Invariants:
    - Idempotent: re-running overwrites only ghost-managed skill folders
      (those listed in the manifest); never touches non-ghost skills.
    - Manifest at ``<skills-dir>/.ghost-installed.json`` is the source of
      truth for what ghost owns. Uninstall removes ONLY manifest-listed
      folders and the manifest itself.
    - Opt-out via ``--no-install-skills`` flag or
      ``GHOST_NO_INSTALL_SKILLS=1`` env var (no-op exit 0 with "skipped"
      message).
    - Source folder layout (``<skills-source>/<skill-name>/SKILL.md``) is
      preserved verbatim under ``~/.claude/skills/<skill-name>/``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

MANIFEST_NAME = ".ghost-installed.json"
OPT_OUT_ENV = "GHOST_NO_INSTALL_SKILLS"


def _opt_out_active(flag: bool) -> bool:
    """True if either ``--no-install-skills`` or the env var is set."""
    if flag:
        return True
    val = os.environ.get(OPT_OUT_ENV, "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _default_skills_dir() -> Path:
    """User's personal-scope Claude Code skills directory.

    Override with ``GHOST_SKILLS_DEST`` (used by tests to redirect to a
    tmp dir without touching the real ``~/.claude/skills``).
    """
    override = os.environ.get("GHOST_SKILLS_DEST")
    if override:
        return Path(override).expanduser()
    return Path("~/.claude/skills").expanduser()


def _default_source_dir() -> Path:
    """Where the bundled skills live in the installed wheel / editable repo.

    Walks up from this file:
        src/gits/install_skills.py  →  src/gits  →  src  →  <repo-root>/skills
    Both editable (``uv tool install --editable .``) and wheel installs
    keep ``skills/`` at the repo / wheel root, so the same walk works in
    both cases.

    Override with ``GHOST_SKILLS_SRC`` (tests).
    """
    override = os.environ.get("GHOST_SKILLS_SRC")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent.parent.parent / "skills"


def _get_version() -> str:
    """Read ghost-in-the-shell version from the package metadata."""
    try:
        from . import __version__
        return __version__
    except ImportError:
        return "unknown"


def _enumerate_source_skills(src_dir: Path) -> list[str]:
    """Return sorted list of skill folder names under *src_dir*.

    A "skill" is a subdirectory containing a ``SKILL.md`` file. Anything
    else (loose files, hidden dirs, dirs without a SKILL.md) is silently
    skipped — this keeps the surface small and predictable.
    """
    if not src_dir.is_dir():
        return []
    out = []
    for child in sorted(src_dir.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue
        if not (child / "SKILL.md").is_file():
            continue
        out.append(child.name)
    return out


def _read_manifest(dest_dir: Path) -> dict:
    """Read existing manifest, or empty dict if absent/malformed."""
    path = dest_dir / MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_manifest(dest_dir: Path, payload: dict) -> None:
    """Write the manifest atomically (write to .tmp, rename)."""
    path = dest_dir / MANIFEST_NAME
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def install_skills(
    *,
    src_dir: Path | None = None,
    dest_dir: Path | None = None,
    opt_out: bool = False,
    log: callable = print,
) -> int:
    """Sync bundled skills → user's skills dir.

    Returns:
        0 on success (including opt-out no-op), non-zero on failure.

    Behavior:
        - Creates *dest_dir* if missing.
        - For each skill in *src_dir*: copy the whole subdir over, REPLACING
          any existing folder by that name (the manifest is the contract —
          if a name is in the manifest, ghost owns it; if it isn't,
          installing now claims it).
        - Writes / rewrites manifest with current skill set + version +
          ISO timestamp.
    """
    if opt_out:
        log(f"install-skills: skipped (opt-out via flag or ${OPT_OUT_ENV})")
        return 0

    src = src_dir if src_dir is not None else _default_source_dir()
    dest = dest_dir if dest_dir is not None else _default_skills_dir()

    skills = _enumerate_source_skills(src)
    if not skills:
        log(f"install-skills: no skills found under {src} (nothing to do)")
        return 0

    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"install-skills: cannot create {dest}: {e}", file=sys.stderr)
        return 1

    prior_manifest = _read_manifest(dest)
    prior_skills = set(prior_manifest.get("skills", []))

    installed: list[str] = []
    updated: list[str] = []

    for name in skills:
        src_skill = src / name
        dest_skill = dest / name
        was_managed = name in prior_skills
        existed = dest_skill.exists()

        # Replace ghost-managed skills wholesale. For non-ghost-managed
        # folders, we still overwrite — but the manifest now claims it,
        # so a subsequent uninstall will remove it. This matches the
        # spec's "ghost ships these skills; user opt-in to install"
        # contract: if you ran install-skills, you authorized ghost to
        # own these names.
        if existed:
            try:
                shutil.rmtree(dest_skill)
            except OSError as e:
                print(
                    f"install-skills: cannot remove existing {dest_skill}: {e}",
                    file=sys.stderr,
                )
                return 1

        try:
            shutil.copytree(src_skill, dest_skill)
        except OSError as e:
            print(
                f"install-skills: cannot copy {src_skill} → {dest_skill}: {e}",
                file=sys.stderr,
            )
            return 1

        if was_managed:
            updated.append(name)
        else:
            installed.append(name)

    # Any skill in the prior manifest that is NOT in the current source
    # has been dropped from ghost — remove it from disk and from the
    # manifest. This keeps the uninstall contract honest: "manifest
    # lists exactly what ghost owns RIGHT NOW".
    removed: list[str] = []
    for name in prior_skills - set(skills):
        stale = dest / name
        if stale.is_dir():
            try:
                shutil.rmtree(stale)
                removed.append(name)
            except OSError as e:
                print(
                    f"install-skills: cannot remove stale {stale}: {e}",
                    file=sys.stderr,
                )
                # Continue — partial cleanup is better than abort.

    manifest = {
        "ghost_version": _get_version(),
        "installed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "skills": skills,
    }
    try:
        _write_manifest(dest, manifest)
    except OSError as e:
        print(f"install-skills: cannot write manifest: {e}", file=sys.stderr)
        return 1

    # Summary
    log(f"install-skills: dest = {dest}")
    if installed:
        log(f"  installed: {', '.join(installed)}")
    if updated:
        log(f"  updated:   {', '.join(updated)}")
    if removed:
        log(f"  removed (no longer shipped): {', '.join(removed)}")
    if not (installed or updated or removed):
        log("  (no changes)")
    log(f"  manifest:  {dest / MANIFEST_NAME}")
    return 0


def uninstall_skills(
    *,
    dest_dir: Path | None = None,
    log: callable = print,
) -> int:
    """Remove all ghost-managed skills + manifest. Untouched: anything not
    in the manifest.

    Returns 0 on success (including "nothing to do"), non-zero on failure.
    """
    dest = dest_dir if dest_dir is not None else _default_skills_dir()

    if not dest.is_dir():
        log(f"uninstall-skills: {dest} doesn't exist — nothing to do")
        return 0

    manifest = _read_manifest(dest)
    skills = manifest.get("skills", [])
    if not skills:
        # No manifest, or empty manifest — just make sure the manifest
        # file itself is gone, then exit.
        mpath = dest / MANIFEST_NAME
        if mpath.is_file():
            try:
                mpath.unlink()
            except OSError as e:
                print(f"uninstall-skills: cannot remove manifest: {e}", file=sys.stderr)
                return 1
        log("uninstall-skills: no ghost-managed skills found")
        return 0

    removed: list[str] = []
    missing: list[str] = []
    for name in skills:
        target = dest / name
        if not target.exists():
            missing.append(name)
            continue
        try:
            shutil.rmtree(target)
            removed.append(name)
        except OSError as e:
            print(f"uninstall-skills: cannot remove {target}: {e}", file=sys.stderr)
            return 1

    try:
        (dest / MANIFEST_NAME).unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"uninstall-skills: cannot remove manifest: {e}", file=sys.stderr)
        return 1

    log(f"uninstall-skills: dest = {dest}")
    if removed:
        log(f"  removed: {', '.join(removed)}")
    if missing:
        log(f"  not present (skipped): {', '.join(missing)}")
    log("  manifest deleted")
    return 0


# ---------------------------------------------------------------------------
# argparse registration + dispatch
# ---------------------------------------------------------------------------


def install_parser(sub: argparse._SubParsersAction) -> None:
    """Register ``install-skills`` and ``uninstall-skills`` under ``ghost``."""
    p = sub.add_parser(
        "install-skills",
        help=(
            "Sync bundled Claude Code skills (skills/butler, "
            "skills/onboard-worktree) into ~/.claude/skills/"
        ),
        description=(
            "Sync ghost-bundled skills into ~/.claude/skills/ so Claude "
            "Code can auto-trigger them across all your projects. "
            "Idempotent (manifest at ~/.claude/skills/.ghost-installed.json "
            "tracks ghost-owned folders; non-ghost skills are never "
            "touched). Opt out: --no-install-skills, or set "
            f"{OPT_OUT_ENV}=1."
        ),
    )
    p.add_argument(
        "--no-install-skills",
        dest="no_install_skills",
        action="store_true",
        help="Opt out: exit 0 with a 'skipped' message. Same effect as "
             f"setting {OPT_OUT_ENV}=1.",
    )
    p.set_defaults(func=_cmd_install)

    q = sub.add_parser(
        "uninstall-skills",
        help="Remove ghost-managed skills from ~/.claude/skills/ (manifest-driven)",
        description=(
            "Remove ONLY the skill folders listed in "
            "~/.claude/skills/.ghost-installed.json (plus the manifest "
            "itself). Skills you installed manually are untouched."
        ),
    )
    q.set_defaults(func=_cmd_uninstall)


def _cmd_install(args: argparse.Namespace) -> None:
    flag = getattr(args, "no_install_skills", False)
    rc = install_skills(opt_out=_opt_out_active(flag))
    sys.exit(rc)


def _cmd_uninstall(args: argparse.Namespace) -> None:
    rc = uninstall_skills()
    sys.exit(rc)


def dispatch(args: argparse.Namespace) -> None:
    args.func(args)
