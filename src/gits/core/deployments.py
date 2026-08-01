"""Deployment provenance — answer "which ghost code is actually live".

Ghost task whlive
-----------------
On 2026-08-01 this machine had **two coexisting ghost deployments and neither
pointed at master**, and nobody could say so without an hour of archaeology:

* the ``ghost`` / ``gits`` CLI came from a June uv-tool snapshot whose receipt
  named a *local directory* as its requirement;
* the PreToolUse hooks (``gits guard``) and the Discord bot ran from an
  **editable** install pointed at a working checkout that happened to be
  parked on a dirty feature branch.

The consequences were not theoretical: a new ``config.env`` key crashed the
stale CLI outright (``Settings`` is ``extra='forbid'``), a routine
``uv tool install --reinstall`` would have packed another ticket's unfinished
untracked source into the live tool, and the *refusal* hooks were running
whatever that checkout was parked on.

This module makes that state readable in one command. It is deliberately
**observability, not automation** — it changes nothing, it only reports.

Provenance is read, never inferred
----------------------------------
Every deployment already carries the installer's own record of where its code
came from: :pep:`610` ``direct_url.json`` inside its ``*.dist-info/``.

* a git install records ``vcs_info.commit_id`` — an exact 40-hex sha, so
  "which code is this" is a *file read*, not a guess;
* an editable install records ``dir_info.editable`` plus the source path, and
  the answer then depends on that checkout, so the checkout is interrogated
  with git (HEAD, branch, dirty, untracked sources, distance to master).

Everything here takes its inputs explicitly (paths, settings files, compare
ref) so the three interesting states can be *constructed* in tests rather than
observed on one machine at one moment.

Honest limits
-------------
* Distance to ``origin/master`` is measured **as of the last fetch** in the
  comparison repo unless ``fetch=True`` is requested.
* A dirty editable checkout means **the sha does not describe what runs**.
  That is reported as a finding, not smoothed over.
* Probing another deployment's ``Settings`` fields means *executing another
  deployment's code*. It is a subprocess with a timeout and every failure is
  swallowed into an ``unknown`` finding with a reason — a diagnostic that can
  itself hang or crash is worse than none. ``unknown`` is never reported as
  "fine".
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Canonical wheel/dist name (PEP 503 normalisation with underscores), used to
# pick *our* dist-info out of an environment that may hold several editable
# installs (this machine's .venv also carries builder-os).
DIST_NAME = "ghost_in_the_shell"

DEFAULT_COMPARE_REF = "origin/master"
GIT_TIMEOUT = 10.0
PROBE_TIMEOUT = 10.0

#: Prefixes whose *untracked* files would be packed into a wheel built from a
#: working tree — the failure mode behind "don't install a dirty tree".
SOURCE_PREFIXES = ("src/",)

_PROBE_CODE = (
    "import json,gits.config;"
    "print(json.dumps(sorted(gits.config.Settings.model_fields)))"
)


# ── findings ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Finding:
    """One thing worth saying about a deployment.

    ``level`` is ``"error"`` (drift we can prove), ``"warn"`` (worth seeing),
    or ``"unknown"`` (we could not determine — never treat as fine).
    """

    level: str
    code: str
    message: str


# ── provenance records (PEP 610) ─────────────────────────────────────────


@dataclass(frozen=True)
class GitOrigin:
    """Installed from a VCS URL; ``commit_id`` is exact."""

    url: str
    commit_id: str
    requested_revision: str | None = None

    @property
    def summary(self) -> str:
        rev = f" (requested {self.requested_revision})" if self.requested_revision else ""
        return f"git {self.url} @ {self.commit_id}{rev}"


@dataclass(frozen=True)
class LocalOrigin:
    """Installed from a local directory (editable or a one-off copy)."""

    path: Path
    editable: bool

    @property
    def summary(self) -> str:
        kind = "editable" if self.editable else "local dir"
        return f"{kind} {self.path}"


Origin = GitOrigin | LocalOrigin


@dataclass(frozen=True)
class WorktreeState:
    """Git state of a checkout a deployment runs from."""

    path: Path
    head_sha: str
    branch: str | None  # None == detached HEAD
    dirty: bool
    modified: tuple[str, ...] = ()
    untracked: tuple[str, ...] = ()
    untracked_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class Distance:
    """How far a sha is from the comparison ref, as of the last fetch."""

    ahead: int | None = None
    behind: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class ConfigCompat:
    """Whether a deployment declares every key present in ``config.env``.

    ``status`` is ``"ok"``, ``"missing"`` (it would crash on those keys) or
    ``"unknown"`` (probe failed — ``reason`` says why).
    """

    status: str
    declared: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    reason: str | None = None


@dataclass
class Deployment:
    """One live deployment: an environment plus every role it serves."""

    executable: Path
    roles: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    env_root: Path | None = None
    dist_info: Path | None = None
    origin: Origin | None = None
    worktree: WorktreeState | None = None
    distance: Distance | None = None
    config: ConfigCompat | None = None
    findings: list[Finding] = field(default_factory=list)
    receipt_requirement: str | None = None

    @property
    def sha(self) -> str | None:
        """The 40-hex sha of the code that is live, when one describes it."""
        if isinstance(self.origin, GitOrigin):
            return self.origin.commit_id
        if self.worktree is not None:
            return self.worktree.head_sha
        return None

    @property
    def sha_is_complete(self) -> bool:
        """False when uncommitted changes mean the sha does not say what runs."""
        return not (self.worktree is not None and self.worktree.dirty)


@dataclass
class Report:
    """Everything ``ghost doctor`` knows, in one value."""

    deployments: list[Deployment] = field(default_factory=list)
    compare_ref: str = DEFAULT_COMPARE_REF
    compare_repo: Path | None = None
    compare_sha: str | None = None
    fetched_at: str | None = None
    config_env: Path | None = None
    config_keys: tuple[str, ...] = ()
    findings: list[Finding] = field(default_factory=list)

    def all_findings(self) -> list[tuple[Deployment | None, Finding]]:
        out: list[tuple[Deployment | None, Finding]] = [(None, f) for f in self.findings]
        for dep in self.deployments:
            out.extend((dep, f) for f in dep.findings)
        return out

    @property
    def errors(self) -> list[tuple[Deployment | None, Finding]]:
        return [p for p in self.all_findings() if p[1].level == "error"]

    @property
    def unknowns(self) -> list[tuple[Deployment | None, Finding]]:
        return [p for p in self.all_findings() if p[1].level == "unknown"]

    @property
    def warnings(self) -> list[tuple[Deployment | None, Finding]]:
        return [p for p in self.all_findings() if p[1].level == "warn"]

    @property
    def verdict(self) -> str:
        """``"drift"`` | ``"unresolved"`` | ``"clean"``.

        ``unresolved`` is never folded into ``clean``: a probe we could not
        run is not evidence of agreement.
        """
        if self.errors:
            return "drift"
        if self.unknowns:
            return "unresolved"
        return "clean"

    @property
    def exit_code(self) -> int:
        """1 only for provable drift; ``unresolved`` stays 0 but never reads clean."""
        return 1 if self.errors else 0


# ── git plumbing ─────────────────────────────────────────────────────────


def _git(args: Sequence[str], cwd: Path, *, timeout: float = GIT_TIMEOUT) -> tuple[int, str, str]:
    """Run git, never raise. Returns ``(returncode, raw stdout, stderr)``.

    stdout is deliberately **not** stripped: ``status --porcelain`` encodes
    state in the first two columns, and a leading space there is data.
    """
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, "", f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout, proc.stderr.strip()


def _git_line(args: Sequence[str], cwd: Path, *, timeout: float = GIT_TIMEOUT) -> tuple[int, str]:
    """``_git`` for commands whose output is a single scalar."""
    rc, out, _ = _git(args, cwd, timeout=timeout)
    return rc, out.strip()


def is_git_worktree(path: Path) -> bool:
    rc, out = _git_line(["rev-parse", "--is-inside-work-tree"], path)
    return rc == 0 and out == "true"


def _parse_status(status: str) -> tuple[list[str], list[str]]:
    """Split ``git status --porcelain`` into (modified, untracked) paths."""
    modified: list[str] = []
    untracked: list[str] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        code, name = line[:2], line[3:]
        # Renames are reported as ``old -> new``; the new path is what ships.
        if " -> " in name:
            name = name.split(" -> ", 1)[1]
        name = name.strip().strip('"')
        if not name:
            continue
        if code == "??":
            untracked.append(name)
        else:
            modified.append(name)
    return modified, untracked


def inspect_worktree(
    path: Path, *, source_prefixes: Sequence[str] = SOURCE_PREFIXES
) -> WorktreeState | None:
    """Read HEAD / branch / dirtiness of ``path``; None if it is not a checkout."""
    if not path.is_dir() or not is_git_worktree(path):
        return None

    rc, head = _git_line(["rev-parse", "HEAD"], path)
    if rc != 0:
        return None

    rc, branch = _git_line(["symbolic-ref", "--short", "-q", "HEAD"], path)
    branch_name = branch or None if rc == 0 else None

    rc, status, _ = _git(["status", "--porcelain"], path)
    modified, untracked = _parse_status(status) if rc == 0 else ([], [])

    untracked_sources = tuple(
        n for n in untracked if any(n.startswith(p) for p in source_prefixes)
    )
    return WorktreeState(
        path=path,
        head_sha=head,
        branch=branch_name,
        dirty=bool(modified or untracked),
        modified=tuple(modified),
        untracked=tuple(untracked),
        untracked_sources=untracked_sources,
    )


def measure_distance(repo: Path | None, sha: str | None, compare_ref: str) -> Distance:
    """Commits ``sha`` is ahead of / behind ``compare_ref``, using ``repo``'s objects.

    Both commits must be known to ``repo``; when they are not, that is
    reported as an error string rather than silently as "no distance".
    """
    if repo is None:
        return Distance(error="no comparison repository available")
    if not sha:
        return Distance(error="deployment sha unknown")
    rc, _ = _git_line(["cat-file", "-e", f"{sha}^{{commit}}"], repo)
    if rc != 0:
        return Distance(error=f"commit {sha} unknown to {repo}")
    rc, _ = _git_line(["rev-parse", "--verify", "-q", f"{compare_ref}^{{commit}}"], repo)
    if rc != 0:
        return Distance(error=f"ref {compare_ref} unknown to {repo}")
    rc, out, err = _git(["rev-list", "--left-right", "--count", f"{sha}...{compare_ref}"], repo)
    if rc != 0:
        return Distance(error=err or "rev-list failed")
    parts = out.split()
    if len(parts) != 2:
        return Distance(error=f"unexpected rev-list output: {out!r}")
    return Distance(ahead=int(parts[0]), behind=int(parts[1]))


def last_fetch_time(repo: Path | None) -> str | None:
    """ISO timestamp of the repo's last fetch, or None if never/unknown."""
    if repo is None:
        return None
    rc, git_dir = _git_line(["rev-parse", "--git-common-dir"], repo)
    if rc != 0:
        return None
    head = Path(git_dir)
    if not head.is_absolute():
        head = repo / head
    fetch_head = head / "FETCH_HEAD"
    if not fetch_head.exists():
        return None
    return datetime.fromtimestamp(fetch_head.stat().st_mtime, tz=UTC).isoformat()


# ── PEP 610 provenance ───────────────────────────────────────────────────


def site_packages_dirs(env_root: Path) -> list[Path]:
    return sorted(p for p in env_root.glob("lib/python*/site-packages") if p.is_dir())


def find_dist_info(env_root: Path, *, dist_name: str = DIST_NAME) -> Path | None:
    """Locate *our* ``*.dist-info`` inside an environment."""
    for site in site_packages_dirs(env_root):
        for candidate in sorted(site.glob("*.dist-info")):
            name = candidate.name.split("-", 1)[0]
            if name.lower().replace("-", "_") == dist_name:
                return candidate
    return None


def read_direct_url(dist_info: Path) -> tuple[Origin | None, str | None]:
    """Parse ``direct_url.json``. Returns ``(origin, error)``."""
    path = dist_info / "direct_url.json"
    if not path.exists():
        return None, f"{path} not found (installed from an index, not a direct URL?)"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return None, f"cannot read {path}: {exc}"

    url = data.get("url", "")
    vcs = data.get("vcs_info")
    if isinstance(vcs, dict):
        commit = vcs.get("commit_id", "")
        if not commit:
            return None, f"{path} has vcs_info without commit_id"
        return GitOrigin(
            url=url,
            commit_id=commit,
            requested_revision=vcs.get("requested_revision"),
        ), None

    dir_info = data.get("dir_info")
    if isinstance(dir_info, dict):
        local = url[len("file://") :] if url.startswith("file://") else url
        return LocalOrigin(path=Path(local), editable=bool(dir_info.get("editable"))), None

    return None, f"{path} has neither vcs_info nor dir_info"


def env_root_for(executable: Path) -> Path | None:
    """``<root>/bin/gits`` -> ``<root>``, following symlinks first."""
    try:
        real = executable.resolve()
    except OSError:
        return None
    if real.parent.name != "bin":
        return None
    return real.parent.parent


# ── uv tool receipts ─────────────────────────────────────────────────────


def read_uv_receipts(uv_tools_dir: Path) -> dict[str, str]:
    """Map installed entrypoint path -> requirement string, from uv receipts.

    The requirement is what a plain ``uv tool install --reinstall`` would
    re-resolve. A *local directory* requirement is the shape that packed
    another ticket's untracked source into the live tool on 2026-08-01.
    """
    out: dict[str, str] = {}
    if not uv_tools_dir.is_dir():
        return out
    for receipt in sorted(uv_tools_dir.glob("*/uv-receipt.toml")):
        try:
            data = tomllib.loads(receipt.read_text())
        except (OSError, ValueError):
            continue
        tool = data.get("tool") or {}
        reqs = []
        for req in tool.get("requirements") or []:
            if isinstance(req, str):
                reqs.append(req)
            elif isinstance(req, dict):
                name = req.get("name", "?")
                if req.get("git"):
                    reqs.append(f"git+{req['git']}")
                elif req.get("directory") or req.get("path"):
                    reqs.append(f"dir:{req.get('directory') or req.get('path')}")
                elif req.get("url"):
                    reqs.append(str(req["url"]))
                else:
                    reqs.append(name)
        requirement = ", ".join(reqs)
        for entry in tool.get("entrypoints") or []:
            path = entry.get("install-path")
            if path:
                out[str(path)] = requirement
    return out


def requirement_is_local_dir(requirement: str | None) -> bool:
    return bool(requirement) and requirement.startswith("dir:")


# ── config.env / Settings compatibility ──────────────────────────────────


def config_env_keys(path: Path) -> tuple[str, ...]:
    """Keys present in a ``config.env``, lowercased. Stdlib parse, no pydantic."""
    if not path.exists():
        return ()
    keys: list[str] = []
    try:
        text = path.read_text()
    except OSError:
        return ()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if key:
            keys.append(key.lower())
    return tuple(dict.fromkeys(keys))


def probe_settings_fields(
    python_exe: Path, *, timeout: float = PROBE_TIMEOUT
) -> tuple[tuple[str, ...] | None, str | None]:
    """Ask a deployment's own interpreter which ``Settings`` fields it declares.

    This *executes another deployment's code*, which is exactly the code most
    likely to be broken — so every failure mode (missing interpreter, import
    error, hang, garbage on stdout) becomes ``(None, reason)``. It never
    raises and never blocks past ``timeout``.
    """
    if not python_exe.exists():
        return None, f"interpreter not found: {python_exe}"
    try:
        proc = subprocess.run(  # noqa: S603
            [str(python_exe), "-c", _PROBE_CODE],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"probe timed out after {timeout:g}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"probe failed to run: {type(exc).__name__}: {exc}"

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit {proc.returncode}"
        return None, f"probe exited {proc.returncode}: {tail}"

    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        return None, "probe produced no output"
    try:
        fields = json.loads(lines[-1])
    except ValueError as exc:
        return None, f"probe output not JSON: {exc}"
    if not isinstance(fields, list):
        return None, "probe output was not a list of field names"
    return tuple(str(f).lower() for f in fields), None


def check_config_compat(
    python_exe: Path, keys: Sequence[str], *, timeout: float = PROBE_TIMEOUT
) -> ConfigCompat:
    """Would this deployment survive the keys currently in ``config.env``?

    ``Settings`` is ``extra='forbid'`` **and stays that way** — an undeclared
    key there makes *every* ``Settings()`` in that deployment raise. Rather
    than relaxing the validation, this reports which deployments have not
    caught up with the key set on disk.
    """
    declared, reason = probe_settings_fields(python_exe, timeout=timeout)
    if declared is None:
        return ConfigCompat(status="unknown", reason=reason)
    missing = tuple(k for k in keys if k not in declared)
    return ConfigCompat(
        status="missing" if missing else "ok",
        declared=declared,
        missing=missing,
    )


# ── discovery ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DeploymentRef:
    """A pointer at an executable, plus why we care about it."""

    role: str  # "cli" | "hook" | "bot"
    label: str
    executable: Path


ENTRYPOINT_NAMES = ("ghost", "gits")


def _hook_commands(settings_file: Path) -> list[str]:
    try:
        data = json.loads(settings_file.read_text())
    except (OSError, ValueError):
        return []
    commands: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            cmd = node.get("command")
            if isinstance(cmd, str):
                commands.append(cmd)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data.get("hooks"))
    return commands


def discover_hook_refs(settings_files: Iterable[Path]) -> list[DeploymentRef]:
    """Find the ``gits``/``ghost`` executables that hooks are wired to.

    A hook's binary is pinned by an absolute path in a settings file, not by
    anything in this repo — which is precisely why nobody could answer "what
    is the guard running" without looking here.
    """
    refs: list[DeploymentRef] = []
    for settings_file in settings_files:
        if not settings_file.exists():
            continue
        for command in _hook_commands(settings_file):
            try:
                tokens = shlex.split(command)
            except ValueError:
                tokens = command.split()
            if not tokens:
                continue
            exe = Path(tokens[0])
            if exe.name not in ENTRYPOINT_NAMES:
                continue
            refs.append(
                DeploymentRef(role="hook", label=f"{settings_file} ({' '.join(tokens[1:])})",
                              executable=exe)
            )
    return refs


def running_bot_executable() -> tuple[Path, str] | None:
    """The executable of the running bot process, via ``ps``. None on any doubt."""
    try:
        proc = subprocess.run(  # noqa: S603
            ["ps", "-Ao", "pid=,command="], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, command = line.partition(" ")
        try:
            tokens = shlex.split(command)
        except ValueError:
            continue
        if len(tokens) < 2 or tokens[1] != "start":
            continue
        exe = Path(tokens[0])
        if exe.name in ENTRYPOINT_NAMES:
            return exe, f"pid {pid}"
    return None


def default_settings_files(home: Path) -> list[Path]:
    """Every Claude account dir's settings file (this machine has four)."""
    files = sorted(home.glob(".claude*/settings.json"))
    files += sorted(home.glob(".claude*/settings.local.json"))
    return files


def discover_refs(
    *,
    home: Path | None = None,
    path_lookup: Callable[[str], str | None] = shutil.which,
    settings_files: Sequence[Path] | None = None,
    bot_lookup: Callable[[], tuple[Path, str] | None] | None = running_bot_executable,
) -> list[DeploymentRef]:
    """Locate every deployment that is live on this machine."""
    home = home or Path.home()
    refs: list[DeploymentRef] = []

    for name in ENTRYPOINT_NAMES:
        found = path_lookup(name)
        if found:
            refs.append(DeploymentRef(role="cli", label=f"PATH:{name}", executable=Path(found)))

    if settings_files is None:
        settings_files = default_settings_files(home)
    refs.extend(discover_hook_refs(settings_files))

    if bot_lookup is not None:
        bot = bot_lookup()
        if bot is not None:
            refs.append(DeploymentRef(role="bot", label=bot[1], executable=bot[0]))

    return refs


# ── report assembly ──────────────────────────────────────────────────────


def _default_master_branches() -> tuple[str, ...]:
    return ("master", "main")


def _finding_for_worktree(
    wt: WorktreeState, *, main_branches: Sequence[str] = ()
) -> list[Finding]:
    main_branches = main_branches or _default_master_branches()
    findings: list[Finding] = []
    if wt.branch is None:
        findings.append(
            Finding("error", "not-on-master", f"{wt.path}: detached HEAD at {wt.head_sha}")
        )
    elif wt.branch not in main_branches:
        findings.append(
            Finding("error", "not-on-master", f"{wt.path}: on branch {wt.branch!r}, not master")
        )
    if wt.dirty:
        findings.append(
            Finding(
                "error",
                "dirty-worktree",
                f"{wt.path}: {len(wt.modified)} modified, {len(wt.untracked)} untracked "
                f"— the sha does not describe what runs",
            )
        )
    if wt.untracked_sources:
        findings.append(
            Finding(
                "error",
                "untracked-sources",
                f"{wt.path}: untracked source files would be packed by a wheel build: "
                + ", ".join(wt.untracked_sources),
            )
        )
    return findings


def build_report(
    refs: Sequence[DeploymentRef],
    *,
    compare_ref: str = DEFAULT_COMPARE_REF,
    compare_repo: Path | None = None,
    config_env: Path | None = None,
    uv_tools_dir: Path | None = None,
    probe_timeout: float = PROBE_TIMEOUT,
    probe_config: bool = True,
    fetch: bool = False,
) -> Report:
    """Resolve every ref into a :class:`Deployment` and grade the result."""
    report = Report(compare_ref=compare_ref, config_env=config_env)

    receipts = read_uv_receipts(uv_tools_dir) if uv_tools_dir else {}
    if config_env is not None:
        report.config_keys = config_env_keys(config_env)

    # One Deployment per environment; a venv commonly serves several roles.
    by_env: dict[Path, Deployment] = {}
    for ref in refs:
        try:
            key = ref.executable.resolve()
        except OSError:
            key = ref.executable
        dep = by_env.get(key)
        if dep is None:
            dep = Deployment(executable=key)
            by_env[key] = dep
            report.deployments.append(dep)
        if ref.role not in dep.roles:
            dep.roles.append(ref.role)
        dep.labels.append(ref.label)
        requirement = receipts.get(str(ref.executable))
        if requirement and dep.receipt_requirement is None:
            dep.receipt_requirement = requirement

    for dep in report.deployments:
        _resolve_origin(dep)

    if compare_repo is None:
        compare_repo = _pick_compare_repo(report.deployments)
    report.compare_repo = compare_repo

    if fetch and compare_repo is not None:
        _git(["fetch", "--quiet", "origin"], compare_repo, timeout=60)
    report.fetched_at = last_fetch_time(compare_repo)

    if compare_repo is not None:
        rc, out = _git_line(["rev-parse", f"{compare_ref}^{{commit}}"], compare_repo)
        report.compare_sha = out if rc == 0 else None
    if report.compare_sha is None:
        report.findings.append(
            Finding(
                "unknown",
                "compare-ref-unresolved",
                f"cannot resolve {compare_ref}"
                + (f" in {compare_repo}" if compare_repo else " — no repository available"),
            )
        )

    for dep in report.deployments:
        dep.distance = measure_distance(compare_repo, dep.sha, compare_ref)
        _grade(dep, report, probe_config=probe_config, probe_timeout=probe_timeout)

    return report


def _resolve_origin(dep: Deployment) -> None:
    dep.env_root = env_root_for(dep.executable)
    if dep.env_root is None:
        dep.findings.append(
            Finding(
                "unknown",
                "env-unresolved",
                f"{dep.executable}: cannot locate the environment root",
            )
        )
        return
    dep.dist_info = find_dist_info(dep.env_root)
    if dep.dist_info is None:
        dep.findings.append(
            Finding(
                "unknown",
                "dist-info-missing",
                f"{dep.env_root}: no {DIST_NAME} *.dist-info — provenance unreadable",
            )
        )
        return
    origin, error = read_direct_url(dep.dist_info)
    dep.origin = origin
    if error:
        dep.findings.append(Finding("unknown", "provenance-unreadable", error))
    if isinstance(origin, LocalOrigin):
        dep.worktree = inspect_worktree(origin.path)
        if dep.worktree is None:
            dep.findings.append(
                Finding(
                    "unknown",
                    "not-a-checkout",
                    f"{origin.path}: not a git checkout — cannot say which code this is",
                )
            )


def _pick_compare_repo(deployments: Sequence[Deployment]) -> Path | None:
    """Prefer a local checkout we already found; fall back to this source tree."""
    for dep in deployments:
        if dep.worktree is not None:
            return dep.worktree.path
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return None


def _grade(
    dep: Deployment, report: Report, *, probe_config: bool, probe_timeout: float
) -> None:
    if dep.worktree is not None:
        dep.findings.extend(_finding_for_worktree(dep.worktree))

    if requirement_is_local_dir(dep.receipt_requirement):
        dep.findings.append(
            Finding(
                "error",
                "local-requirement",
                f"{dep.executable}: uv receipt requirement is "
                f"{dep.receipt_requirement} — a reinstall would pack whatever that "
                f"working tree currently holds",
            )
        )

    dist = dep.distance or Distance()
    if dist.error:
        dep.findings.append(
            Finding("unknown", "distance-unknown", f"{dep.executable}: {dist.error}")
        )
    elif dist.behind:
        dep.findings.append(
            Finding(
                "error",
                "behind-master",
                f"{dep.executable}: {dist.behind} commit(s) behind {report.compare_ref}"
                + (f", {dist.ahead} ahead" if dist.ahead else ""),
            )
        )
    elif dist.ahead:
        dep.findings.append(
            Finding(
                "warn",
                "ahead-of-master",
                f"{dep.executable}: {dist.ahead} commit(s) ahead of {report.compare_ref}",
            )
        )

    if not probe_config or not report.config_keys or dep.env_root is None:
        return
    python_exe = dep.env_root / "bin" / "python"
    dep.config = check_config_compat(python_exe, report.config_keys, timeout=probe_timeout)
    if dep.config.status == "missing":
        dep.findings.append(
            Finding(
                "error",
                "config-key-unknown",
                f"{dep.executable}: does not declare "
                + ", ".join(dep.config.missing)
                + f" (present in {report.config_env}) — Settings() raises there, "
                "because extra='forbid'",
            )
        )
    elif dep.config.status == "unknown":
        dep.findings.append(
            Finding(
                "unknown",
                "config-compat-unknown",
                f"{dep.executable}: cannot tell whether it declares the "
                f"{len(report.config_keys)} key(s) in {report.config_env}: "
                f"{dep.config.reason}",
            )
        )


def collect_report(
    *,
    home: Path | None = None,
    compare_ref: str = DEFAULT_COMPARE_REF,
    compare_repo: Path | None = None,
    config_env: Path | None = None,
    uv_tools_dir: Path | None = None,
    fetch: bool = False,
    probe_config: bool = True,
    probe_timeout: float = PROBE_TIMEOUT,
    **discover_kwargs: object,
) -> Report:
    """Discover + resolve in one call (what the CLI uses)."""
    home = home or Path.home()
    if config_env is None:
        config_env = home / ".gits" / "config.env"
    if uv_tools_dir is None:
        uv_tools_dir = Path(
            os.environ.get("UV_TOOL_DIR") or home / ".local" / "share" / "uv" / "tools"
        )
    refs = discover_refs(home=home, **discover_kwargs)  # type: ignore[arg-type]
    return build_report(
        refs,
        compare_ref=compare_ref,
        compare_repo=compare_repo,
        config_env=config_env,
        uv_tools_dir=uv_tools_dir,
        fetch=fetch,
        probe_config=probe_config,
        probe_timeout=probe_timeout,
    )


# ── preinstall check ─────────────────────────────────────────────────────


@dataclass
class PreinstallCheck:
    """Verdict on a working tree about to be packed into a wheel.

    Honest about what this is: a check you have to *remember to run* fails the
    same way that remembering to reinstall fails. It does not prevent a dirty
    install; it only makes the state answerable at the moment you ask.
    """

    path: Path
    worktree: WorktreeState | None
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


def check_preinstall(path: Path) -> PreinstallCheck:
    """Would installing from ``path`` pack uncommitted or untracked code?"""
    wt = inspect_worktree(path)
    if wt is None:
        return PreinstallCheck(
            path=path,
            worktree=None,
            findings=[
                Finding(
                    "unknown",
                    "not-a-checkout",
                    f"{path}: not a git checkout — cannot say what an install would pack",
                )
            ],
        )
    findings: list[Finding] = []
    if wt.untracked_sources:
        findings.append(
            Finding(
                "error",
                "untracked-sources",
                f"{path}: {len(wt.untracked_sources)} untracked source file(s) would be "
                "packed into the wheel: " + ", ".join(wt.untracked_sources),
            )
        )
    if wt.modified:
        findings.append(
            Finding(
                "error",
                "dirty-worktree",
                f"{path}: {len(wt.modified)} uncommitted change(s) would be packed: "
                + ", ".join(wt.modified[:10]),
            )
        )
    if wt.branch is None:
        findings.append(Finding("warn", "not-on-master", f"{path}: detached HEAD"))
    elif wt.branch not in _default_master_branches():
        findings.append(
            Finding("warn", "not-on-master", f"{path}: on branch {wt.branch!r}, not master")
        )
    return PreinstallCheck(path=path, worktree=wt, findings=findings)
