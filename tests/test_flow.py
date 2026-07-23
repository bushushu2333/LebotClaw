"""Tests for core.flow (Flow 可见, spec P1 1.1-1.6) + stream_events flow 分支。"""
import json

import pytest

from lebotclaw.core.agent import Agent
from lebotclaw.core.flow import (
    FlowEngine,
    FlowNode,
    FlowRun,
    NodeStatus,
    extract_knowledge_points,
)
from lebotclaw.core.memory import MemoryStore
from lebotclaw.core.router import IntentRouter
from lebotclaw.adapters.base import ModelAdapter, ModelResponse
from lebotclaw.tools.registry import ToolRegistry
from lebotclaw.tools.builtin.calculator import CalculatorTool


class FakeAdapter(ModelAdapter):
    """按脚本依次返回 ModelResponse 的假 adapter（stream_deltas 走基类兜底）。"""

    def __init__(self, responses):
        super().__init__(model_name="fake")
        self._responses = list(responses)
        self.calls = []

    def generate(self, messages, tools=None, temperature=0.7, max_tokens=2048):
        self.calls.append({"messages": messages, "tools": tools})
        if self._responses:
            return self._responses.pop(0)
        return ModelResponse(content="好的。")

    def stream(self, messages, tools=None, temperature=0.7, max_tokens=2048):
        resp = self.generate(messages=messages, tools=tools,
                             temperature=temperature, max_tokens=max_tokens)
        yield resp.content or ""


def _text(s):
    return ModelResponse(content=s)


def _tool(name="calculator", args=None, call_id="call_1"):
    return ModelResponse(content="", tool_calls=[
        {"id": call_id, "tool_name": name,
         "arguments": args if args is not None else {"expression": "1+1"}},
    ])


@pytest.fixture()
def engine(tmp_path):
    return FlowEngine(router=IntentRouter(), user_dir=str(tmp_path))


@pytest.fixture()
def agent(tmp_path):
    tools = ToolRegistry()
    tools.register(CalculatorTool())
    return Agent(
        name="test_flow",
        system_prompt="你是测试助手。",
        tools=tools,
        memory=MemoryStore(db_path=str(tmp_path / "mem.db")),
        user_dir=str(tmp_path),
    )


# ---------- 1.1 数据模型与序列化 ----------

class TestFlowModel:
    def test_node_round_trip(self):
        node = FlowNode(id=2, title="例题练习", description="做题",
                        tools_needed=["calculator"], status=NodeStatus.RUNNING,
                        note="做到一半")
        restored = FlowNode.from_dict(json.loads(json.dumps(node.to_dict())))
        assert restored == node

    def test_run_round_trip(self, engine):
        run = engine.create_run("帮我安排数学复习")
        run.replan_log.append({"ts": 1.0, "trigger": "太难了",
                               "action": "insert", "node": run.nodes[0].to_dict()})
        run.summary = "总结"
        run.completion_rate = 0.75
        run.skill_used = "fen-pizza"
        payload = json.dumps(run.to_dict(), ensure_ascii=False)
        restored = FlowRun.from_dict(json.loads(payload))
        assert restored.to_dict() == run.to_dict()
        assert restored.knowledge_points == run.knowledge_points
        assert restored.plan is None  # plan 引用不参与序列化

    def test_knowledge_point_extraction(self):
        assert "分数" in extract_knowledge_points("帮我复习分数")
        assert "数学" in extract_knowledge_points("帮我安排数学复习")
        assert extract_knowledge_points("今天天气怎么样") == []


# ---------- 1.2 触发判定 ----------

class TestTrigger:
    def test_plan_keywords_hit(self, engine):
        assert engine.should_trigger("帮我安排数学复习")
        assert engine.should_trigger("给我一个复习计划")
        assert engine.should_trigger("分数怎么学")
        assert engine.should_trigger("这道题分几个步骤")

    def test_intent_whitelist_hit(self, engine):
        assert engine.should_trigger("帮我写一篇关于春天的作文")  # text_creation
        assert engine.should_trigger("计算圆的面积")  # math_calculation

    def test_normal_qa_no_trigger(self, engine):
        assert not engine.should_trigger("今天天气怎么样")
        assert not engine.should_trigger("你好呀")
        assert not engine.should_trigger("什么是光合作用")  # knowledge_qa 不在白名单

    def test_bare_calculation_no_trigger(self, engine):
        assert not engine.should_trigger("3+4=?")
        assert not engine.should_trigger("12*8")

    def test_disabled_engine(self, tmp_path):
        eng = FlowEngine(router=IntentRouter(), user_dir=str(tmp_path), enabled=False)
        assert not eng.should_trigger("帮我安排数学复习")


# ---------- 1.3 状态机 ----------

class TestStateMachine:
    def test_create_run_from_planner(self, engine):
        run = engine.create_run("帮我安排数学复习")
        assert run.id.startswith("flow_")
        assert len(run.nodes) >= 4  # 复习模板 4 步
        assert run.knowledge_points  # 数学被抽出
        assert engine.active_run is run

    def test_advance_nodes(self, engine):
        run = engine.create_run("帮我安排数学复习")
        engine.start_node(run, 0)
        assert run.nodes[0].status == NodeStatus.RUNNING
        engine.complete_node(run, 0, note="回顾完毕")
        assert run.nodes[0].status == NodeStatus.DONE
        assert run.nodes[0].note == "回顾完毕"
        engine.skip_node(run, 1, note="已掌握")
        assert run.nodes[1].status == NodeStatus.SKIPPED
        engine.fail_node(run, 2, note="卡住了")
        assert run.nodes[2].status == NodeStatus.FAILED

    def test_finalize_summary_and_rate(self, engine):
        run = engine.create_run("帮我安排数学复习")
        total = len(run.nodes)
        engine.complete_node(run, 0)
        engine.complete_node(run, 1)
        for i in range(2, total):
            engine.skip_node(run, i)
        engine.finalize(run)
        assert run.completion_rate == 1.0
        assert "总结" not in run.summary or run.summary  # 模板拼接，不调 LLM
        assert str(total) in run.summary


# ---------- 1.5 replan 嫁接 ----------

class TestReplan:
    def test_frustrated_inserts_node(self, engine):
        run = engine.create_run("帮我安排数学复习")
        for i in range(len(run.nodes)):
            engine.complete_node(run, i)
        actions = engine.apply_replan(run, "太难了，我跟不上")
        assert [a["action"] for a in actions] == ["insert"]
        inserted = actions[0]["node"]
        assert inserted.status == NodeStatus.INSERTED
        assert "鼓励" in inserted.title
        assert run.replan_log[-1]["trigger"] == "太难了，我跟不上"
        assert run.replan_log[-1]["action"] == "insert"

    def test_confused_inserts_review_node(self, engine):
        run = engine.create_run("帮我安排数学复习")
        engine.complete_node(run, 0)
        actions = engine.apply_replan(run, "我不懂这一步")
        assert [a["action"] for a in actions] == ["insert"]
        assert "回退" in actions[0]["node"].title

    def test_positive_skips_node(self, engine):
        run = engine.create_run("帮我安排数学复习")
        actions = engine.apply_replan(run, "我都会了，太简单")
        assert [a["action"] for a in actions] == ["skip"]
        skipped = actions[0]["node"]
        assert skipped.status == NodeStatus.SKIPPED
        assert run.replan_log[-1]["action"] == "skip"

    def test_is_replan_feedback(self, engine):
        assert engine.is_replan_feedback("太难了")
        assert engine.is_replan_feedback("这道题我不会")
        assert not engine.is_replan_feedback("帮我安排数学复习")
        assert not engine.is_replan_feedback("今天天气不错")

    def test_replan_on_deserialized_run(self, engine):
        run = engine.create_run("帮我安排数学复习")
        restored = FlowRun.from_dict(json.loads(
            json.dumps(run.to_dict(), ensure_ascii=False)))
        actions = engine.apply_replan(restored, "太难了")  # plan=None → 重建
        assert actions and actions[0]["action"] == "insert"


# ---------- 1.6 归档 ----------

class TestArchive:
    def test_archive_appends_jsonl(self, engine, tmp_path):
        run = engine.create_run("帮我复习分数")
        engine.finalize(run)
        engine.archive(run)
        engine.archive(run)  # 追加而非覆盖
        path = tmp_path / "flow_runs.jsonl"
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        record = json.loads(lines[0])
        assert record["id"] == run.id
        assert "分数" in record["knowledge_points"]
        assert "archived_at" in record


# ---------- stream_events 挂 flow_engine ----------

def _flow_events(agent, engine, user_input):
    return list(agent.stream_events(user_input, flow_engine=engine))


class TestStreamEventsFlow:
    def test_full_flow_event_sequence(self, agent, tmp_path):
        engine = FlowEngine(router=IntentRouter(), user_dir=str(tmp_path))
        agent.model_adapter = FakeAdapter([_text("回顾。"), _text("练习。"),
                                           _text("巩固。"), _text("总结。")])
        events = _flow_events(agent, engine, "帮我安排数学复习")
        types = [e["type"] for e in events]
        assert types[0] == "plan"
        assert types[-1] == "flow_done"
        assert "step" in types and "delta" in types
        assert "tool" not in types  # flow 模式不出旧 tool 事件

        plan = events[0]
        assert plan["goal"] == "帮我安排数学复习"
        assert len(plan["nodes"]) == 4  # 复习模板
        assert plan["knowledge_points"]

        running = [e for e in events if e["type"] == "step" and e["status"] == "running"]
        done = [e for e in events if e["type"] == "step" and e["status"] == "done"]
        assert len(running) == len(done) == 4
        assert running[0]["index"] == 0 and running[0]["total"] == 4

        flow_done = events[-1]
        assert flow_done["completion_rate"] == 1.0
        assert flow_done["summary"]
        assert flow_done["knowledge_points"] == plan["knowledge_points"]

        # 归档
        archive = (tmp_path / "flow_runs.jsonl").read_text(encoding="utf-8")
        assert json.loads(archive.strip())["knowledge_points"]

        # 每个节点都注入了「当前在执行第 N 步」
        systems = [c["messages"][0]["content"] for c in agent.model_adapter.calls]
        assert any("当前在执行第 1 步" in s for s in systems)
        assert any("当前在执行第 4 步" in s for s in systems)

    def test_tool_round_event(self, agent, tmp_path):
        engine = FlowEngine(router=IntentRouter(), user_dir=str(tmp_path))
        agent.model_adapter = FakeAdapter([
            _text("回顾。"),
            _tool(),            # 第 2 节点：模型要求工具
            _text("算完练习。"),
            _text("巩固。"),
            _text("总结。"),
        ])
        events = _flow_events(agent, engine, "帮我安排数学复习")
        tool_rounds = [e for e in events if e["type"] == "tool_round"]
        assert len(tool_rounds) == 1
        tr = tool_rounds[0]
        assert tr["round"] == 1
        assert tr["node_id"] == 1
        assert tr["tool"] == "calculator"
        assert tr["success"] is True
        assert "calculator" in tr["reason"]
        assert "input" in tr and "output" in tr
        assert not any(e["type"] == "tool" for e in events)

    def test_replan_continuation_flow(self, agent, tmp_path):
        engine = FlowEngine(router=IntentRouter(), user_dir=str(tmp_path))
        agent.model_adapter = FakeAdapter([_text("一。"), _text("二。"),
                                           _text("三。"), _text("四。"),
                                           _text("别急，换个简单的方法。")])
        _flow_events(agent, engine, "帮我安排数学复习")  # 第一轮：完整 flow
        events = _flow_events(agent, engine, "太难了，我跟不上")
        types = [e["type"] for e in events]
        assert "replanned" in types
        replanned = [e for e in events if e["type"] == "replanned"]
        assert replanned[0]["action"] == "insert"
        assert replanned[0]["trigger"] == "太难了，我跟不上"
        assert replanned[0]["node"]["status"] == "inserted"
        assert types[-1] == "flow_done"
        # 两次 flow_done → 归档两行
        lines = (tmp_path / "flow_runs.jsonl").read_text(
            encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

    def test_normal_qa_zero_extra_events(self, agent, tmp_path):
        engine = FlowEngine(router=IntentRouter(), user_dir=str(tmp_path))
        agent.model_adapter = FakeAdapter([_text("你好！")])
        events = _flow_events(agent, engine, "你好呀")
        assert {e["type"] for e in events} == {"delta"}
        assert not (tmp_path / "flow_runs.jsonl").exists()

    def test_no_flow_engine_behavior_unchanged(self, agent):
        agent.model_adapter = FakeAdapter([_tool(), _text("结果是 2。")])
        events = list(agent.stream_events("1+1等于几"))
        types = {e["type"] for e in events}
        assert "tool" in types  # 旧 tool 事件保留
        assert "tool_round" not in types
        assert "plan" not in types

    def test_flow_disabled_falls_back(self, agent, tmp_path):
        engine = FlowEngine(router=IntentRouter(), user_dir=str(tmp_path))
        agent.flow_enabled = False
        agent.model_adapter = FakeAdapter([_text("普通回答。")])
        events = _flow_events(agent, engine, "帮我安排数学复习")
        assert {e["type"] for e in events} == {"delta"}
