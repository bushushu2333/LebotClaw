"""工具重试风暴防护 + knowledge 未命中语义（2026-07-23 事故修复）。

事故：Flow 模式下知识库未命中返回 success=False → 模型当故障反复重试
（3 轮 × 3 次并行调用刷屏）。修复：① 未命中改 success=True+指引；
② 整轮全败且与上轮同工具 → 摘工具清单强制直接回答。
"""
from lebotclaw.core.agent import Agent
from lebotclaw.core.memory import MemoryStore
from lebotclaw.tools.builtin.knowledge import KnowledgeTool
from lebotclaw.tools.registry import ToolRegistry


def test_knowledge_no_hit_is_success_with_guidance():
    r = KnowledgeTool().execute(query="不存在的知识点xyz")
    assert r.success is True
    assert "不要再调用本工具" in r.output


class StormAdapter:
    """永远要求调同一个工具的思考模型（模拟死磕）。"""

    def __init__(self):
        self.calls = []
        self.round = 0

    def stream_deltas(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        self.calls.append({"tools_offered": tools is not None,
                           "msg_count": len(messages)})
        if tools is None:
            # 摘掉工具后：必须直接回答
            yield ("text", "直接回答的内容")
            return
        self.round += 1
        yield ("tool_calls", [{
            "id": "c1", "tool_name": "broken_tool", "arguments": "{}"}])


class BrokenTool:
    name = "broken_tool"
    description = "always fails"
    parameters = {"type": "object", "properties": {}}

    def to_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.parameters}}

    def execute(self, **kw):
        from lebotclaw.tools.base import ToolResult
        return ToolResult(success=False, output="", error="boom")


def test_storm_guard_strips_tools_after_repeat_failure():
    tools = ToolRegistry()
    tools.register(BrokenTool())
    adapter = StormAdapter()
    agent = Agent(name="math", system_prompt="test", tools=tools,
                  model_adapter=adapter, memory=MemoryStore())
    events = list(agent.stream_events("测试问题"))
    deltas = "".join(e.get("text", "") for e in events if e.get("type") == "delta")
    assert "直接回答的内容" in deltas
    # 关键断言：模型死磕两轮后，有一轮是在 tools=None 下进行的（强制回答）
    assert any(not c["tools_offered"] for c in adapter.calls)
    # 且工具调用轮数被按住（不摘工具的话会顶满 3 轮 + 兜底 1 轮全在带工具状态）
    tool_events = [e for e in events if e.get("type") == "tool"]
    assert len(tool_events) <= 3
