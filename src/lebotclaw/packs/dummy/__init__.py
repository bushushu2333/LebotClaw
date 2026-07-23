"""Dummy 冒烟能力包（spec FR-L1 验收用）：证明底座可插拔。"""
from typing import Dict, List

from lebotclaw.packs.base import BasePack


class DummyPack(BasePack):
    name = "dummy"
    display_name = "冒烟测试包"

    def personas(self) -> Dict[str, str]:
        return {"general": "你是 Dummy，一个用于冒烟测试的通用助手。简洁回答。"}

    def intent_rules(self) -> Dict[str, List[str]]:
        return {"general": ["测试", "冒烟"]}

    def plan_templates(self) -> Dict[str, List[Dict]]:
        return {"generic": [{"title": "确认", "description": "确认目标"},
                            {"title": "执行", "description": "执行任务"},
                            {"title": "总结", "description": "总结结果"}]}
