from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, name: str, data: dict[str, Any]) -> Path:
        path = self.root / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def read(self, name: str) -> dict[str, Any]:
        path = self.root / name
        return json.loads(path.read_text(encoding="utf-8"))
