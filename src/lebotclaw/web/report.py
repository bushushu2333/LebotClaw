"""家长学习周报：汇总一周的学习数据，LLM 写成给家长看的真诚报告。

买单的是家长——这份周报是信任与续费的载体。
数据全部来自本地：<user_dir> 下 4 类记忆 / 错题本 / 生词本 / 出题战绩。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from lebotclaw.tools.builtin.store import JsonListStore

_WEEK = 7 * 86400

_PROMPT = """你是孩子的学习伙伴"超级小博"，每周给家长写一份真诚的学习周报（不超过 600 字）。
要求：
- 像朋友跟家长汇报，不官方、不堆砌术语，说人话
- 有亮点要具体夸（哪道题、哪个知识点），有问题也坦诚说，但最后一定给 1-2 条在家就能做的建议
- 结构：📌 本周概览（数据）→ 🌟 值得表扬的地方 → 🔍 需要关注的地方 → 💡 超级小博的建议
- 数据平平就实话实说，不要编造学习行为

学生：{name}{grade}

本周数据：
{stats}
"""


def _report_file(user_dir: str):
    return Path(user_dir).expanduser() / "weekly_report.json"


def collect_stats(memory, user_dir: str = "~/.lebotclaw") -> dict:
    """本周学习数据汇总。"""
    now = time.time()
    stats: dict = {"period": f"{datetime.fromtimestamp(now - _WEEK):%m月%d日} – {datetime.now():%m月%d日}"}

    # 会话摘要（近 7 天）
    summaries = [
        e for e in memory.search_memory(category="session_summary", limit=50)
        if getattr(e, "updated_at", 0) and now - e.updated_at < _WEEK
    ]
    stats["chat_sessions"] = len(summaries)
    stats["recent_topics"] = [e.content[:80] for e in summaries[:5]]

    # 学习进度记忆
    progress = memory.search_memory(category="learning_progress", limit=20)
    stats["progress_notes"] = [f"{e.key}: {e.content[:60]}" for e in progress[:8]]

    # 错题本
    mistakes = JsonListStore(f"{user_dir}/mistakes.json").all()
    week_mistakes = [i for i in mistakes if now - i.get("created_at", 0) < _WEEK]
    stats["mistakes_total"] = len(mistakes)
    stats["mistakes_mastered"] = sum(1 for i in mistakes if i.get("mastered"))
    stats["mistakes_this_week"] = [
        {"q": i.get("question", "")[:40], "note": i.get("note", "")[:40], "mastered": i.get("mastered", False)}
        for i in week_mistakes[-6:]
    ]

    # 生词本
    words = JsonListStore(f"{user_dir}/wordbank.json").all()
    stats["words_total"] = len(words)
    stats["words_mastered"] = sum(1 for i in words if i.get("mastered"))

    # 出题战绩
    qfile = Path(user_dir).expanduser() / "quizzes.json"
    quizzes = []
    if qfile.exists():
        try:
            quizzes = list(json.loads(qfile.read_text(encoding="utf-8")).values())
        except (json.JSONDecodeError, OSError):
            pass
    week_quizzes = [q for q in quizzes if now - q.get("created_at", 0) < _WEEK]
    stats["quizzes_this_week"] = len(week_quizzes)
    stats["quizzes_passed"] = sum(1 for q in week_quizzes if q.get("passed"))

    return stats


def generate_report(adapter, memory, user_dir: str = "~/.lebotclaw") -> str:
    """LLM 生成周报文本（阻塞调用，需走 io_bound）。"""
    profile = memory.get_student_profile()
    stats = collect_stats(memory, user_dir)
    resp = adapter.generate(
        messages=[{"role": "user", "content": _PROMPT.format(
            name=profile.get("名字", "孩子"),
            grade=f"（{profile['年级']}）" if profile.get("年级") else "",
            stats=json.dumps(stats, ensure_ascii=False, indent=2),
        )}],
        max_tokens=4096,
    )
    text = resp.content or "本周数据不足，下周再来看看吧～"
    f = _report_file(user_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({
        "generated_at": time.time(), "text": text, "stats": stats,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return text


def cached_report(user_dir: str = "~/.lebotclaw", max_age: float = 6 * 86400):
    """读缓存周报（一周内有效）。"""
    f = _report_file(user_dir)
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if time.time() - d.get("generated_at", 0) > max_age:
        return None
    return d
