"""Tests for atomic JSON file writer."""

import asyncio
import json
from pathlib import Path

import pytest

from gits.utils.atomic_write import atomic_write_json


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


class TestAtomicWriteJson:
    def test_basic_write(self, tmp_dir):
        path = tmp_dir / "test.json"
        asyncio.run(atomic_write_json(path, {"key": "value"}))
        assert path.exists()
        with open(path) as f:
            data = json.load(f)
        assert data == {"key": "value"}

    def test_overwrite(self, tmp_dir):
        path = tmp_dir / "test.json"
        asyncio.run(atomic_write_json(path, {"a": 1}))
        asyncio.run(atomic_write_json(path, {"b": 2}))
        with open(path) as f:
            data = json.load(f)
        assert data == {"b": 2}

    def test_creates_parent_dirs(self, tmp_dir):
        path = tmp_dir / "sub" / "dir" / "test.json"
        asyncio.run(atomic_write_json(path, {"nested": True}))
        assert path.exists()

    def test_unicode(self, tmp_dir):
        path = tmp_dir / "unicode.json"
        asyncio.run(atomic_write_json(path, {"text": "你好世界 🎉"}))
        with open(path) as f:
            data = json.load(f)
        assert data["text"] == "你好世界 🎉"

    def test_no_temp_file_left_on_success(self, tmp_dir):
        path = tmp_dir / "clean.json"
        asyncio.run(atomic_write_json(path, {"clean": True}))
        # Only the target file should exist
        files = list(tmp_dir.iterdir())
        assert len(files) == 1
        assert files[0].name == "clean.json"

    def test_no_partial_write_on_error(self, tmp_dir):
        """If writing fails, original file should not be corrupted."""
        path = tmp_dir / "safe.json"
        asyncio.run(atomic_write_json(path, {"original": True}))

        # Try to write non-serializable data
        with pytest.raises(TypeError):
            asyncio.run(atomic_write_json(path, {"bad": object()}))

        # Original file should still be intact
        with open(path) as f:
            data = json.load(f)
        assert data == {"original": True}
