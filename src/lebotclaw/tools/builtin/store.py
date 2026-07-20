"""错题本/生词本共用的 JSON 列表存储（线程安全）。"""
import json
import threading
import time
from pathlib import Path
from typing import Optional


class JsonListStore:
    """append-only 小清单存储：~/.lebotclaw/ 下一个 json 文件一条 list。

    Web 多线程下安全（模块级锁 + 写文件整体覆盖）。条目为 dict，
    至少含 id/created_at，具体字段由使用方定义。
    """

    _lock = threading.Lock()

    def __init__(self, path: str):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                return data if isinstance(data, list) else []
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def _save(self, items: list):
        self.path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    def all(self) -> list:
        with self._lock:
            return self._load()

    def add(self, item: dict) -> dict:
        with self._lock:
            items = self._load()
            item["id"] = (max((i.get("id", 0) for i in items), default=0) + 1)
            item.setdefault("created_at", time.time())
            items.append(item)
            self._save(items)
        return item

    def update(self, item_id: int, **fields) -> Optional[dict]:
        with self._lock:
            items = self._load()
            for it in items:
                if it.get("id") == item_id:
                    it.update(fields)
                    self._save(items)
                    return it
        return None

    def find(self, item_id: int) -> Optional[dict]:
        with self._lock:
            for it in self._load():
                if it.get("id") == item_id:
                    return it
        return None
