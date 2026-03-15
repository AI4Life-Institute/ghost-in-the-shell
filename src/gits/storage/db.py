"""RunsDB — skill runner metadata storage for Ghost-in-the-Shell."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

DEFAULT_DB_PATH = Path.home() / ".gits" / "gits.db"

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS runs (
        id          TEXT PRIMARY KEY,
        skill_name  TEXT NOT NULL,
        agent_type  TEXT NOT NULL,
        started_at  TEXT NOT NULL,
        finished_at TEXT,
        exit_code   INTEGER,
        status      TEXT NOT NULL,
        log_path    TEXT NOT NULL,
        guard_log   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_artifacts (
        id          TEXT PRIMARY KEY,
        run_id      TEXT NOT NULL REFERENCES runs(id),
        type        TEXT NOT NULL,
        path        TEXT NOT NULL,
        label       TEXT,
        metadata    TEXT,
        created_at  TEXT NOT NULL
    )
    """,
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _new_id() -> str:
    import uuid
    return str(uuid.uuid4())


class RunsDB:
    """Async context manager for skill runner run metadata."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> "RunsDB":
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        for stmt in _DDL:
            await self._conn.execute(stmt)
        await self._conn.commit()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def _c(self) -> aiosqlite.Connection:
        assert self._conn is not None, "RunsDB not entered"
        return self._conn

    async def insert_run(
        self,
        *,
        run_id: str,
        skill_name: str,
        agent_type: str = "runner",
        log_path: str,
    ) -> None:
        """Insert a new run record with status=running."""
        now = _now_iso()
        await self._c.execute(
            """
            INSERT INTO runs (id, skill_name, agent_type, started_at, status, log_path)
            VALUES (?, ?, ?, ?, 'running', ?)
            """,
            (run_id, skill_name, agent_type, now, log_path),
        )
        await self._c.commit()

    async def finish_run(self, run_id: str, *, exit_code: int, status: str) -> None:
        """Update run on completion."""
        now = _now_iso()
        await self._c.execute(
            "UPDATE runs SET finished_at=?, exit_code=?, status=? WHERE id=?",
            (now, exit_code, status, run_id),
        )
        await self._c.commit()

    async def update_guard_log(self, run_id: str, guard_data: dict) -> None:
        """Store guard decision JSON."""
        await self._c.execute(
            "UPDATE runs SET guard_log=?, status='guarded' WHERE id=?",
            (json.dumps(guard_data), run_id),
        )
        await self._c.commit()

    async def query_runs(
        self,
        skill_name: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return recent runs, optionally filtered by skill_name."""
        if skill_name:
            cursor = await self._c.execute(
                "SELECT * FROM runs WHERE skill_name=? ORDER BY started_at DESC LIMIT ?",
                (skill_name, limit),
            )
        else:
            cursor = await self._c.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def insert_artifact(
        self,
        *,
        run_id: str,
        type_: str,
        path: str,
        label: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        artifact_id = _new_id()
        await self._c.execute(
            """
            INSERT INTO run_artifacts (id, run_id, type, path, label, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                run_id,
                type_,
                path,
                label,
                json.dumps(metadata) if metadata else None,
                _now_iso(),
            ),
        )
        await self._c.commit()
        return artifact_id

    async def query_artifacts(self, run_id: str) -> list[dict]:
        cursor = await self._c.execute(
            "SELECT * FROM run_artifacts WHERE run_id=? ORDER BY created_at",
            (run_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
