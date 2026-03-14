"""Atomic JSON file writer — write to temp file then rename."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path


async def atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON file atomically: write to temp file then rename.

    This prevents partial writes from corrupting state files if the
    process crashes mid-write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_write_sync, path, data)


def _write_sync(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with open(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
