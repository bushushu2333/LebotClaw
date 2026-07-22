"""内容守护命中记录（per-user）。家长周报聚合用。

记录落在 ``~/.lebotclaw/users/<uid>/moderation_log.json``，原词打码保护隐私。
mental（自伤/伤人）类标 ``high=true``，周报单独温和提示。
"""
import json
import time
from pathlib import Path

from lebotclaw.core.moderation import mask_words, Result

_CONFIG_DIR = Path.home() / ".lebotclaw"


def _user_dir(uid: str) -> Path:
    return (_CONFIG_DIR / "users" / uid) if uid else _CONFIG_DIR


def _log_path(uid: str) -> Path:
    return _user_dir(uid) / "moderation_log.json"


def log_hit(uid: str, result: Result):
    """记一次命中（uid 为空=全局/CLI，落到根目录）。"""
    if not result.hit:
        return
    path = _log_path(uid)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = []
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        data.append({
            "ts": int(time.time()),
            "category": result.category,
            "severity": result.severity,
            "words": mask_words(result.words),
            "high": result.priority_high,
        })
        # 保留最近 500 条，防无限增长
        path.write_text(json.dumps(data[-500:], ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - 日志失败不应影响对话主流程
        print(f"⚠ moderation_log 写入失败：{e}")


def load_log(uid: str) -> list:
    """读 per-user 命中记录（家长周报用）。"""
    path = _log_path(uid)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def summarize(uid: str, since_ts: int = 0) -> dict:
    """聚合周报用：分类计数 + 是否有 high（心理）命中。"""
    rows = [r for r in load_log(uid) if r.get("ts", 0) >= since_ts]
    by_cat = {}
    high = 0
    for r in rows:
        c = r.get("category", "")
        by_cat[c] = by_cat.get(c, 0) + 1
        if r.get("high"):
            high += 1
    return {"total": len(rows), "by_category": by_cat, "mental_hits": high}
