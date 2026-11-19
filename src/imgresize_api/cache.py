"""A small disk cache for processed images with a TTL and a size cap.

Layout: ``<dir>/<k[0:2]>/<k[2:4]>/<k>.bin`` holds the image and ``<k>.json`` its metadata
(content type, ETag, creation time). Writes are atomic (temporary file + rename), so
concurrent workers never serve a half-written file.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Entry:
    content: bytes
    content_type: str
    etag: str
    created: float
    width: int = 0
    height: int = 0


def make_key(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_etag(content: bytes) -> str:
    return '"' + hashlib.sha256(content).hexdigest()[:32] + '"'


class DiskCache:
    def __init__(self, directory: str | Path, ttl: int, max_bytes: int, clock: Callable[[], float] = time.time) -> None:
        self.directory = Path(directory)
        self.ttl = ttl
        self.max_bytes = max_bytes
        self.clock = clock
        self.writes_since_prune = 0
        self.directory.mkdir(parents=True, exist_ok=True)

    def _paths(self, key: str) -> tuple[Path, Path]:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("cache keys are sha256 hex digests")
        folder = self.directory / key[:2] / key[2:4]
        return folder / f"{key}.bin", folder / f"{key}.json"

    def get(self, key: str) -> Entry | None:
        data_path, meta_path = self._paths(key)
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
            if self.clock() - float(meta["created"]) > self.ttl:
                self._remove(data_path, meta_path)
                return None
            content = data_path.read_bytes()
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if meta.get("size") != len(content):
            self._remove(data_path, meta_path)
            return None
        return Entry(
            content,
            meta["content_type"],
            meta["etag"],
            float(meta["created"]),
            meta.get("width", 0),
            meta.get("height", 0),
        )

    def put(self, key: str, content: bytes, content_type: str, width: int = 0, height: int = 0) -> Entry:
        data_path, meta_path = self._paths(key)
        data_path.parent.mkdir(parents=True, exist_ok=True)
        entry = Entry(content, content_type, make_etag(content), self.clock(), width, height)
        meta = {
            "content_type": content_type,
            "etag": entry.etag,
            "created": entry.created,
            "size": len(content),
            "width": width,
            "height": height,
        }
        self._atomic_write(data_path, content)
        self._atomic_write(meta_path, json.dumps(meta).encode("utf-8"))
        self.writes_since_prune += 1
        return entry

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    @staticmethod
    def _remove(*paths: Path) -> None:
        for path in paths:
            with contextlib.suppress(OSError):
                path.unlink()

    def prune(self) -> dict[str, int]:
        """Delete expired entries, then the oldest ones until the cache fits ``max_bytes``."""
        self.writes_since_prune = 0
        now = self.clock()
        removed = 0
        alive: list[tuple[float, int, Path, Path]] = []
        for meta_path in self.directory.glob("*/*/*.json"):
            data_path = meta_path.with_suffix(".bin")
            try:
                meta = json.loads(meta_path.read_text("utf-8"))
                created = float(meta["created"])
                size = data_path.stat().st_size
            except (OSError, ValueError, KeyError, TypeError):
                self._remove(meta_path, data_path)
                removed += 1
                continue
            if now - created > self.ttl:
                self._remove(meta_path, data_path)
                removed += 1
            else:
                alive.append((created, size, meta_path, data_path))
        for stray in self.directory.glob("*/*/.tmp-*"):
            with contextlib.suppress(OSError):
                if now - stray.stat().st_mtime > 3600:
                    stray.unlink()
        total = sum(item[1] for item in alive)
        if self.max_bytes:
            alive.sort()
            while alive and total > self.max_bytes:
                _created, size, meta_path, data_path = alive.pop(0)
                self._remove(meta_path, data_path)
                total -= size
                removed += 1
        return {"removed": removed, "entries": len(alive), "bytes": total}
