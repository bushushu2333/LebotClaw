"""纯文本通道透传测试（spec 1.9）：collapse_events_to_text 折叠 Flow/Skill/陪伴事件。"""
import threading
import time

from lebotclaw.web.chat_bridge import collapse_events_to_text


class FakeWorkspace:
    def touch_companion(self):
        return {}

    def check_day_milestone(self):
        return None

    def add_tokens(self, n):
        return None


class FakeAgent:
    def __init__(self, events):
        self._events = events

    def stream_events(self, user_input, extra_system="", flow_engine=None):
        yield from self._events


class FakeCtx:
    def __init__(self, events):
        self.lock = threading.RLock()
        self.uid = ""
        self.workspace = FakeWorkspace()
        self.flow_engine = None
        self.active_agent = FakeAgent(events)

    def active_name(self):
        return "math"

    def classify_and_route(self, text):
        pass


def test_collapse_plain_chat():
    ctx = FakeCtx([{"type": "delta", "text": "你好"}, {"type": "delta", "text": "呀"}])
    assert collapse_events_to_text(ctx, "hi") == "你好呀"


def test_collapse_flow_events_to_text_lines():
    ctx = FakeCtx([
        {"type": "plan", "goal": "分数复习", "nodes": [{"title": "回顾"}, {"title": "练习"}]},
        {"type": "step", "node_id": 0, "status": "running"},
        {"type": "delta", "text": "先回顾知识点。"},
        {"type": "flow_done", "summary": "本次共 2 步：完成 2 步。"},
        {"type": "skill_saved", "slug": "s1", "title": "披萨讲分数"},
    ])
    text = collapse_events_to_text(ctx, "帮我复习分数")
    assert "🗺️ 计划「分数复习」：回顾 → 练习" in text
    assert "🎉 本次共 2 步：完成 2 步。" in text
    assert "💾 小博学会了新本领「披萨讲分数」" in text
    assert text.endswith("先回顾知识点。")
    # step 中间事件不刷屏
    assert "running" not in text


def test_collapse_companion_milestone():
    ctx = FakeCtx([
        {"type": "companion_milestone", "kind": "days", "value": 30},
        {"type": "delta", "text": "早！"},
    ])
    text = collapse_events_to_text(ctx, "早")
    assert "第 30 天" in text and "早！" in text
