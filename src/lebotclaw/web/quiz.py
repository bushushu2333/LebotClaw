"""错题举一反三：LLM 按错因生成专属选择题，逐题判分，全对自动消错题。

豆包也能出题，但不知道"你"错在哪——这里的题是按错题本里每个孩子的
真实错因出的，这是差异化的根。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from lebotclaw.tools.builtin.store import JsonListStore

_GEN_PROMPT = """你是中小学出题老师。根据学生的错题，出 {count} 道考查相同知识点、但数字或情境不同的单项选择题（举一反三，严禁出原题）。
学生年级：{grade}

学生的错题：
{mistakes}

只输出 JSON 数组，不要输出任何其他文字：
[{{"q": "题干", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "answer": "A", "explain": "一句话讲清考点和易错点"}}]"""


def _quiz_file(user_dir: str):
    return Path(user_dir).expanduser() / "quizzes.json"


def _load(user_dir: str) -> dict:
    f = _quiz_file(user_dir)
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save(d: dict, user_dir: str):
    f = _quiz_file(user_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _mistake_store(user_dir: str) -> JsonListStore:
    return JsonListStore(f"{user_dir}/mistakes.json")


def _parse_questions(text: str) -> list:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for q in arr:
        if isinstance(q, dict) and q.get("q") and isinstance(q.get("options"), dict) and q.get("answer"):
            out.append({
                "q": str(q["q"]),
                "options": {k: str(v) for k, v in q["options"].items()},
                "answer": str(q["answer"]).strip().upper(),
                "explain": str(q.get("explain", "")),
            })
    return out


def generate_quiz(adapter, memory, mistake_ids: list = None, count: int = 3, user_dir: str = "~/.lebotclaw") -> dict | None:
    """按错题生成选择题卷。mistake_ids 为空则取最近未掌握错题。"""
    items = _mistake_store(user_dir).all()
    sel = [i for i in items if i.get("id") in (mistake_ids or [])]
    if not sel:
        sel = [i for i in items if not i.get("mastered")][-3:]
    if not sel:
        return None

    grade = memory.get_student_profile().get("年级", "") or "小学"
    mtext = "\n".join(
        f"- 题目：{i.get('question', '')}　正确答案：{i.get('correct_answer', '') or '未知'}　易错点：{i.get('note', '')}"
        for i in sel
    )
    resp = adapter.generate(
        messages=[{"role": "user", "content": _GEN_PROMPT.format(count=count, grade=grade, mistakes=mtext)}],
        max_tokens=4096,
    )
    questions = _parse_questions(resp.content or "")
    if not questions:
        return None

    qz = {
        "id": uuid.uuid4().hex[:8],
        "mistake_ids": [i["id"] for i in sel],
        "questions": questions,
        "answers": {},
        "passed": False,
        "created_at": time.time(),
    }
    d = _load(user_dir)
    d[qz["id"]] = qz
    _save(d, user_dir)
    return qz


def get_quiz(quiz_id: str, user_dir: str = "~/.lebotclaw") -> dict | None:
    return _load(user_dir).get(quiz_id)


def public_quiz(qz: dict) -> dict:
    """不含答案的试卷视图（给前端渲染）。"""
    return {
        "id": qz["id"],
        "questions": [{"q": q["q"], "options": q["options"]} for q in qz["questions"]],
        "answered": qz.get("answers", {}),
        "passed": qz.get("passed", False),
    }


def answer_question(quiz_id: str, q_index: int, choice: str, user_dir: str = "~/.lebotclaw") -> dict | None:
    """判分。全部答完且全对 → 关联错题自动标记已掌握。"""
    d = _load(user_dir)
    qz = d.get(quiz_id)
    if not qz or q_index >= len(qz["questions"]):
        return None
    q = qz["questions"][q_index]
    correct = choice.strip().upper() == q["answer"]
    qz["answers"][str(q_index)] = {"choice": choice.strip().upper(), "correct": correct}

    finished = len(qz["answers"]) >= len(qz["questions"])
    passed = finished and all(a["correct"] for a in qz["answers"].values())
    if passed and not qz.get("passed"):
        qz["passed"] = True
        store = _mistake_store(user_dir)
        for mid in qz.get("mistake_ids", []):
            store.update(mid, mastered=True)
    _save(d, user_dir)
    return {
        "correct": correct,
        "answer": q["answer"],
        "explain": q.get("explain", ""),
        "finished": finished,
        "passed": passed,
    }
