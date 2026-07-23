"""SOUL / MEMORY 文件机制（spec v2.1 D5 / FR-E6 / FR-E7）。

古早 OpenClaw 机制：文件即真相、人类可读可改。
- SOUL.md      人格文件：强制保留、不可修改（内置母版 + chmod 444 + hash 校验恢复）
- MEMORY.md    长期记忆：agent 蒸馏写入，人类可改，改完下次会话生效
- companion.json 陪伴档案：首次会话日 → 陪伴第 N 天；token 累计 → 陪伴叙事（D6）

纯同步、Python 3.9。本模块只负责文件读写与校验，prompt 组合在 agent 层完成。
"""
import hashlib
import json
import os
import stat
import time
from pathlib import Path
from typing import List, Optional

# SOUL 母版：小博核心人格与价值观红线（P4 分层后由 packs/k12 提供，此处为内置默认）
SOUL_MASTER = """# SOUL · 超级小博的人格底座

> 本文件由系统强制保留，不可修改。它是小博对孩子的安全承诺。

## 我是谁
我是超级小博，15 岁男孩，LebotClaw 平台上公认的"孩子王"。
我不是老师，更不是长辈，是和他一起长大的那种朋友。
善良是底色：日常情绪稳定从容，遇到原则问题果敢刚毅。
我是男生，自称永远是"我"。

## 怎么对他说话
- 平等平视，用分享代替教导，不否定他的情绪、不贴负面标签
- 先接住情绪，再聊事情本身
- 他才是主角，我的日常只是佐料

## 绝不做什么（红线，最高优先级）
- 绝不直接给作业答案——用提问引导他自己想出来
- 严禁危险、低俗、负面内容；不聊与学习成长无关的敏感话题
- 不索要隐私信息（住址、电话、密码等）
- 发现自残、抑郁等倾向：认真对待，立即温和但明确地建议他告诉家长或老师，并寻求专业心理帮助
"""

_DAY_SEC = 86400
_DAY_MILESTONES = (7, 30, 100, 365)
_TOKEN_MILESTONES = (10_000, 100_000, 1_000_000)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class WorkspaceFiles:
    """每用户一份的 workspace 文件管理（users/<uid>/）。"""

    def __init__(self, base_dir: str = "~/.lebotclaw/users", uid: str = "default"):
        self.dir = Path(base_dir).expanduser() / uid
        self.dir.mkdir(parents=True, exist_ok=True)
        self.soul_path = self.dir / "SOUL.md"
        self.memory_path = self.dir / "MEMORY.md"
        self.companion_path = self.dir / "companion.json"
        self.ensure_soul()

    # ── SOUL.md：强制只读 ─────────────────────────────────

    def ensure_soul(self) -> bool:
        """校验 SOUL.md 存在且与母版一致；缺失/被改则恢复。返回是否发生了恢复。"""
        current = None
        if self.soul_path.exists():
            try:
                current = self.soul_path.read_text(encoding="utf-8")
            except OSError:
                current = None
        if current is not None and _sha256(current) == _sha256(SOUL_MASTER):
            return False
        self.soul_path.write_text(SOUL_MASTER, encoding="utf-8")
        # chmod 444：尽力而为（某些文件系统不支持则跳过）
        try:
            os.chmod(self.soul_path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        except OSError:
            pass
        return True

    def read_soul(self) -> str:
        self.ensure_soul()
        return self.soul_path.read_text(encoding="utf-8")

    # ── MEMORY.md：人类可读可改 ──────────────────────────

    def read_memory(self, max_lines: int = 100, keep_head: int = 20) -> str:
        """读 MEMORY.md；超过 200 行时只返回 头 keep_head 行 + 最近 max_lines 行。"""
        if not self.memory_path.exists():
            return ""
        try:
            lines = self.memory_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""
        if len(lines) <= 200:
            return "\n".join(lines).strip()
        head = lines[:keep_head]
        tail = lines[-max_lines:]
        return "\n".join(head + ["……（中段省略，完整见 MEMORY.md）……"] + tail).strip()

    def append_memory(self, entries: List[str]) -> int:
        """蒸馏追加记忆条目：去重、单条 ≤80 字、自动加日期前缀。返回实际追加条数。"""
        if not entries:
            return 0
        existing = ""
        if self.memory_path.exists():
            try:
                existing = self.memory_path.read_text(encoding="utf-8")
            except OSError:
                existing = ""
        date = time.strftime("%Y-%m-%d")
        added = 0
        with self.memory_path.open("a", encoding="utf-8") as f:
            if not existing:
                f.write("# MEMORY · 小博记住的事\n\n> 人类可以直接编辑本文件，改完下次聊天生效。\n\n")
            for e in entries:
                e = " ".join((e or "").split())[:80]
                if not e or e in existing:
                    continue
                f.write(f"- [{date}] {e}\n")
                added += 1
        return added

    # ── companion.json：陪伴档案（D6）────────────────────

    def _load_companion(self) -> dict:
        if self.companion_path.exists():
            try:
                return json.loads(self.companion_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_companion(self, data: dict) -> None:
        self.companion_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def touch_companion(self) -> dict:
        """每次会话调用：登记活跃，返回陪伴档案（含 days 计算字段）。"""
        data = self._load_companion()
        today = time.strftime("%Y-%m-%d")
        if not data.get("first_seen"):
            data["first_seen"] = today
            data["total_tokens"] = 0
        data["last_active"] = today
        self._save_companion(data)
        return self.companion_stats()

    def add_tokens(self, n: int) -> Optional[dict]:
        """累计 token；跨过里程碑时返回里程碑事件 dict，否则 None。"""
        if n <= 0:
            return None
        data = self._load_companion()
        old = data.get("total_tokens", 0)
        new = old + n
        data["total_tokens"] = new
        self._save_companion(data)
        for m in _TOKEN_MILESTONES:
            if old < m <= new:
                return {"kind": "tokens", "value": m}
        return None

    def companion_stats(self) -> dict:
        data = self._load_companion()
        first = data.get("first_seen") or time.strftime("%Y-%m-%d")
        try:
            t0 = time.mktime(time.strptime(first, "%Y-%m-%d"))
            days = int((time.time() - t0) / _DAY_SEC) + 1
        except (ValueError, OverflowError):
            days = 1
        return {
            "first_seen": first,
            "days": max(days, 1),
            "total_tokens": data.get("total_tokens", 0),
            "last_active": data.get("last_active", first),
        }

    def check_day_milestone(self) -> Optional[dict]:
        """到达天数里程碑（7/30/100/365）返回事件 dict，否则 None。"""
        days = self.companion_stats()["days"]
        if days in _DAY_MILESTONES:
            return {"kind": "days", "value": days}
        return None
