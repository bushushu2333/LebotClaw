"""L2 结构化知识库（参考 inno-agent 的 L2 wiki）。

区别于 memory（对话自动抽取的碎片记忆）：wiki 是用户/agent 主动沉淀的**结构化知识页**
（带标题、来源、标签），agent 每轮按当前问题检索相关页注入 system prompt，
让回答有据可依、可追溯。

并发安全：与 MemoryStore 一致（check_same_thread=False + WAL + RLock）。
"""
import sqlite3
import re
import time
import uuid
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class WikiPage:
    id: str
    title: str
    content: str
    source: str = ""
    tags: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0


_CREATE = """
CREATE TABLE IF NOT EXISTS wiki_pages (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wiki_title ON wiki_pages(title);
"""


class WikiStore:
    def __init__(self, db_path: str = "~/.lebotclaw/wiki.db"):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_CREATE)
            self._conn.commit()

    def add_page(self, title: str, content: str, source: str = "",
                 tags: Optional[List[str]] = None) -> str:
        pid = uuid.uuid4().hex[:12]
        now = time.time()
        tags_str = ",".join(tags) if tags else ""
        with self._lock:
            self._conn.execute(
                "INSERT INTO wiki_pages (id,title,content,source,tags,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (pid, title, content, source, tags_str, now, now),
            )
            self._conn.commit()
        return pid

    def update_page(self, pid: str, title: str = None, content: str = None,
                    source: str = None, tags: Optional[List[str]] = None) -> None:
        sets, params = [], []
        if title is not None:
            sets.append("title=?"); params.append(title)
        if content is not None:
            sets.append("content=?"); params.append(content)
        if source is not None:
            sets.append("source=?"); params.append(source)
        if tags is not None:
            sets.append("tags=?"); params.append(",".join(tags))
        if not sets:
            return
        sets.append("updated_at=?"); params.append(time.time()); params.append(pid)
        with self._lock:
            self._conn.execute(
                f"UPDATE wiki_pages SET {','.join(sets)} WHERE id=?", params)
            self._conn.commit()

    def search(self, query: str = "", limit: int = 5) -> List[WikiPage]:
        with self._lock:
            if query:
                rows = self._conn.execute(
                    "SELECT * FROM wiki_pages WHERE title LIKE ? OR content LIKE ? "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (f"%{query}%", f"%{query}%", limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM wiki_pages ORDER BY updated_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [self._row(r) for r in rows]

    def search_relevant(self, text: str, limit: int = 3) -> List[WikiPage]:
        """按 text 的 2/3-gram 命中数排序检索（中文友好，整句也能匹配到含关键词的页）。

        比 search() 的整句 LIKE 更宽松：把问题拆成 2/3 字片段，命中越多越相关。
        """
        cleaned = re.sub(r"[^一-龥a-zA-Z0-9]", "", text or "")
        grams = set()
        for n in (2, 3):
            for i in range(len(cleaned) - n + 1):
                grams.add(cleaned[i:i + n])
        grams = {g for g in grams if len(g) >= 2}
        if not grams:
            return []
        with self._lock:
            rows = self._conn.execute("SELECT * FROM wiki_pages").fetchall()
        scored = []
        for r in rows:
            blob = r["title"] + " " + r["content"]
            hit = sum(1 for g in grams if g in blob)
            if hit > 0:
                scored.append((hit, r))
        scored.sort(key=lambda x: -x[0])
        return [self._row(r) for _, r in scored[:limit]]

    def list_pages(self, limit: int = 200) -> List[WikiPage]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM wiki_pages ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row(r) for r in rows]

    def get_page(self, pid: str) -> Optional[WikiPage]:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM wiki_pages WHERE id=?", (pid,)).fetchone()
        return self._row(r) if r else None

    def delete_page(self, pid: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM wiki_pages WHERE id=?", (pid,))
            self._conn.commit()

    def _row(self, r: sqlite3.Row) -> WikiPage:
        return WikiPage(
            id=r["id"], title=r["title"], content=r["content"],
            source=r["source"], tags=r["tags"],
            created_at=r["created_at"], updated_at=r["updated_at"],
        )
