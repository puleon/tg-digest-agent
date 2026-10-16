"""Vision results per message, kept on disk between the two passes of a refresh.

The refresh reads images with the vision model first (one model swap), then classifies with
the text model (another swap) — the shared server keeps two models and one of them is the
assistant's. The second pass takes captions/OCR from here instead of calling the vision tier;
a run that dies between the passes resumes instead of reading the images again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class VisionCache:
    def __init__(self, path: Path, *, only: bool = False) -> None:
        self.path = path
        self.only = only
        """Cache-only: a miss is a failed read, never a call to the vision tier."""
        self._data: dict[int, tuple[dict[str, Any] | None, list[str]]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._data[int(row["post_id"])] = (row.get("result"), row.get("degraded", []))

    def __contains__(self, post_id: object) -> bool:
        return post_id in self._data

    def __len__(self) -> int:
        return len(self._data)

    def get(self, post_id: int) -> tuple[dict[str, Any] | None, list[str]]:
        return self._data[post_id]

    def put(self, post_id: int, result: dict[str, Any] | None, degraded: list[str]) -> None:
        self._data[post_id] = (result, list(degraded))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            row = {"post_id": post_id, "result": result, "degraded": degraded}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def cache_only(self) -> VisionCache:
        view = VisionCache.__new__(VisionCache)
        view.path, view.only, view._data = self.path, True, self._data
        return view

    def clear(self) -> None:
        self._data.clear()
        self.path.unlink(missing_ok=True)
