from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonStateStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.root / f'{name}.json'

    def load(self, name: str, default: Any = None) -> Any:
        path = self.path(name)
        if not path.exists():
            return {} if default is None else default
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return {} if default is None else default

    def save(self, name: str, value: Any) -> None:
        path = self.path(name)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')

    def append_jsonl(self, name: str, row: dict[str, Any]) -> None:
        path = self.root / f'{name}.jsonl'
        with path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
