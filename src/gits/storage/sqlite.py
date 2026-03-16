"""SQLite storage layer for Ghost-in-the-Shell."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

DEFAULT_DB_PATH = Path.home() / ".gits" / "gits.db"

SCHEMA_VERSION = 1

DDL = [
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id         TEXT PRIMARY KEY,
        goal       TEXT NOT NULL,
        status     TEXT NOT NULL,
        profile    TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        summary    TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS steps (
        id      TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(id),
        seq     INTEGER NOT NULL,
        action  TEXT NOT NULL,
        input   TEXT,
        output  TEXT,
        ts      INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        id         TEXT PRIMARY KEY,
        task_id    TEXT NOT NULL REFERENCES tasks(id),
        type       TEXT NOT NULL,
        filename   TEXT NOT NULL,
        path       TEXT NOT NULL,
        size_bytes INTEGER,
        created_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS observations (
        id        TEXT PRIMARY KEY,
        task_id   TEXT NOT NULL REFERENCES tasks(id),
        key       TEXT NOT NULL,
        value     TEXT,
        sensitive INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL,
        UNIQUE(task_id, key)
    )
    """,
]


async def migrate(conn: aiosqlite.Connection) -> None:
    """Idempotent migration. Creates all tables and sets schema_version = 1."""
    # Create schema_version first (needed to check version)
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    await conn.commit()

    row = await (await conn.execute("SELECT version FROM schema_version LIMIT 1")).fetchone()
    current = row[0] if row else 0

    if current >= SCHEMA_VERSION:
        return

    for stmt in DDL:
        await conn.execute(stmt)

    if current == 0:
        await conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    else:
        await conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))

    await conn.commit()


class GitsDB:
    """Async context manager wrapping an aiosqlite connection."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> "GitsDB":
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await migrate(self._conn)
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "GitsDB not entered"
        return self._conn

    @property
    def tasks(self) -> "TaskRepo":
        return TaskRepo(self.conn)

    @property
    def steps(self) -> "StepRepo":
        return StepRepo(self.conn)

    @property
    def artifacts(self) -> "ArtifactRepo":
        return ArtifactRepo(self.conn)

    @property
    def memory(self) -> "MemoryRepo":
        return MemoryRepo(self.conn)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    return str(uuid.uuid4())


def _row_to_dict(row: aiosqlite.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


class TaskRepo:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def create(self, goal: str, profile: str | None = None) -> str:
        task_id = _new_id()
        now = _now_ms()
        await self._conn.execute(
            """
            INSERT INTO tasks (id, goal, status, profile, created_at, updated_at)
            VALUES (?, ?, 'queued', ?, ?, ?)
            """,
            (task_id, goal, profile, now, now),
        )
        await self._conn.commit()
        return task_id

    async def get(self, task_id: str) -> dict | None:
        cursor = await self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return _row_to_dict(await cursor.fetchone())

    async def update_status(
        self, task_id: str, status: str, summary: str | None = None
    ) -> None:
        now = _now_ms()
        await self._conn.execute(
            "UPDATE tasks SET status = ?, summary = ?, updated_at = ? WHERE id = ?",
            (status, summary, now, task_id),
        )
        await self._conn.commit()

    async def list_recent(self, limit: int = 50) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM tasks ORDER BY rowid DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


class StepRepo:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(
        self,
        task_id: str,
        seq: int,
        action: str,
        input_data: Any = None,
        output_data: Any = None,
    ) -> str:
        step_id = _new_id()
        await self._conn.execute(
            """
            INSERT INTO steps (id, task_id, seq, action, input, output, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                step_id,
                task_id,
                seq,
                action,
                json.dumps(input_data) if input_data is not None else None,
                json.dumps(output_data) if output_data is not None else None,
                _now_ms(),
            ),
        )
        await self._conn.commit()
        return step_id

    async def list_for_task(self, task_id: str) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM steps WHERE task_id = ? ORDER BY seq", (task_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


class ArtifactRepo:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(
        self,
        task_id: str,
        type_: str,
        filename: str,
        path: str,
        size_bytes: int | None = None,
    ) -> str:
        artifact_id = _new_id()
        await self._conn.execute(
            """
            INSERT INTO artifacts (id, task_id, type, filename, path, size_bytes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (artifact_id, task_id, type_, filename, path, size_bytes, _now_ms()),
        )
        await self._conn.commit()
        return artifact_id

    async def list_for_task(self, task_id: str) -> list[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at", (task_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


class MemoryRepo:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def set(
        self, task_id: str, key: str, value: str, sensitive: bool = False
    ) -> None:
        obs_id = _new_id()
        now = _now_ms()
        await self._conn.execute(
            """
            INSERT INTO observations (id, task_id, key, value, sensitive, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id, key) DO UPDATE SET
                value = excluded.value,
                sensitive = excluded.sensitive,
                updated_at = excluded.updated_at
            """,
            (obs_id, task_id, key, value, int(sensitive), now),
        )
        await self._conn.commit()

    async def get(self, task_id: str, key: str) -> str | None:
        cursor = await self._conn.execute(
            "SELECT value FROM observations WHERE task_id = ? AND key = ?",
            (task_id, key),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def clear_sensitive(self, task_id: str) -> None:
        await self._conn.execute(
            "DELETE FROM observations WHERE task_id = ? AND sensitive = 1", (task_id,)
        )
        await self._conn.commit()
