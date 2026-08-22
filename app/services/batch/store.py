"""Batch persistence: in-memory cache + JSON files under ``<data_dir>/batches``."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from app.core.config import Settings


class BatchStore:
    """Thread-safe store for extraction batches (persisted as JSON files)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.dir: Path = settings.data_path / "batches"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cache: dict[str, dict] = {}

    def create(self, files: list[str], source: str = "upload") -> str:
        batch_id = uuid.uuid4().hex[:12]
        batch = {
            "id": batch_id,
            "status": "pending",
            "source": source,  # "upload" (real extraction) | "demo" (mock)
            "total": len(files),
            "done": 0,
            "files": files,
            "results": [None] * len(files),
            "findings": [],
            "audit_summary": None,
            "graph": None,
            "insights": {},
            "errors": [],
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "completed_at": None,
        }
        with self._lock:
            self._cache[batch_id] = batch
        self._persist(batch)
        return batch_id

    def get(self, batch_id: str) -> dict | None:
        with self._lock:
            batch = self._cache.get(batch_id)
        if batch is not None:
            return batch
        path = self.dir / f"{batch_id}.json"
        if path.is_file():
            try:
                batch = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
            with self._lock:
                self._cache[batch_id] = batch
            return batch
        return None

    def update(self, batch_id: str, **changes) -> None:
        with self._lock:
            batch = self._cache.get(batch_id)
            if batch is None:
                return
            batch.update(changes)
        self._persist(batch)

    def _persist(self, batch: dict) -> None:
        try:
            (self.dir / f"{batch['id']}.json").write_text(
                json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass  # persistence is best-effort
