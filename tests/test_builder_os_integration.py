"""tv6q3n — isolated ghost ⇄ builder-os full-lifecycle integration (codex gate #8).

The automated form of the merge gate for ``builder-os-rollout-v2 → master``. It
drives the **real** ghost builder chain (registry → event monitor → renderer →
response adapter → engine disposer) against the **real** builder-os CLI on a
throwaway git repo — fake Discord transport only, no bot, headless.

See ``tests/builder_integration/harness.py`` for the three actors. Each test
builds its own hermetic env (tmp checkout + tmp clone + offline provider seams),
so scenarios are isolated and order-independent.
"""

from __future__ import annotations

import os

import pytest

from gits.core.builder_response import RespondOutcome
from tests.builder_integration.harness import (
    BOS_PYTHON,
    FakeTransportAdapter,
    ScriptedDriver,
    make_bos_env,
    make_engine,
    make_settings,
    pump,
    register_ticket,
    write_humans,
)

# --------------------------------------------------------------------------- #
# Merge-gate posture: this gate MUST run. A silent skip on a mis-provisioned CI
# reads as a false pass (the T9 AC17 false-green class), so the DEFAULT when the
# builder-os toolchain is missing is to **fail loud**, never skip. A local dev
# without builder-os can opt into skipping explicitly with TV6Q3N_ALLOW_SKIP=1;
# CI never sets it, so a mis-provisioned runner goes RED, never green.
# --------------------------------------------------------------------------- #
_ALLOW_SKIP = os.environ.get("TV6Q3N_ALLOW_SKIP") == "1"
GATE_SCENARIO_MIN = 11   # happy path + 10 fault scenarios (guard against silent loss)


@pytest.fixture(autouse=True)
def _require_builder_os_toolchain():
    if BOS_PYTHON.exists():
        return
    if _ALLOW_SKIP:
        pytest.skip(f"builder-os toolchain absent at {BOS_PYTHON} — TV6Q3N_ALLOW_SKIP dev opt-out")
    pytest.fail(
        f"tv6q3n MERGE GATE cannot run: builder-os toolchain not found at {BOS_PYTHON}. "
        "A skipped gate is a FALSE PASS — provision BOS_REPO/BOS_PYTHON to the pinned "
        "builder-os checkout (or set TV6Q3N_ALLOW_SKIP=1 for a local dev opt-out).",
        pytrace=False,
    )


def test_gate_scenario_count_is_complete():
    """Belt-and-suspenders against silent scenario loss: the gate must carry the
    happy path + all 10 fault scenarios. Catches an accidental deletion that
    would otherwise shrink the gate without turning it red."""
    import tests.test_builder_os_integration as mod

    scenarios = [n for n in dir(mod)
                 if n.startswith("test_") and (n.startswith("test_s") or "happy" in n)]
    assert len(scenarios) >= GATE_SCENARIO_MIN, \
        f"expected ≥{GATE_SCENARIO_MIN} gate scenarios, found {len(scenarios)}: {sorted(scenarios)}"

MAPPED_USER = "555"      # present in builder_humans.json → "liangchen"
UNMAPPED_USER = "999"    # absent → fail-closed
ACTOR = "liangchen"      # a real human_builder in the pinned contract profile
THREAD = "thread-1"
ASSIST = "assist-1"


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A fresh hermetic env + scripted driver + real headless engine, ready to run."""
    env = make_bos_env(tmp_path)
    env.activate(monkeypatch)
    drv = ScriptedDriver(env)
    settings = make_settings(env, tmp_path)
    adapter = FakeTransportAdapter()
    engine = make_engine(settings, adapter)
    write_humans(settings, {MAPPED_USER: ACTOR})
    return env, drv, settings, adapter, engine


# --------------------------------------------------------------------------- #
# Happy path — AC#1
# --------------------------------------------------------------------------- #
async def test_happy_path_real_merge_and_cleanup(wired):
    env, drv, settings, adapter, engine = wired

    did = drv.drive_to_ready()
    await register_ticket(engine, env, drv.session_id, channel_id=THREAD,
                          assistant_channel_id=ASSIST)
    await pump(engine, 2)

    # the ready-for-human completion card rendered, pinned, and mirrored
    assert adapter.cards(), "no card rendered for ready_for_human"
    assert any(c == THREAD for c, _ in adapter.pinned), "ready card not pinned in thread"
    assert any(s.channel_id == ASSIST for s in adapter.cards()), "ready card not mirrored"

    # synthesise the human clicking the 'Merge' disposition button
    cb = adapter.find_cb(kind="disp", choice="merge")
    assert cb == f"bos|disp|{env.uid}|{did}|merge", adapter.callbacks()
    handled = await engine.builder_response.handle_click(THREAD, MAPPED_USER, cb)
    assert handled is True

    # the engine drove dispose → cleanup → teardown → unregister on the REAL chain
    assert "driver.disposed" in env.event_types()
    assert "driver.terminated" in env.event_types()

    # AC#1: a REAL local merge landed the candidate work on the throwaway clone
    log = env.clone_log()
    assert "merge bos/4 (disposition)" in log, log
    assert "candidate work" in log, log
    assert "bos/4" not in env.clone_branches(), "ticket branch not deleted by cleanup"

    # registry unregistered (ghost-side teardown)
    assert engine.builder_registry.get(env.uid) is None


# --------------------------------------------------------------------------- #
# Scenario 1 — crash after admit before ghost registry write reuses the token (B2)
# --------------------------------------------------------------------------- #
async def test_s1_admit_idempotent_capability_reuse(wired):
    env, drv, settings, adapter, engine = wired
    spec1 = drv.admit()
    h1 = env.capability_hash_file.read_text().strip()
    # "crash before the ghost registry write" == the ticket was never registered.
    # A retry re-admits with the SAME ghost-minted token (B2 start token txn).
    spec2 = drv.admit()
    h2 = env.capability_hash_file.read_text().strip()
    assert h1 == h2, "capability hash changed on retry — token not reused (B2)"
    assert spec1["capability_sha256"] == h1 == spec2["capability_sha256"]
    # no permanent unauthorized state: the reused token still authorises respond
    # (proven by the token round-trip in the happy path); here we assert the
    # authority record is intact and single.


# --------------------------------------------------------------------------- #
# Scenario 2 — replay start (idempotent re-admit) binds, no double-launch (B3)
# --------------------------------------------------------------------------- #
async def test_s2_replay_binds_existing_no_double_launch(wired):
    env, drv, settings, adapter, engine = wired
    spec1 = drv.admit()
    spec2 = drv.admit()
    assert spec2["replay"] is True, "re-admit did not signal replay (bind, don't launch)"
    assert spec2["driver_session_id"] == spec1["driver_session_id"]
    assert env.event_types().count("driver.started") == 1, "double-launch (>1 started)"


# --------------------------------------------------------------------------- #
# Scenario 3 — close_without_merge disposition + cleanup releases + unregisters
# --------------------------------------------------------------------------- #
async def test_s3_close_without_merge_releases_and_unregisters(wired):
    env, drv, settings, adapter, engine = wired
    drv.drive_to_ready()
    await register_ticket(engine, env, drv.session_id, channel_id=THREAD,
                          assistant_channel_id=ASSIST)
    await pump(engine, 2)

    cb = adapter.find_cb(kind="disp", choice="close_without_merge")
    assert cb, adapter.callbacks()
    assert await engine.builder_response.handle_click(THREAD, MAPPED_USER, cb) is True

    types = env.event_types()
    assert "driver.disposed" in types and "driver.terminated" in types
    # closed, not merged: the candidate work never reaches the clone's default branch
    assert "merge bos/4" not in env.clone_log()
    assert "candidate work" not in env.clone_log()
    # lease/worktree released + registry unregistered. The (unmerged) ticket
    # branch is KEPT by design on close_without_merge (M3 — only a merged branch
    # is deleted at cleanup); the disposable worktree is still torn down.
    assert not env.worktree.exists(), "worktree not torn down by cleanup"
    assert "bos/4" in env.clone_branches(), "close_without_merge must keep the branch"
    assert engine.builder_registry.get(env.uid) is None


# --------------------------------------------------------------------------- #
# Scenario 4 — transcript resume carries --resume <sid>, not a fresh brief (B6)
# --------------------------------------------------------------------------- #
async def test_s4_resume_carries_resume_flag(wired):
    env, drv, settings, adapter, engine = wired
    drv.admit()
    rc, out, err = drv.record_session("clisess-xyz", "@1")
    assert rc == 0, err
    # ghost's launcher parses the real resume LaunchSpec (fenced-confirmed by default)
    spec = await engine.builder_launcher.resume(env.uid)
    assert "--resume" in spec.cli_args, spec.cli_args
    assert "clisess-xyz" in spec.cli_args, spec.cli_args
    # B6: a surviving transcript is resumed in-place — no fresh brief is handed back.
    assert spec.initial_prompt is None, "resume onto a live transcript must not re-brief"


# --------------------------------------------------------------------------- #
# Scenario 7 — requester auth: admit local_operator=False (B4) + ghost fail-closed
# --------------------------------------------------------------------------- #
async def test_s7_requester_admission_and_failclosed_unmapped(wired):
    env, drv, settings, adapter, engine = wired
    drv.admit(requester=ACTOR)
    # B4: a named --requester flips local_operator off and admission validates the
    # requester against the pinned contract's human_builders (requester_authorized).
    preds = {p["name"]: p for p in env.read_admission()["predicate_results"]}
    assert preds["requester_authorized"]["passed"] is True
    assert ACTOR in preds["requester_authorized"]["detail"]

    cref = drv.submit_candidate(drv.commit_candidate())
    drv.ingest_review("approved", cref)
    rc, out, err = drv.declare_ready()
    assert rc == 0, err
    did = drv.disposition_decision_id()
    await register_ticket(engine, env, drv.session_id, channel_id=THREAD,
                          assistant_channel_id=ASSIST)
    await pump(engine, 2)

    # an UNMAPPED Discord id is refused fail-closed: nothing recorded, no dispose
    outcome = await engine.builder_response.respond(THREAD, UNMAPPED_USER, env.uid, did, "merge")
    assert outcome == RespondOutcome.UNMAPPED
    assert "driver.disposed" not in env.event_types()
    assert "merge bos/4" not in env.clone_log()
    assert engine.builder_registry.get(env.uid) is not None


# --------------------------------------------------------------------------- #
# Scenario 7b — negative auth: a wrong capability token is rejected (tamper)
# --------------------------------------------------------------------------- #
async def test_s7b_wrong_capability_token_rejected(wired):
    env, drv, settings, adapter, engine = wired
    did = drv.drive_to_ready()
    # ghost holds a WRONG token (mint mismatch / tamper). The actor gate passes
    # (mapped id) so this isolates the capability-token check: builder-os compares
    # sha256(token) to the hash frozen at admit and rejects the mismatch (exit 8).
    await register_ticket(engine, env, drv.session_id, channel_id=THREAD,
                          assistant_channel_id=ASSIST, capability_token="WRONG-CAP-TOKEN")
    await pump(engine, 2)

    outcome = await engine.builder_response.respond(THREAD, MAPPED_USER, env.uid, did, "merge")
    assert outcome == RespondOutcome.UNAUTHORIZED
    # nothing recorded, nothing disposed, no merge — and the ticket survives
    assert "driver.disposed" not in env.event_types()
    assert "merge bos/4" not in env.clone_log()
    assert engine.builder_registry.get(env.uid) is not None
    # the correct token, by contrast, authorises (proven green by the happy path):
    # so the hash match is load-bearing here, not assumed.


# --------------------------------------------------------------------------- #
# Scenario 8 — resume fencing: refuse unless the prior window is confirmed absent (B5)
# --------------------------------------------------------------------------- #
async def test_s8_resume_refuses_without_fencing(wired):
    env, drv, settings, adapter, engine = wired
    drv.admit()
    rc, out, err = drv.resume(fenced_confirmed=False)
    assert rc != 0, "resume without --fenced-confirmed must refuse"
    assert "fenc" in (err + out).lower(), (rc, out, err)
    # ghost's launcher (fenced-confirmed by default) gets past the fencing gate
    spec = await engine.builder_launcher.resume(env.uid)
    assert spec.ticket_uid == env.uid


# --------------------------------------------------------------------------- #
# Scenario 5 — mirror failure: primary persists, respond works, mirror repairs (B7)
# --------------------------------------------------------------------------- #
async def test_s5_mirror_failure_primary_persists_and_repairs(tmp_path, monkeypatch):
    env = make_bos_env(tmp_path)
    env.activate(monkeypatch)
    drv = ScriptedDriver(env)
    settings = make_settings(env, tmp_path)
    adapter = FakeTransportAdapter(fail_channels={ASSIST})   # Assistant mirror down
    engine = make_engine(settings, adapter)
    write_humans(settings, {MAPPED_USER: ACTOR})

    drv.admit()
    did = drv.escalate("Ship it?", "authority is human-owned",
                       [("approve", "Approve"), ("deny", "Deny")])
    await register_ticket(engine, env, drv.session_id, channel_id=THREAD,
                          assistant_channel_id=ASSIST)
    await pump(engine, 2)

    # primary thread delivery committed the decision record even though the mirror
    # send failed — respond, resume-token and suppression all key off this record.
    assert engine.builder_renderer.first_open_decision(env.uid) == did
    rec = engine.builder_renderer.decision_record(env.uid, did)
    assert rec is not None and rec["mirror_pending"] is True
    assert not any(s.channel_id == ASSIST for s in adapter.cards()), "mirror wrongly delivered"

    # the authenticated respond finds the open decision off the primary projection
    outcome = await engine.builder_response.respond(THREAD, MAPPED_USER, env.uid, did, "approve")
    assert outcome == RespondOutcome.RECORDED

    # the mirror repairs independently once the Assistant channel recovers (B7)
    adapter.fail_channels.clear()
    await engine.builder_renderer.repair_pending_mirrors(env.uid)
    assert any(s.channel_id == ASSIST for s in adapter.cards()), "mirror never repaired"
    assert engine.builder_renderer.decision_record(env.uid, did)["mirror_pending"] is False


# --------------------------------------------------------------------------- #
# Scenario 6 — registry loss/corruption → explicit builder-global fault
# --------------------------------------------------------------------------- #
async def test_s6_registry_corruption_surfaces_global_fault(wired):
    env, drv, settings, adapter, engine = wired
    drv.admit()
    await register_ticket(engine, env, drv.session_id)

    faults: list[str] = []

    async def _spy(detail: str) -> None:
        faults.append(detail)

    engine.builder_event_monitor.on_global_fault(_spy)

    # a present-but-corrupt registry must NOT read as "absent" (silent dormancy):
    settings.builder_tickets_file.write_text("{ not valid json ")
    assert engine.builder_registry.integrity_fault() is not None
    assert engine.builder_registry.list_tickets() == []  # read stays tolerant (no crash)

    await pump(engine, 1)
    assert faults, "corrupt registry did not surface a builder-global fault"
    assert env.uid.split(":")[0] in faults[0] or "corrupt" in faults[0].lower() \
        or "builder_tickets" in faults[0]


# --------------------------------------------------------------------------- #
# Scenario 9 — request_changes disposition (codex-mandated): stays registered, no cleanup
# --------------------------------------------------------------------------- #
async def test_s9_request_changes_keeps_ticket_no_cleanup(wired):
    env, drv, settings, adapter, engine = wired
    drv.drive_to_ready()
    await register_ticket(engine, env, drv.session_id, channel_id=THREAD,
                          assistant_channel_id=ASSIST)
    await pump(engine, 2)

    cb = adapter.find_cb(kind="disp", choice="request_changes")
    assert cb, adapter.callbacks()
    assert await engine.builder_response.handle_click(THREAD, MAPPED_USER, cb) is True

    types = env.event_types()
    # dispose reopened a CR round instead of disposing — never DISPOSED/terminated
    assert "review.changes_requested" in types
    assert "driver.disposed" not in types
    assert "driver.terminated" not in types
    assert "merge bos/4" not in env.clone_log()
    # ghost keeps the ticket registered + pane alive; it must NEVER call cleanup
    # (which would refuse from CR_REWORK)
    assert engine.builder_registry.get(env.uid) is not None
    assert env.read_state()["state"] == "CR_REWORK"
    # pane-alive (explicit): the terminal teardown never ran, so the driver pane
    # was not killed — the CR round can continue in it.
    engine.tmux.kill_window.assert_not_called()


# --------------------------------------------------------------------------- #
# Scenario 10 — crash between respond and dispose (codex-mandated): dup → completion
# --------------------------------------------------------------------------- #
async def test_s10_crash_between_respond_and_dispose_recovers(wired):
    env, drv, settings, adapter, engine = wired
    did = drv.drive_to_ready()
    await register_ticket(engine, env, drv.session_id, channel_id=THREAD,
                          assistant_channel_id=ASSIST)
    await pump(engine, 2)

    # the answer is durably recorded — then ghost crashes before dispose/cleanup.
    rc, out, err = drv.respond(did, "merge", actor=ACTOR, token=env.capability_token)
    assert rc == 0, err
    assert "driver.disposed" not in env.event_types(), "dispose ran before the simulated crash"

    # the human clicks again (duplicate). builder-os returns 'already decided' (rc 9),
    # but ghost's disposition is state-driven: a duplicate RESUMES the terminal
    # workflow to completion instead of dead-ending on "already decided".
    cb = adapter.find_cb(kind="disp", choice="merge")
    assert await engine.builder_response.handle_click(THREAD, MAPPED_USER, cb) is True

    types = env.event_types()
    assert "driver.disposed" in types and "driver.terminated" in types
    assert "merge bos/4 (disposition)" in env.clone_log(), env.clone_log()
    assert engine.builder_registry.get(env.uid) is None  # idempotent completion
