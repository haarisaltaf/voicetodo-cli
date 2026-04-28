"""Local cache (offline-first list) and pending-uploads queue.

Mirrors the Android app's TodoStore and PendingUploads so the CLI feels
the same and could in theory share a server's expectations.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Iterable, Optional


class Cache:
    """Persists the most recently fetched lists to disk so commands like
    `voicetodo list` can render instantly without a server round-trip."""

    def __init__(self, cache_dir: Path) -> None:
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.open_path = self.dir / "todos_open.json"
        self.completed_path = self.dir / "todos_completed.json"
        self.meta_path = self.dir / "meta.json"

    def get_open(self) -> list[dict]:
        return self._read(self.open_path)

    def get_completed(self) -> list[dict]:
        return self._read(self.completed_path)

    def save_open(self, todos: Iterable[dict]) -> None:
        self._write(self.open_path, list(todos))

    def save_completed(self, todos: Iterable[dict]) -> None:
        self._write(self.completed_path, list(todos))

    def last_refresh(self) -> Optional[float]:
        m = self._read_obj(self.meta_path)
        v = m.get("last_refresh") if isinstance(m, dict) else None
        return float(v) if isinstance(v, (int, float)) else None

    def record_refresh(self, ts: float) -> None:
        self._write_obj(self.meta_path, {"last_refresh": ts})

    @staticmethod
    def _read(p: Path) -> list[dict]:
        if not p.exists():
            return []
        try:
            with open(p) as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    @staticmethod
    def _write(p: Path, items: list[dict]) -> None:
        # Atomic write so a Ctrl-C during save can't corrupt the cache.
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(items, f, indent=2)
        tmp.replace(p)

    @staticmethod
    def _read_obj(p: Path) -> dict:
        if not p.exists():
            return {}
        try:
            with open(p) as f:
                obj = json.load(f)
            return obj if isinstance(obj, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _write_obj(p: Path, obj: dict) -> None:
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(obj, f)
        tmp.replace(p)


class PendingUploads:
    """Holds audio files that failed to upload, for later retry."""

    def __init__(self, dir_: Path) -> None:
        self.dir = Path(dir_)
        self.dir.mkdir(parents=True, exist_ok=True)

    def enqueue(self, src: Path) -> Path:
        dst = self.dir / src.name
        # Try a rename; if cross-device, fall back to copy.
        try:
            src.rename(dst)
        except OSError:
            shutil.copy2(src, dst)
            try:
                src.unlink()
            except OSError:
                pass
        return dst

    def list(self) -> list[Path]:
        if not self.dir.exists():
            return []
        return sorted(p for p in self.dir.iterdir() if p.is_file())

    def remove(self, p: Path) -> None:
        try:
            p.unlink()
        except FileNotFoundError:
            pass

    def count(self) -> int:
        return len(self.list())
