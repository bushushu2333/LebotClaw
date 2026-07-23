"""CapabilityPack 协议（spec v2.1 A1 / FR-L1）。

底层（flow/skill/workspace）只认本协议，不 import 任何具体能力包。
一个能力包 = 人设 prompt + 意图路由规则 + planner 模板 + 工具集（四要素），
外加可选的 skill 蒸馏模板与闯关文案（v2.1 新增两要素，默认空实现）。

实现约束：Python 3.9、纯同步。协议用鸭子类型约定（不强制继承），
但提供 BasePack 默认实现便于新包快速起步。
"""
from typing import Dict, List, Optional


class BasePack:
    """能力包基类：全部要素给安全默认（空），子类按需覆盖。"""

    name: str = "base"
    display_name: str = "基础能力包"

    # ── 四要素 ────────────────────────────────────────────

    def personas(self) -> Dict[str, str]:
        """agent 名 → system prompt。空 dict = 仅一个通用伙伴。"""
        return {}

    def intent_rules(self) -> Dict[str, List[str]]:
        """意图类目 → 关键词/正则列表（喂给 IntentRouter）。"""
        return {}

    def plan_templates(self) -> Dict[str, List[Dict]]:
        """planner 模板名 → 步骤模板列表（喂给 Planner）。"""
        return {}

    def tool_factories(self) -> Dict[str, list]:
        """agent 名 → 工具实例列表（已构造好的 Tool 对象）。"""
        return {}

    # ── v2.1 可选要素 ─────────────────────────────────────

    def skill_templates(self) -> Dict[str, str]:
        """skill 蒸馏 prompt 模板：类目（teaching_tactic/task_flow）→ prompt 模板。
        空 dict = 用 core 内置通用模板。"""
        return {}

    def quest_copy(self) -> Dict[str, str]:
        """闯关模式文案：事件类型 → 模板文案（{title} 等占位符）。
        空 dict = 用 core 内置默认文案。"""
        return {}

    def soul_master(self) -> Optional[str]:
        """SOUL.md 母版。None = 用 core 内置 SOUL_MASTER。"""
        return None
