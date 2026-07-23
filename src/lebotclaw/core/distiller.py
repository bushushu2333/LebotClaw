"""Skill 蒸馏器（spec 2.3-2.5）：一次高质量 Flow 跑完 → 判断是否值得沉淀 →
LLM 蒸馏成 SKILL.md 文件包 → 内容守护闸 → 自动入库。

铁律：**宁缺毋滥**。阈值不达标、黑名单、守护闸不过、LLM 输出解析失败——
任何一环不过都静默放弃（返回 None），绝不影响正常对话。

调用点：agent._stream_flow 在 flow_done 之后调用 maybe_distill()，
拿到事件 dict 则 yield（前端「小博学会了新本领」卡片，带查看/撤销）。
"""
import json
import re
import time
from pathlib import Path
from typing import Optional

from lebotclaw.core.skillstore import VALID_CATEGORIES

# ---- 从严阈值（spec C2 全自动）----
MIN_COMPLETION = 0.85     # flow 完成率
MIN_STEPS_DONE = 3        # 至少完整走完 3 步（太短没有沉淀价值）
MIN_BODY_CHARS = 200      # 蒸馏正文下限（过短=没蒸出干货）
MAX_HISTORY_CHARS = 3000  # 喂给蒸馏模型的历史上限

_DISTILL_PROMPT = """你是小博（一个陪小朋友的通用智能体）的本领提炼器。下面是小博陪用户完成一件事的完整过程。
请判断这次过程中是否有一个**可复用的好做法**，如果有，提炼成一个 SKILL（本领卡）。

【任务目标】{goal}
【执行步骤与结果】
{nodes}
【对话片段（节选）】
{history}

要求：
1. 只提炼**真的可复用**的做法（换个任务/换个人还能用）。一次性闲聊、纯知识问答不要提炼——直接输出 NO_SKILL。
2. 分类二选一：task_flow（可复用的做事流程）或 play_pattern（可复用的互动玩法/游戏套路）。
3. 用小朋友看得懂的说法，像小博自己记笔记一样写。
4. 严格按以下格式输出（不要输出多余内容）：

TITLE: 10 字以内的本领名称
CATEGORY: task_flow 或 play_pattern
TRIGGER: 触发关键词（空格分隔，3-6 个）
BODY:
（Markdown 正文，包含：# 什么时候用 / # 怎么做 / # 容易踩的坑 三个小节，总长度 200-600 字）

如果不值得沉淀，只输出：NO_SKILL"""


def _fmt_nodes(run) -> str:
    lines = []
    for n in run.nodes:
        lines.append("- [%s] %s%s" % (
            n.status.value, n.title,
            ("：" + n.note[:80]) if getattr(n, "note", "") else ""))
    return "\n".join(lines)


def _fmt_history(history: list) -> str:
    parts = []
    for msg in history[-8:]:
        role = {"user": "用户", "assistant": "小博"}.get(msg.get("role"), "")
        content = (msg.get("content") or "")[:400]
        if role and content:
            parts.append("%s：%s" % (role, content))
    text = "\n".join(parts)
    return text[-MAX_HISTORY_CHARS:]


def _parse_output(text: str) -> Optional[dict]:
    """解析蒸馏输出；任何字段缺失/不合格 → None（宁缺毋滥）。"""
    if not text or "NO_SKILL" in text[:200]:
        return None
    def _grab(key, following):
        m = re.search(r"^%s:\s*(.+?)(?=^%s|\Z)" % (key, following),
                      text, flags=re.M | re.S)
        return m.group(1).strip() if m else ""
    title = _grab("TITLE", "CATEGORY:|TRIGGER:|BODY:")
    category = _grab("CATEGORY", "TRIGGER:|BODY:")
    trigger = _grab("TRIGGER", "BODY:")
    m = re.search(r"^BODY:\s*\n(.+)\Z", text, flags=re.M | re.S)
    body = m.group(1).strip() if m else ""
    if not title or len(title) > 30:
        return None
    category = category if category in VALID_CATEGORIES else ""
    if not category:
        return None
    if len(body) < MIN_BODY_CHARS:
        return None
    return {"title": title, "category": category, "trigger": trigger,
            "body": body[:2000]}


class SkillDistiller:
    """从严蒸馏：阈值 → 黑名单 → LLM → 解析 → 双闸 → 入库。"""

    def __init__(self, skill_store, adapter=None, user_dir: str = "~/.lebotclaw"):
        self.store = skill_store
        self.adapter = adapter
        self.user_dir = str(user_dir)

    # ---- 各道闸（拆成小方法便于测试）----

    def passes_threshold(self, run) -> bool:
        """从严阈值：完成率>0.85 + 至少3步全完成 + 无失败节点。"""
        if run.completion_rate < MIN_COMPLETION:
            return False
        from lebotclaw.core.flow import NodeStatus
        done = sum(1 for n in run.nodes if n.status == NodeStatus.DONE)
        failed = sum(1 for n in run.nodes if n.status == NodeStatus.FAILED)
        return done >= MIN_STEPS_DONE and failed == 0

    def is_blacklisted(self, run) -> bool:
        """撤销黑名单（spec 2.5）：同 trigger 关键词 30 天内不再自动沉淀。"""
        path = Path(self.user_dir).expanduser() / "skill_undos.json"
        if not path.exists():
            return False
        try:
            undos = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        goal = run.goal or ""
        now = time.time()
        for trigger, ts in undos.items():
            if now - ts > 30 * 86400:
                continue
            words = [w for w in str(trigger).split() if w]
            # 黑名单 trigger 过半关键词命中 goal → 视为同一套路，跳过
            if words and sum(1 for w in words if w in goal) >= max(1, len(words) // 2 + 1):
                return True
            if trigger and trigger in goal:
                return True
        return False

    def gates_pass(self, skill: dict) -> bool:
        """入库闸：内容守护（moderation）——标题或正文命中违禁词 → 拒。"""
        from lebotclaw.core import moderation
        body = skill.get("body", "")
        title = skill.get("title", "")
        try:
            if moderation.check(title + "\n" + body).hit:
                return False
        except Exception:  # noqa: BLE001 — 守护模块异常时放行（不阻塞沉淀）
            pass
        return True

    # ---- 主入口 ----

    def maybe_distill(self, run, history: list) -> Optional[dict]:
        """全流程闸口。成功返回 skill_saved 事件 dict，否则 None。"""
        if self.adapter is None or self.store is None:
            return None
        if not self.passes_threshold(run) or self.is_blacklisted(run):
            return None
        # 同场景已有活跃 skill → 不重复沉淀（交给演化引擎打磨旧的）
        try:
            if self.store.find(scenario=run.goal):
                return None
        except Exception:  # noqa: BLE001
            pass
        prompt = _DISTILL_PROMPT.format(
            goal=run.goal, nodes=_fmt_nodes(run), history=_fmt_history(history))
        try:
            resp = self.adapter.generate(
                [{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=1200)
            text = getattr(resp, "text", "") or getattr(resp, "content", "") or ""
        except Exception:  # noqa: BLE001 — 蒸馏失败静默放弃
            return None
        skill = _parse_output(text)
        if skill is None or not self.gates_pass(skill):
            return None
        skill.update({
            "source": "internal",
            "effectiveness": 0.75,
        })
        try:
            slug = self.store.add(skill)
        except Exception:  # noqa: BLE001
            return None
        return {"type": "skill_saved", "slug": slug, "title": skill["title"],
                "category": skill["category"]}
