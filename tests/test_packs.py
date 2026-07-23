"""CapabilityPack 协议测试 — spec FR-L1：底座只认协议、pack 可插拔。"""
from lebotclaw.packs.base import BasePack
from lebotclaw.packs.k12 import K12Pack
from lebotclaw.packs.dummy import DummyPack


def test_base_pack_safe_defaults():
    p = BasePack()
    assert p.personas() == {}
    assert p.intent_rules() == {}
    assert p.plan_templates() == {}
    assert p.tool_factories() == {}
    assert p.skill_templates() == {}
    assert p.quest_copy() == {}
    assert p.soul_master() is None


def test_k12_pack_four_elements():
    p = K12Pack()
    personas = p.personas()
    assert set(personas) >= {"math", "chinese", "english", "science", "general"}
    assert "超级小博" in personas["math"]
    assert "math" in p.intent_rules()
    templates = p.plan_templates()
    assert "review" in templates and "learn" in templates
    tools = p.tool_factories()
    assert tools["math"]  # 数学伙伴有工具
    names = [t.name for t in tools["math"]]
    assert "calculator" in names


def test_dummy_pack_smoke():
    p = DummyPack()
    assert p.personas()["general"]
    assert len(p.plan_templates()["generic"]) == 3
    # 空工具集也能成立（可插拔不强制四要素全满）
    assert p.tool_factories() == {}


def test_packs_are_interchangeable():
    """两个 pack 满足同一鸭子协议：对底座暴露完全相同的接口面。"""
    for pack in (K12Pack(), DummyPack(), BasePack()):
        for method in ("personas", "intent_rules", "plan_templates",
                       "tool_factories", "skill_templates", "quest_copy"):
            assert callable(getattr(pack, method))
