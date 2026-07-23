"""K12 能力包（spec A1）：re-export 现有模块，不搬文件、不改行为。

四要素来源：
- personas      ← education.heads.HEADSTemplate（5 学科人设）
- intent_rules  ← core.router 的关键词表
- plan_templates← core.planner 的 5 套模板
- tool_factories← tools.builtin 的教育工具
"""
from typing import Dict, List

from lebotclaw.packs.base import BasePack


class K12Pack(BasePack):
    name = "k12"
    display_name = "K12 学科伴学包"

    def personas(self) -> Dict[str, str]:
        from lebotclaw.education.heads import HEADSTemplate
        return {
            "math": HEADSTemplate.math_prompt(),
            "chinese": HEADSTemplate.chinese_prompt(),
            "english": HEADSTemplate.english_prompt(),
            "science": HEADSTemplate.science_prompt(),
            "general": HEADSTemplate.general_prompt(),
        }

    def intent_rules(self) -> Dict[str, List[str]]:
        from lebotclaw.core.router import _SUBJECT_KEYWORDS
        return dict(_SUBJECT_KEYWORDS)

    def plan_templates(self) -> Dict[str, List[Dict]]:
        from lebotclaw.core.planner import Planner
        return dict(Planner()._templates)

    def tool_factories(self) -> Dict[str, list]:
        from lebotclaw.tools.builtin.calculator import CalculatorTool
        from lebotclaw.tools.builtin.knowledge import KnowledgeTool
        from lebotclaw.tools.builtin.dictionary import DictionaryTool
        from lebotclaw.tools.builtin.timer import TimerTool
        from lebotclaw.tools.builtin.mistakebook import MistakeBookTool
        from lebotclaw.tools.builtin.wordbank import WordBankTool
        return {
            "math": [CalculatorTool(), KnowledgeTool(), MistakeBookTool()],
            "chinese": [DictionaryTool(), KnowledgeTool(), WordBankTool()],
            "english": [DictionaryTool(), KnowledgeTool(), WordBankTool()],
            "science": [KnowledgeTool()],
            "general": [TimerTool(), KnowledgeTool()],
        }
