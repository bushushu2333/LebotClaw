from lebotclaw.core.agent import Agent
from lebotclaw.tools.registry import ToolRegistry
from lebotclaw.tools.builtin.dictionary import DictionaryTool
from lebotclaw.tools.builtin.knowledge import KnowledgeTool
from lebotclaw.tools.builtin.wordbank import WordBankTool
from lebotclaw.education.heads import HEADSTemplate


class EnglishAgent:

    @staticmethod
    def create(model_adapter=None, memory=None) -> Agent:
        tools = ToolRegistry()
        tools.register(DictionaryTool())
        tools.register(KnowledgeTool())
        tools.register(WordBankTool())
        return Agent(
            name="english",
            system_prompt=HEADSTemplate.english_prompt(),
            tools=tools,
            model_adapter=model_adapter,
            memory=memory,
        )


def create_english_agent(model_adapter=None, memory=None) -> Agent:
    return EnglishAgent.create(model_adapter=model_adapter, memory=memory)
