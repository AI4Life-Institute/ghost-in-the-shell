"""Unit tests for the Org tree: lint, resolver, owner-resolution, dispatch
guard (POC port — task or6t4n) + the onboard node-write / snapshot-splice /
commit path.

The conformance spec is the vault POC harness
``2026-05-31-or6t4n-org-schema-poc.py``; the negative tests below mirror its
8 injected breaks one-for-one. Every Discord/network call is mocked — no I/O
beyond the git tmp repo each test builds.
"""

from __future__ import annotations

import argparse
import copy
import os
import subprocess
import textwrap

import pytest
import yaml

from gits.butler import butler_cli, onboard, org


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

_META = textwrap.dedent(
    """\
    # acme org — hand-written header comment that MUST survive a snapshot regen.
    # second comment line.
    org: acme
    guild_id: "111"
    onboarding_category: "222"
    root: "Root"
    rules:
      dispatch: "parent -> descendant only"
    tree_snapshot:   # DERIVED — do not hand-edit
      - Root:
        - alice
        - bob:
          - carol
    """
)

_NODES = {
    "Root": {"alias": "Root", "kind": "federation-root", "chair": "boss", "reports_to": None},
    "alice": {"alias": "alice", "channel_id": "900", "human": "alice",
              "reports_to": "Root", "scope": ["Projects/Algo/"], "desc": ""},
    "bob": {"alias": "bob", "channel_id": "901", "human": "bob",
            "reports_to": "Root", "scope": ["Projects/Ghost/"], "desc": ""},
    "carol": {"alias": "carol", "channel_id": "902", "human": "carol",
              "reports_to": "bob", "scope": ["Projects/Ghost/sub/"], "desc": ""},
}


def _write_org(tmp_path) -> str:
    """Build a git repo with Org/acme/ and return the org dir path."""
    repo = tmp_path / "vault-main"
    org_dir = repo / "Org" / "acme"
    org_dir.mkdir(parents=True)
    (org_dir / "_meta.yaml").write_text(_META)
    for alias, n in _NODES.items():
        body = {k: v for k, v in n.items() if k != "__file"}
        (org_dir / f"{alias}.yaml").write_text(yaml.safe_dump(body, sort_keys=False))
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    return str(org_dir) + "/"


def _loaded():
    """In-memory (meta, nodes) with __file keys, like org.load_org."""
    meta = yaml.safe_load(_META)
    nodes = copy.deepcopy(_NODES)
    for a, n in nodes.items():
        n["__file"] = f"{a}.yaml"
    return meta, nodes


# ---------------------------------------------------------------------------
# lint: clean + the 8 POC negative tests
# ---------------------------------------------------------------------------


def test_lint_clean():
    meta, nodes = _loaded()
    assert org.lint(meta, nodes) == []


def _expect(mutate, substr):
    meta, nodes = _loaded()
    mutate(nodes)
    errs = org.lint(meta, nodes)
    assert any(substr in e for e in errs), f"expected {substr!r} in {errs}"


def test_neg_dup_channel_id():
    _expect(lambda t: t["alice"].__setitem__("channel_id", t["bob"]["channel_id"]), "shared by")


def test_neg_dangling_reports_to():
    _expect(lambda t: t["alice"].__setitem__("reports_to", "nobody"), "dangling")


def test_neg_second_root():
    _expect(lambda t: t["alice"].__setitem__("reports_to", None), "exactly 1 root")


def test_neg_cycle():
    _expect(lambda t: t["bob"].__setitem__("reports_to", "carol"), "cycle")


def test_neg_overlapping_scope():
    _expect(lambda t: t["alice"].__setitem__("scope", ["Projects/Ghost/"]), "claimed by both")


def test_neg_snapshot_drift():
    _expect(lambda t: t["alice"].__setitem__("reports_to", "bob"), "tree_snapshot out of sync")


def test_neg_missing_field():
    _expect(lambda t: t["alice"].pop("reports_to"), "missing required")


def test_neg_bad_alias_format_isolated():
    def m(t):
        t["bad name"] = {"alias": "bad name", "channel_id": "777", "human": "x",
                         "reports_to": "Root", "scope": [], "desc": "",
                         "__file": "bad name.yaml"}
    _expect(m, "not ^[a-z]")


def test_neg_filename_mismatch_isolated():
    def m(t):
        t["fresh"] = {"alias": "fresh", "channel_id": "778", "human": "x",
                      "reports_to": "Root", "scope": [], "desc": "",
                      "__file": "WRONG.yaml"}
    _expect(m, "filename != alias")


def test_neg_unknown_key():
    _expect(lambda t: t["alice"].__setitem__("bogus", 1), "unknown key")


# ---------------------------------------------------------------------------
# resolver / owner-resolution / dispatch guard
# ---------------------------------------------------------------------------


def test_resolve_channel():
    _, nodes = _loaded()
    assert org.resolve_channel(nodes, "alice") == "900"
    assert org.resolve_channel(nodes, "nope") is None


def test_resolve_owner_deepest_wins():
    _, nodes = _loaded()
    assert org.resolve_owner(nodes, "Projects/Ghost/sub/x.md") == "carol"
    assert org.resolve_owner(nodes, "Projects/Ghost/top.md") == "bob"
    assert org.resolve_owner(nodes, "Projects/Algo/z.md") == "alice"
    assert org.resolve_owner(nodes, "Projects/Unknown/v.md") is None  # UNASSIGNED


def test_dispatch_guard():
    _, nodes = _loaded()
    assert org.can_dispatch(nodes, "bob", "carol") is True      # descendant
    assert org.can_dispatch(nodes, "alice", "carol") is False   # lateral
    assert org.can_dispatch(nodes, "carol", "bob") is False     # upward
    assert org.can_dispatch(nodes, None, "carol") is True       # chair / main


# ---------------------------------------------------------------------------
# dir-level lint: tree_snapshot-must-be-last + scope-exists warning
# ---------------------------------------------------------------------------


def test_lint_org_dir_clean(tmp_path):
    od = _write_org(tmp_path)
    hard, warns = org.split_errors(org.lint_org_dir(od))
    assert hard == []
    # all scope folders are missing on disk -> warn-only, never hard
    assert all(w.startswith("warn:") for w in warns)


def test_lint_snapshot_not_last_key(tmp_path):
    od = _write_org(tmp_path)
    meta_path = os.path.join(od, "_meta.yaml")
    open(meta_path, "a").write("\nlegacy_category: \"333\"\n")  # key after snapshot
    hard, _ = org.split_errors(org.lint_org_dir(od))
    assert any("must be the LAST top-level key" in e for e in hard)


def test_meta_top_level_keys_order():
    keys = org.meta_top_level_keys(_META)
    assert keys[-1] == "tree_snapshot"
    assert keys[0] == "org"


# ---------------------------------------------------------------------------
# onboard internals: node rendering, snapshot splice, preflight
# ---------------------------------------------------------------------------


def test_render_node_yaml_round_trips_and_lints():
    meta, nodes = _loaded()
    node = onboard.build_node("dave", "903", "dave", "bob", ["Projects/Ghost/dave/"], "hi")
    text = onboard.render_node_yaml("acme", node)
    assert text.startswith("# dave — node in the acme org tree.")
    parsed = yaml.safe_load(text)
    assert parsed["alias"] == "dave" and parsed["reports_to"] == "bob"
    # adding it keeps the tree lint-clean (with regenerated snapshot)
    parsed["__file"] = "dave.yaml"
    trial = {**nodes, "dave": parsed}
    assert org.lint(org.with_snapshot(meta, trial), trial) == []


def test_splice_preserves_comments_and_regens(tmp_path):
    od = _write_org(tmp_path)
    meta, nodes = org.load_org(od)
    node = onboard.build_node("dave", "903", "dave", "bob", [], "")
    node["__file"] = "dave.yaml"
    trial = {**nodes, "dave": node}
    new_text = onboard.splice_meta_snapshot(org.read_meta_text(od), meta, trial)
    # hand-written header comments above tree_snapshot survive
    assert "MUST survive a snapshot regen" in new_text
    assert "second comment line" in new_text
    # inline comment on the tree_snapshot line survives
    assert "DERIVED — do not hand-edit" in new_text
    # the regenerated snapshot parses to the reconstruction including dave
    reparsed = yaml.safe_load(new_text)
    assert reparsed["tree_snapshot"] == org.render_tree(meta, trial)
    assert "dave" in str(reparsed["tree_snapshot"])
    # tree_snapshot is still the last key
    assert org.meta_top_level_keys(new_text)[-1] == "tree_snapshot"


def test_preflight_problems():
    meta, nodes = _loaded()
    # bad name
    _, probs, _ = onboard.preflight(meta, nodes, "Bad Name", "Root", [], None, "")
    assert any("invalid/reserved" in p for p in probs)
    # reserved
    _, probs, _ = onboard.preflight(meta, nodes, "main", "Root", [], None, "")
    assert any("invalid/reserved" in p for p in probs)
    # alias taken
    _, probs, _ = onboard.preflight(meta, nodes, "alice", "Root", [], None, "")
    assert any("already exists" in p for p in probs)
    # missing parent
    _, probs, _ = onboard.preflight(meta, nodes, "dave", "ghost", [], None, "")
    assert any("not in org" in p for p in probs)
    # no parent at all
    _, probs, _ = onboard.preflight(meta, nodes, "dave", None, [], None, "")
    assert any("no --reports-to" in p for p in probs)
    # clean
    _, probs, hard = onboard.preflight(meta, nodes, "dave", "bob", ["Projects/Ghost/dave/"], None, "ok")
    assert probs == [] and hard == []


def test_preflight_overlapping_scope_blocks():
    meta, nodes = _loaded()
    _, probs, hard = onboard.preflight(meta, nodes, "dave", "bob", ["Projects/Ghost/"], None, "")
    assert probs == []
    assert any("claimed by both" in e for e in hard)


# ---------------------------------------------------------------------------
# onboard end-to-end (Discord + bind mocked; real git tmp repo)
# ---------------------------------------------------------------------------


def _args(**kw):
    base = dict(name="dave", org=None, reports_to="bob", scope=None, human=None,
                cli="claude", desc="", dry_run=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_onboard_end_to_end(tmp_path, monkeypatch):
    od = _write_org(tmp_path)
    repo = os.path.dirname(os.path.dirname(od.rstrip("/")))  # .../vault-main

    monkeypatch.setattr(onboard, "_find_channel", lambda *a, **k: None)
    monkeypatch.setattr(onboard, "_create_channel", lambda *a, **k: "955")
    monkeypatch.setattr(onboard, "_await_reaction", lambda *a, **k: True)
    sent = {}
    monkeypatch.setattr(butler_cli, "send_decorated",
                        lambda cid, content, **k: sent.setdefault("c", content) or "msg1")
    # worktrees land in tmp, not the user's real ~/src
    monkeypatch.setenv("GITS_WORKTREE_PARENT", str(tmp_path / "wts"))
    (tmp_path / "wts").mkdir()
    monkeypatch.chdir(repo)

    onboard.cmd_onboard(_args(scope=["Projects/Ghost/dave/"], desc="d"))

    # node file written + lint clean
    node_path = os.path.join(od, "dave.yaml")
    assert os.path.exists(node_path)
    hard, _ = org.split_errors(org.lint_org_dir(od))
    assert hard == []
    # snapshot regenerated to include dave; comments survived
    meta_text = org.read_meta_text(od)
    assert "MUST survive a snapshot regen" in meta_text
    assert "dave" in str(yaml.safe_load(meta_text)["tree_snapshot"])
    # committed
    log = subprocess.run(["git", "-C", repo, "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert "onboard dave" in log
    # worktree + binding
    wt = str(tmp_path / "wts" / "vault-dave")
    assert os.path.isdir(wt)
    assert os.path.exists(os.path.join(wt, ".butler.json"))
    # NO push: no origin remote was configured, and none was added
    remotes = subprocess.run(["git", "-C", repo, "remote"],
                             capture_output=True, text=True).stdout
    assert remotes.strip() == ""
    assert sent["c"].startswith("/bind ")


def test_onboard_idempotent_already_onboarded(tmp_path, monkeypatch, capsys):
    od = _write_org(tmp_path)
    repo = os.path.dirname(os.path.dirname(od.rstrip("/")))
    monkeypatch.chdir(repo)
    # alice already exists with reports_to Root
    onboard.cmd_onboard(_args(name="alice", reports_to="Root", dry_run=False))
    out = capsys.readouterr().out
    assert "already onboarded" in out


def test_onboard_clobber_refused(tmp_path, monkeypatch):
    od = _write_org(tmp_path)
    repo = os.path.dirname(os.path.dirname(od.rstrip("/")))
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit) as ei:
        onboard.cmd_onboard(_args(name="alice", reports_to="bob"))  # alice.reports_to=Root
    assert "refusing to clobber" in str(ei.value)
