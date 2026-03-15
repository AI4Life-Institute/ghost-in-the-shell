"""Unit tests for the SQLite storage layer."""

from __future__ import annotations

import pytest
import aiosqlite

from gits.storage.sqlite import (
    ArtifactRepo,
    GitsDB,
    MemoryRepo,
    StepRepo,
    TaskRepo,
    migrate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def make_conn() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await migrate(conn)
    return conn


# ---------------------------------------------------------------------------
# TaskRepo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_create_and_get():
    conn = await make_conn()
    repo = TaskRepo(conn)
    task_id = await repo.create("browse example.com", profile="default")
    assert isinstance(task_id, str) and len(task_id) > 0

    task = await repo.get(task_id)
    assert task is not None
    assert task["goal"] == "browse example.com"
    assert task["status"] == "queued"
    assert task["profile"] == "default"
    assert task["summary"] is None
    await conn.close()


@pytest.mark.asyncio
async def test_task_get_missing():
    conn = await make_conn()
    repo = TaskRepo(conn)
    result = await repo.get("nonexistent-id")
    assert result is None
    await conn.close()


@pytest.mark.asyncio
async def test_task_update_status():
    conn = await make_conn()
    repo = TaskRepo(conn)
    task_id = await repo.create("do something")
    await repo.update_status(task_id, "running")
    task = await repo.get(task_id)
    assert task["status"] == "running"
    assert task["summary"] is None

    await repo.update_status(task_id, "done", summary="All finished")
    task = await repo.get(task_id)
    assert task["status"] == "done"
    assert task["summary"] == "All finished"
    await conn.close()


@pytest.mark.asyncio
async def test_task_list_recent():
    conn = await make_conn()
    repo = TaskRepo(conn)
    ids = [await repo.create(f"task {i}") for i in range(5)]
    recent = await repo.list_recent(limit=3)
    assert len(recent) == 3
    # most recent first
    assert recent[0]["goal"] == "task 4"
    await conn.close()


# ---------------------------------------------------------------------------
# StepRepo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_step_add_and_list():
    conn = await make_conn()
    task_id = await TaskRepo(conn).create("browse")
    repo = StepRepo(conn)

    step_id = await repo.add(task_id, seq=0, action="navigate", input_data={"url": "https://example.com"})
    assert isinstance(step_id, str)

    await repo.add(task_id, seq=1, action="snapshot", output_data={"html": "<html/>"})

    steps = await repo.list_for_task(task_id)
    assert len(steps) == 2
    assert steps[0]["action"] == "navigate"
    assert steps[0]["seq"] == 0
    assert '"url"' in steps[0]["input"]
    assert steps[1]["action"] == "snapshot"
    assert '"html"' in steps[1]["output"]
    await conn.close()


@pytest.mark.asyncio
async def test_step_list_empty():
    conn = await make_conn()
    task_id = await TaskRepo(conn).create("empty task")
    steps = await StepRepo(conn).list_for_task(task_id)
    assert steps == []
    await conn.close()


# ---------------------------------------------------------------------------
# ArtifactRepo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_artifact_add_and_list():
    conn = await make_conn()
    task_id = await TaskRepo(conn).create("download files")
    repo = ArtifactRepo(conn)

    art_id = await repo.add(task_id, type_="pdf", filename="report.pdf", path="/tmp/report.pdf", size_bytes=1024)
    assert isinstance(art_id, str)

    artifacts = await repo.list_for_task(task_id)
    assert len(artifacts) == 1
    assert artifacts[0]["type"] == "pdf"
    assert artifacts[0]["filename"] == "report.pdf"
    assert artifacts[0]["path"] == "/tmp/report.pdf"
    assert artifacts[0]["size_bytes"] == 1024
    await conn.close()


@pytest.mark.asyncio
async def test_artifact_list_empty():
    conn = await make_conn()
    task_id = await TaskRepo(conn).create("no files")
    result = await ArtifactRepo(conn).list_for_task(task_id)
    assert result == []
    await conn.close()


# ---------------------------------------------------------------------------
# MemoryRepo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_set_get():
    conn = await make_conn()
    task_id = await TaskRepo(conn).create("memory test")
    repo = MemoryRepo(conn)

    await repo.set(task_id, "username", "alice")
    val = await repo.get(task_id, "username")
    assert val == "alice"
    await conn.close()


@pytest.mark.asyncio
async def test_memory_upsert():
    conn = await make_conn()
    task_id = await TaskRepo(conn).create("upsert test")
    repo = MemoryRepo(conn)

    await repo.set(task_id, "counter", "1")
    await repo.set(task_id, "counter", "2")
    val = await repo.get(task_id, "counter")
    assert val == "2"
    await conn.close()


@pytest.mark.asyncio
async def test_memory_get_missing():
    conn = await make_conn()
    task_id = await TaskRepo(conn).create("missing key")
    result = await MemoryRepo(conn).get(task_id, "no_such_key")
    assert result is None
    await conn.close()


@pytest.mark.asyncio
async def test_memory_clear_sensitive():
    conn = await make_conn()
    task_id = await TaskRepo(conn).create("sensitive test")
    repo = MemoryRepo(conn)

    await repo.set(task_id, "token", "secret123", sensitive=True)
    await repo.set(task_id, "username", "alice", sensitive=False)

    assert await repo.get(task_id, "token") == "secret123"

    await repo.clear_sensitive(task_id)

    assert await repo.get(task_id, "token") is None
    assert await repo.get(task_id, "username") == "alice"
    await conn.close()


# ---------------------------------------------------------------------------
# Migration idempotency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_migrate_idempotent():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await migrate(conn)
    # Run again — must not raise
    await migrate(conn)
    # Schema version should still be 1
    cursor = await conn.execute("SELECT version FROM schema_version")
    row = await cursor.fetchone()
    assert row[0] == 1
    await conn.close()
