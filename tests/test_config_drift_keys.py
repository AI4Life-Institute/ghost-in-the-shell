"""The new config.env keys are actually declared (Ghost task drftnt).

``Settings`` is validated with ``extra='forbid'`` — a pydantic **default**, so
the literal does not appear in ``model_config`` and nothing in the file warns
you. An undeclared key sitting in ``~/.gits/config.env`` therefore makes *every*
``Settings()`` raise: the bot, every PreToolUse hook, and every CLI invocation.
ghost#18 shipped 26 undeclared keys and CI stayed green for two months, because
no test ever wrote a real config.env and constructed the model.

This is that test. It reads a real file the way production does, so removing
either declaration from ``gits.config.Settings`` fails it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gits.config import Settings

DRIFT_KEYS = {
    "GHOST_DRIFT_WATCH_ENABLED": "false",
    "GHOST_DRIFT_WATCH_INTERVAL_S": "1800",
}


def write_config_env(tmp_path, keys: dict[str, str]):
    path = tmp_path / "config.env"
    path.write_text("".join(f"{k}={v}\n" for k, v in keys.items()))
    return path


def settings_from(path):
    """Construct Settings the way the bot does, but off a controlled file.

    ``allowed_paths``/``bind_root`` are pinned so a developer's own
    ``~/.gits/config.env`` cannot influence the result.
    """
    return Settings(_env_file=str(path), allowed_paths=[], bind_root=None)


def test_drift_keys_are_declared_and_parse(tmp_path):
    settings = settings_from(write_config_env(tmp_path, DRIFT_KEYS))
    assert settings.ghost_drift_watch_enabled is False
    assert settings.ghost_drift_watch_interval_s == 1800.0


def test_defaults_are_sane_when_the_keys_are_absent(tmp_path):
    settings = settings_from(write_config_env(tmp_path, {}))
    assert settings.ghost_drift_watch_enabled is True
    assert settings.ghost_drift_watch_interval_s == 3600.0


def test_an_undeclared_key_really_does_raise(tmp_path):
    """Pins the hazard itself, so the reason for this file stays visible."""
    path = write_config_env(tmp_path, {"GHOST_DRIFT_NOT_A_REAL_KEY": "1"})
    with pytest.raises(ValidationError):
        settings_from(path)
