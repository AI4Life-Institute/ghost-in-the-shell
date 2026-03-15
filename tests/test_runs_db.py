"""Unit tests for RunsDB."""

from __future__ import annotations

import pytest
import pytest_asyncio
from pathlib import Path

from gits.storage.db import RunsDB


@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / "test.db"


@pytest.mark.asyncio
async def test_insert_and_query_run(tmp_db):
    async with RunsDB(tmp_db) as db:
        await db.insert_run(run_id="2024-01-15T05:00:00", skill_name="test-skill", log_path="/tmp/test.log")
        runs = await db.query_runs()
        assert len(runs) == 1
        assert runs[0]["skill_name"] == "test-skill"
        assert runs[0]["status"] == "running"


@pytest.mark.asyncio
async def test_finish_run(tmp_db):
    async with RunsDB(tmp_db) as db:
        await db.insert_run(run_id="run-1", skill_name="s", log_path="/tmp/x.log")
        await db.finish_run("run-1", exit_code=0, status="success")
        runs = await db.query_runs()
        assert runs[0]["status"] == "success"
        assert runs[0]["exit_code"] == 0
        assert runs[0]["finished_at"] is not None


@pytest.mark.asyncio
async def test_update_guard_log(tmp_db):
    async with RunsDB(tmp_db) as db:
        await db.insert_run(run_id="run-2", skill_name="s", log_path="/tmp/x.log")
        await db.update_guard_log("run-2", {"decision": "retry", "reason": "transient error"})
        runs = await db.query_runs()
        import json
        guard = json.loads(runs[0]["guard_log"])
        assert guard["decision"] == "retry"
        assert runs[0]["status"] == "guarded"


@pytest.mark.asyncio
async def test_query_runs_filtered(tmp_db):
    async with RunsDB(tmp_db) as db:
        await db.insert_run(run_id="r1", skill_name="alpha", log_path="/tmp/a.log")
        await db.insert_run(run_id="r2", skill_name="beta", log_path="/tmp/b.log")
        alpha = await db.query_runs(skill_name="alpha")
        assert len(alpha) == 1
        assert alpha[0]["skill_name"] == "alpha"


@pytest.mark.asyncio
async def test_insert_and_query_artifact(tmp_db):
    async with RunsDB(tmp_db) as db:
        await db.insert_run(run_id="run-3", skill_name="s", log_path="/tmp/x.log")
        art_id = await db.insert_artifact(run_id="run-3", type_="report", path="/tmp/report.json", label="daily")
        artifacts = await db.query_artifacts("run-3")
        assert len(artifacts) == 1
        assert artifacts[0]["label"] == "daily"
        assert artifacts[0]["id"] == art_id


@pytest.mark.asyncio
async def test_idempotent_schema(tmp_db):
    """Opening RunsDB twice does not fail."""
    async with RunsDB(tmp_db) as db:
        await db.insert_run(run_id="r1", skill_name="s", log_path="/x.log")
    async with RunsDB(tmp_db) as db:
        runs = await db.query_runs()
        assert len(runs) == 1
