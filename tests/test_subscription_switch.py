"""Tests for SwitchPrimitive — the atomic credential swap operation."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gits.core.session import SessionBinding
from gits.core.subscription import (
    AddResult,
    SubscriptionVault,
    SubscriptionVaultError,
    SwitchAborted,
    SwitchPrimitive,
    SwitchResult,
)

CRED_A = {"claudeAiOauth": {"accessToken": "tok-A", "refreshToken": "rt-A", "expiresAt": 1, "scopes": [], "subscriptionType": "max", "rateLimitTier": "standard"}}
CRED_B = {"claudeAiOauth": {"accessToken": "tok-B", "refreshToken": "rt-B", "expiresAt": 2, "scopes": [], "subscriptionType": "max", "rateLimitTier": "standard"}}


@pytest.fixture
def vault_dir(tmp_path):
    return tmp_path / "subscriptions"


@pytest.fixture
def claude_cred(tmp_path):
    p = tmp_path / "claude" / ".credentials.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(CRED_A))
    p.chmod(0o600)
    return p


@pytest.fixture
def vault(vault_dir, claude_cred):
    v = SubscriptionVault(vault_dir, claude_credentials_path=claude_cred)
    asyncio.run(v.add("alice"))
    # add a second subscription
    claude_cred.write_text(json.dumps(CRED_B))
    asyncio.run(v.add("bob"))
    # restore alice as active credentials
    v.restore_to_active_path("alice")
    return v


@pytest.fixture
def lock_path(tmp_path):
    return tmp_path / ".switch.lock"


@pytest.fixture(autouse=True)
def _no_keychain(monkeypatch):
    """All tests in this file run as if keychain doesn't exist (file-only).

    The real macOS keychain isn't a fixture-owned resource; mocking these out
    keeps tests hermetic and platform-independent.
    """
    monkeypatch.setattr("gits.core.subscription._read_keychain", lambda: None)
    monkeypatch.setattr("gits.core.subscription._write_keychain", lambda payload: True)
    monkeypatch.setattr("gits.core.subscription._delete_keychain", lambda: True)


@pytest.fixture
def fake_tmux():
    t = MagicMock()
    t.send_keys = AsyncMock()
    t.send_text = AsyncMock()
    t.pane_pid = AsyncMock(return_value=None)  # no real claude processes
    t.window_exists = AsyncMock(return_value=True)
    return t


@pytest.fixture
def fake_session_mgr():
    m = MagicMock()
    bindings = [
        SessionBinding(
            platform="discord",
            channel_id="ch-1",
            window_id="@1",
            window_name="w1",
            work_dir="/tmp/p1",
            coding_cli="claude",
            cli_session_id="sess-1",
        ),
        SessionBinding(
            platform="discord",
            channel_id="ch-2",
            window_id="@2",
            window_name="w2",
            work_dir="/tmp/p2",
            coding_cli="claude",
            cli_session_id="sess-2",
        ),
    ]
    m.list_bindings = MagicMock(return_value=bindings)
    return m


@pytest.fixture
def fake_launcher():
    ln = MagicMock()
    ln.build_launch_command = MagicMock(
        side_effect=lambda cli, sid, **kw: f"{cli} --resume {sid}"
    )
    return ln


@pytest.fixture
def primitive(fake_tmux, fake_session_mgr, fake_launcher, vault, lock_path):
    return SwitchPrimitive(
        tmux=fake_tmux,
        session_mgr=fake_session_mgr,
        launcher=fake_launcher,
        vault=vault,
        lock_path=lock_path,
    )


class TestSwitchPrimitive:
    def test_switch_swaps_credentials(self, primitive, vault, claude_cred):
        # alice is active; switch to bob
        result = asyncio.run(primitive.switch_to("bob", reason="manual"))
        assert isinstance(result, SwitchResult)
        assert result.target == "bob"
        assert result.previous == "alice"
        # credentials file should now contain bob's tokens
        with open(claude_cred) as f:
            data = json.load(f)
        assert data == CRED_B
        # manifest active updated
        m = vault.load()
        assert m.active == "bob"
        assert m.last_switch.from_name == "alice"
        assert m.last_switch.to_name == "bob"

    def test_writeback_captures_oauth_refresh(self, primitive, vault, claude_cred):
        """Simulate OAuth refresh on alice — credentials.json mutates while alice active."""
        refreshed = json.loads(claude_cred.read_text())
        refreshed["claudeAiOauth"]["accessToken"] = "tok-A-refreshed"
        claude_cred.write_text(json.dumps(refreshed))

        asyncio.run(primitive.switch_to("bob"))

        # alice's vault entry should now contain the refreshed token
        with open(vault.credentials_path("alice")) as f:
            stored = json.load(f)
        assert stored["claudeAiOauth"]["accessToken"] == "tok-A-refreshed"

    def test_no_op_when_target_already_active(self, primitive, vault):
        result = asyncio.run(primitive.switch_to("alice"))
        assert result.target == "alice"
        assert result.previous == "alice"
        assert result.bindings_respawned == []  # nothing happened

    def test_unknown_target_raises(self, primitive):
        with pytest.raises(SubscriptionVaultError, match="not found"):
            asyncio.run(primitive.switch_to("nobody"))

    def test_respawn_uses_resume_command(
        self, primitive, fake_tmux, fake_launcher
    ):
        asyncio.run(primitive.switch_to("bob"))
        # send_text called once per binding with the resume command
        assert fake_tmux.send_text.await_count == 2
        # First binding gets sess-1 resume
        first_call = fake_tmux.send_text.await_args_list[0]
        assert "claude --resume sess-1" in first_call.args[1]
        second_call = fake_tmux.send_text.await_args_list[1]
        assert "claude --resume sess-2" in second_call.args[1]

    def test_respawn_failure_marked_but_swap_persists(
        self, primitive, fake_tmux, vault, claude_cred
    ):
        # Make second binding's send_text fail
        async def side_effect(window_id, text, **kw):
            if window_id == "@2":
                raise RuntimeError("tmux gone")

        fake_tmux.send_text = AsyncMock(side_effect=side_effect)

        result = asyncio.run(primitive.switch_to("bob"))
        assert "ch-1" in result.bindings_respawned
        assert "ch-2" in result.bindings_failed
        assert not result.fully_succeeded

        # credentials.json was still swapped to bob
        with open(claude_cred) as f:
            data = json.load(f)
        assert data == CRED_B

    def test_kill_failure_aborts_swap(
        self, primitive, fake_tmux, vault, claude_cred
    ):
        # Inject a fake pane_pid + non-killable pids by mocking the helpers
        fake_tmux.pane_pid = AsyncMock(return_value=12345)

        with patch("gits.core.subscription.find_claude_children") as fcc:
            with patch("gits.core.subscription.kill_claude_process") as kcp:
                async def fcc_impl(pid):
                    return [99001]

                async def kcp_impl(pids, **kw):
                    return {p: False for p in pids}  # all stuck

                fcc.side_effect = fcc_impl
                kcp.side_effect = kcp_impl

                with pytest.raises(SwitchAborted):
                    asyncio.run(primitive.switch_to("bob"))

        # credentials.json must NOT have been swapped
        with open(claude_cred) as f:
            data = json.load(f)
        assert data == CRED_A
        # manifest.active still alice
        assert vault.load().active == "alice"

class TestAddSubscription:
    @pytest.fixture
    def empty_vault(self, vault_dir, claude_cred):
        # Vault with no subscriptions yet, but live credentials present
        return SubscriptionVault(vault_dir, claude_credentials_path=claude_cred)

    @pytest.fixture
    def empty_primitive(
        self, fake_tmux, fake_session_mgr, fake_launcher, empty_vault, lock_path
    ):
        return SwitchPrimitive(
            tmux=fake_tmux,
            session_mgr=fake_session_mgr,
            launcher=fake_launcher,
            vault=empty_vault,
            lock_path=lock_path,
        )

    def test_first_add_via_oauth(self, empty_primitive, empty_vault):
        """Default add: always runs OAuth login, even if creds already present."""
        async def hook():
            return True  # login "succeeded"; live creds were CRED_A all along

        result = asyncio.run(empty_primitive.add_subscription("work", on_login=hook))
        assert isinstance(result, AddResult)
        assert result.succeeded is True
        assert result.became_active is True
        m = empty_vault.load()
        assert m.active == "work"
        assert m.get("work").subscription_type == "max"

    def test_default_add_runs_login_even_with_existing_creds(
        self, empty_primitive, empty_vault
    ):
        """Without --capture-current, add MUST run login regardless of creds."""
        result = asyncio.run(
            empty_primitive.add_subscription(
                "work", login_command=["false"]  # would exit non-zero
            )
        )
        # `false` exits 1 → login fails → AddResult.succeeded == False.
        assert result.succeeded is False
        assert "non-zero" in (result.error or "").lower()
        # Vault stayed empty
        assert empty_vault.load().get("work") is None

    def test_capture_current_skips_login(self, empty_primitive, empty_vault):
        """--capture-current: existing creds → no OAuth, snapshot in place."""
        result = asyncio.run(
            empty_primitive.add_subscription(
                "work",
                capture_current=True,
                login_command=["false"],  # would fail if invoked
            )
        )
        assert result.succeeded is True
        assert result.became_active is True
        m = empty_vault.load()
        assert m.active == "work"
        assert m.get("work").subscription_type == "max"

    def test_capture_current_with_active_succeeds_when_diff_org(
        self, primitive, vault, claude_cred
    ):
        """--capture-current with active subscription succeeds when live creds belong to different org."""
        # alice is active; live creds were swapped to bob's already.
        # Pretend the user scp'd a third account's creds in:
        third = {"claudeAiOauth": {"accessToken": "tok-third", "refreshToken": "rt", "expiresAt": 5, "scopes": [], "subscriptionType": "max", "rateLimitTier": "standard"}}
        # Simulate this by writing to the live path before the call
        from unittest.mock import patch

        # Mock fetch_claude_identity to return a different orgId
        with patch(
            "gits.core.subscription.fetch_claude_identity",
            return_value={"email": "third@x.com", "orgId": "different-org", "orgName": "Third Co", "subscriptionType": "max"},
        ):
            claude_cred.write_text(json.dumps(third))
            result = asyncio.run(
                primitive.add_subscription("third", capture_current=True)
            )
        assert result.succeeded is True
        # alice still active — capture-current with existing active does NOT switch
        m = vault.load()
        assert m.active == "alice"
        assert m.get("third") is not None
        # Live creds restored to alice's
        live = json.loads(claude_cred.read_text())
        assert live == CRED_A

    def test_capture_current_rejects_when_org_matches_active(
        self, primitive, vault, claude_cred
    ):
        """If live creds belong to same Anthropic org as active, refuse to add a duplicate."""
        from unittest.mock import patch

        # alice is active with org_id=None (from fixture). Manually set it to known.
        m = vault.load()
        m.get("alice").org_id = "alice-org"
        asyncio.run(vault.save(m))

        with patch(
            "gits.core.subscription.fetch_claude_identity",
            return_value={"orgId": "alice-org"},
        ):
            with pytest.raises(SubscriptionVaultError, match="same Anthropic org"):
                asyncio.run(
                    primitive.add_subscription("dup", capture_current=True)
                )

    def test_capture_current_rejects_when_no_creds(
        self, empty_primitive, empty_vault, claude_cred
    ):
        claude_cred.unlink()
        with pytest.raises(SubscriptionVaultError, match="capture-current requires"):
            asyncio.run(
                empty_primitive.add_subscription("work", capture_current=True)
            )

    def test_add_with_no_existing_creds_runs_login(
        self, empty_primitive, empty_vault, claude_cred
    ):
        """If ~/.claude/.credentials.json is missing, login must actually run."""
        claude_cred.unlink()
        recorded = {"called": False}

        async def hook():
            recorded["called"] = True
            claude_cred.write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "tok-fresh",
                            "refreshToken": "rt",
                            "expiresAt": 5,
                            "scopes": [],
                            "subscriptionType": "max",
                            "rateLimitTier": "standard",
                        }
                    }
                )
            )
            return True

        result = asyncio.run(empty_primitive.add_subscription("work", on_login=hook))
        assert result.succeeded is True
        assert recorded["called"] is True

    def test_add_second_subscription_restores_active(
        self, primitive, vault, claude_cred, fake_tmux
    ):
        # alice is active; user adds 'home' which is a different subscription
        original = json.loads(claude_cred.read_text())

        async def fake_login():
            # Simulate: user logged in and claude wrote new credentials
            new_creds = {"claudeAiOauth": {**original["claudeAiOauth"], "accessToken": "tok-home"}}
            claude_cred.write_text(json.dumps(new_creds))
            return True

        result = asyncio.run(primitive.add_subscription("home", on_login=fake_login))
        assert result.succeeded is True
        assert result.became_active is False  # alice was already active

        # Vault now has 3 subscriptions
        m = vault.load()
        names = [s.name for s in m.subscriptions]
        assert set(names) == {"alice", "bob", "home"}
        assert m.active == "alice"  # unchanged

        # Live credentials restored to alice's
        live = json.loads(claude_cred.read_text())
        assert live == CRED_A
        # 'home' vault contains the login result
        with open(vault.credentials_path("home")) as f:
            assert json.load(f)["claudeAiOauth"]["accessToken"] == "tok-home"

    def test_add_login_failure_restores(self, primitive, vault, claude_cred):
        async def fake_login_fail():
            return False

        result = asyncio.run(primitive.add_subscription("home", on_login=fake_login_fail))
        assert result.succeeded is False
        assert "non-zero" in (result.error or "").lower()

        # Live credentials still alice's
        live = json.loads(claude_cred.read_text())
        assert live == CRED_A
        # No 'home' entry in vault
        m = vault.load()
        assert m.get("home") is None
        assert not vault.credentials_path("home").exists()

    def test_add_duplicate_rejected(self, primitive, vault):
        with pytest.raises(SubscriptionVaultError, match="already exists"):
            asyncio.run(primitive.add_subscription("alice", on_login=lambda: True))

    def test_add_respawns_even_when_vault_write_fails(
        self, primitive, vault, claude_cred, fake_tmux
    ):
        """If vault.add() raises mid-flight, bindings MUST still be respawned."""
        async def fake_login():
            new = {"claudeAiOauth": {"accessToken": "tok-x", "refreshToken": "rt", "expiresAt": 5, "scopes": [], "subscriptionType": "max", "rateLimitTier": "standard"}}
            claude_cred.write_text(json.dumps(new))
            return True

        # Force vault.add to fail
        from unittest.mock import patch

        async def boom(*args, **kw):
            raise RuntimeError("disk full")

        with patch.object(vault, "add", side_effect=boom):
            result = asyncio.run(
                primitive.add_subscription("home", on_login=fake_login)
            )

        # AddResult reports failure
        assert result.succeeded is False
        assert "vault" in (result.error or "").lower() or "disk" in (result.error or "").lower()
        # Live credentials restored to alice's
        assert json.loads(claude_cred.read_text()) == CRED_A
        # Bindings were respawned despite the vault failure
        assert "ch-1" in result.bindings_respawned
        assert "ch-2" in result.bindings_respawned

    def test_add_respawns_on_uncaught_exception(
        self, primitive, vault, claude_cred, fake_tmux
    ):
        """A KeyboardInterrupt / arbitrary exception MUST still trigger respawn."""
        async def bad_login():
            raise KeyboardInterrupt()

        with pytest.raises(KeyboardInterrupt):
            asyncio.run(primitive.add_subscription("home", on_login=bad_login))

        # send_text was called once per binding (respawn happened)
        respawn_calls = [
            c for c in fake_tmux.send_text.await_args_list
            if "claude --resume" in c.args[1]
        ]
        assert len(respawn_calls) >= 2

    def test_add_writeback_captures_refresh_before_login(
        self, primitive, vault, claude_cred
    ):
        # Simulate OAuth refresh: live creds for alice mutate before add starts
        refreshed = json.loads(claude_cred.read_text())
        refreshed["claudeAiOauth"]["accessToken"] = "tok-A-refreshed"
        claude_cred.write_text(json.dumps(refreshed))

        async def fake_login():
            new = {"claudeAiOauth": {"accessToken": "tok-home", "refreshToken": "rt-home", "expiresAt": 5, "scopes": [], "subscriptionType": "max", "rateLimitTier": "standard"}}
            claude_cred.write_text(json.dumps(new))
            return True

        asyncio.run(primitive.add_subscription("home", on_login=fake_login))

        with open(vault.credentials_path("alice")) as f:
            stored = json.load(f)
        assert stored["claudeAiOauth"]["accessToken"] == "tok-A-refreshed"


class TestRemoveSubscription:
    def test_remove_non_active(self, primitive, vault):
        asyncio.run(primitive.remove_subscription("bob"))
        m = vault.load()
        assert m.get("bob") is None
        assert not vault.credentials_path("bob").exists()
        assert m.active == "alice"

    def test_remove_active_rejected(self, primitive, vault):
        with pytest.raises(SubscriptionVaultError, match="Cannot remove active"):
            asyncio.run(primitive.remove_subscription("alice"))

    def test_remove_active_with_force(self, primitive, vault):
        asyncio.run(primitive.remove_subscription("alice", force=True))
        m = vault.load()
        assert m.get("alice") is None
        assert m.active is None


class TestRespawnRobustness:
    """Real-world failure modes encountered during live testing."""

    def test_respawn_skips_suspended_bindings(
        self, primitive, fake_tmux, fake_session_mgr
    ):
        """Suspended bindings have no claude — respawn must leave them alone."""
        bindings = [
            SessionBinding(
                platform="discord", channel_id="active-1", window_id="@1",
                window_name="w1", work_dir="/tmp/p1",
                coding_cli="claude", cli_session_id="s1", suspended=False,
            ),
            SessionBinding(
                platform="discord", channel_id="suspended-1", window_id="@2",
                window_name="w2", work_dir="/tmp/p2",
                coding_cli="claude", cli_session_id="s2", suspended=True,
            ),
        ]
        fake_session_mgr.list_bindings = lambda: bindings
        fake_tmux.window_exists = AsyncMock(return_value=True)

        respawned, failed = asyncio.run(primitive._respawn_all(bindings))
        assert respawned == ["active-1"]  # suspended skipped
        assert failed == []
        # send_text invoked exactly once (for the active binding)
        send_calls = [c for c in fake_tmux.send_text.await_args_list]
        assert len(send_calls) == 1

    def test_respawn_handles_dead_windows(
        self, primitive, fake_tmux, fake_session_mgr
    ):
        """Bindings whose tmux window is gone must be skipped, not crash."""
        bindings = [
            SessionBinding(
                platform="discord", channel_id="alive", window_id="@1",
                window_name="w1", work_dir="/tmp/p1",
                coding_cli="claude", cli_session_id="s1",
            ),
            SessionBinding(
                platform="discord", channel_id="dead", window_id="@99",
                window_name="w99", work_dir="/tmp/p99",
                coding_cli="claude", cli_session_id="s99",
            ),
        ]

        async def window_exists(wid):
            return wid == "@1"

        fake_tmux.window_exists = AsyncMock(side_effect=window_exists)

        respawned, failed = asyncio.run(primitive._respawn_all(bindings))
        assert respawned == ["alive"]
        assert failed == ["dead"]

    def test_kill_skips_suspended_bindings(self, primitive, fake_tmux):
        """Suspended bindings have no claude pid — don't C-c them."""
        bindings = [
            SessionBinding(
                platform="discord", channel_id="active-1", window_id="@1",
                window_name="w1", work_dir="/tmp/p1",
                coding_cli="claude", cli_session_id="s1", suspended=False,
            ),
            SessionBinding(
                platform="discord", channel_id="suspended-1", window_id="@2",
                window_name="w2", work_dir="/tmp/p2",
                coding_cli="claude", cli_session_id="s2", suspended=True,
            ),
        ]

        asyncio.run(primitive._kill_all_claude(bindings))
        # send_keys called exactly once (for the active binding only)
        sk_calls = list(fake_tmux.send_keys.await_args_list)
        assert len(sk_calls) == 1
        assert sk_calls[0].args[0] == "@1"


    def test_concurrent_switches_serialize(
        self, primitive, fake_tmux, vault, claude_cred
    ):
        """Two parallel switch_to calls must be serialized by the lock."""
        events = []

        original_send_text = fake_tmux.send_text

        async def slow_send_text(*args, **kw):
            events.append(("respawn", args[0]))
            await asyncio.sleep(0.1)

        fake_tmux.send_text = AsyncMock(side_effect=slow_send_text)

        async def run_both():
            t1 = asyncio.create_task(primitive.switch_to("bob"))
            await asyncio.sleep(0.01)
            t2 = asyncio.create_task(primitive.switch_to("alice"))
            r1 = await t1
            r2 = await t2
            return r1, r2

        r1, r2 = asyncio.run(run_both())
        assert r1.target == "bob"
        assert r2.target == "alice"
        # final state: alice active, credentials match alice
        with open(claude_cred) as f:
            data = json.load(f)
        assert data == CRED_A
        assert vault.load().active == "alice"
