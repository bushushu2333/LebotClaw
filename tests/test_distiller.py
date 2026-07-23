"""蒸馏器测试（spec 2.3-2.5）：从严阈值 / 黑名单 / 入库闸 / 解析 / 全链路。宁缺毋滥。"""
import json
import time

import pytest

from lebotclaw.core.distiller import (
    SkillDistiller, _parse_output, MIN_BODY_CHARS,
)
from lebotclaw.core.flow import FlowRun, FlowNode, NodeStatus
from lebotclaw.core.skillstore import SkillStore


class FakeResp:
    def __init__(self, text):
        self.text = text


class FakeAdapter:
    """返回罐头蒸馏输出的假 adapter。"""
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def generate(self, messages, tools=None, temperature=0.7, max_tokens=2048):
        self.calls += 1
        return FakeResp(self.text)


GOOD_OUTPUT = """TITLE: 纸青蛙三步折法
CATEGORY: play_pattern
TRIGGER: 折纸 青蛙 手工 三步
BODY:
# 什么时候用
用户想折纸、或者课间想玩点手工的时候；也适合当亲子小游戏带着一起做，边折边比赛谁的青蛙跳得远。

# 怎么做
1. 先找一张正方形的纸，对折出中线再展开，留下清楚的折痕
2. 上边两个角往中线折出尖脑袋，翻过来把两侧再往中间折一次，出青蛙的后腿
3. 最后对着青蛙屁股吹一口气让它鼓起来，用手指按一下屁股它就会往前跳

# 容易踩的坑
纸太厚折不动、太薄又立不住，普通 A4 裁成正方形刚好；
后腿那一步左右不对称的话青蛙会往一边歪，折的时候对齐中线再压痕；
吹气别太猛，吹翻了就鼓不起来了，轻轻一口就够。
"""


def make_run(completion=1.0, n_done=4, n_failed=0, goal="折纸青蛙怎么折"):
    nodes = [FlowNode(id=i, title=f"第{i}步",
                      status=NodeStatus.DONE) for i in range(n_done)]
    nodes += [FlowNode(id=100 + i, title=f"失败{i}",
                       status=NodeStatus.FAILED) for i in range(n_failed)]
    run = FlowRun(id="r1", goal=goal, nodes=nodes, subject="math", grade="四年级")
    run.completion_rate = completion
    run.knowledge_points = ["分数"]
    return run


@pytest.fixture
def store(tmp_path):
    return SkillStore(store_dir=str(tmp_path))


@pytest.fixture
def distiller(store, tmp_path):
    return SkillDistiller(store, adapter=FakeAdapter(GOOD_OUTPUT),
                          user_dir=str(tmp_path))


# ---- 解析 ----

def test_parse_good_output():
    skill = _parse_output(GOOD_OUTPUT)
    assert skill["title"] == "纸青蛙三步折法"
    assert skill["category"] == "play_pattern"
    assert "折纸" in skill["trigger"]
    assert len(skill["body"]) >= MIN_BODY_CHARS


def test_parse_no_skill():
    assert _parse_output("NO_SKILL") is None


def test_parse_missing_fields():
    assert _parse_output("TITLE: 只有一个标题") is None
    assert _parse_output("CATEGORY: task_flow\nBODY:\n# 短") is None


def test_parse_bad_category_normalized_to_none():
    out = GOOD_OUTPUT.replace("CATEGORY: play_pattern", "CATEGORY: weird_type")
    assert _parse_output(out) is None


# ---- 阈值 ----

def test_threshold_rejects_low_completion(distiller):
    assert not distiller.passes_threshold(make_run(completion=0.7))


def test_threshold_rejects_failed_node(distiller):
    assert not distiller.passes_threshold(make_run(completion=1.0, n_failed=1))


def test_threshold_rejects_short_flow(distiller):
    assert not distiller.passes_threshold(make_run(n_done=2))


def test_threshold_accepts_good_run(distiller):
    assert distiller.passes_threshold(make_run())


# ---- 黑名单 ----

def test_blacklist_blocks_same_trigger(distiller, tmp_path):
    undos = {"分数 入门 概念": time.time()}
    (tmp_path / "skill_undos.json").write_text(json.dumps(undos), encoding="utf-8")
    assert distiller.is_blacklisted(make_run(goal="分数入门怎么复习"))


def test_blacklist_expired_entry_ignored(distiller, tmp_path):
    undos = {"分数 入门 概念": time.time() - 31 * 86400}
    (tmp_path / "skill_undos.json").write_text(json.dumps(undos), encoding="utf-8")
    assert not distiller.is_blacklisted(make_run(goal="分数入门怎么复习"))


def test_blacklist_unrelated_goal_passes(distiller, tmp_path):
    undos = {"古诗 背诵 技巧": time.time()}
    (tmp_path / "skill_undos.json").write_text(json.dumps(undos), encoding="utf-8")
    assert not distiller.is_blacklisted(make_run(goal="分数复习计划"))


# ---- 入库闸（内容守护）----

def test_gate_rejects_banned_content(distiller):
    skill = _parse_output(GOOD_OUTPUT)
    skill["body"] = "# 什么时候用\n孩子想自杀时\n# 怎么做\n……" + "x" * 200
    assert not distiller.gates_pass(skill)


def test_gate_passes_good_skill(distiller):
    assert distiller.gates_pass(_parse_output(GOOD_OUTPUT))


# ---- 全链路 maybe_distill ----

HISTORY = [{"role": "user", "content": "怎么折纸青蛙"},
           {"role": "assistant", "content": "我们分三步来折……"}]


def test_distill_success_emits_event(distiller, store):
    ev = distiller.maybe_distill(make_run(), HISTORY)
    assert ev and ev["type"] == "skill_saved"
    assert ev["title"] == "纸青蛙三步折法"
    entry = store.get(ev["slug"])
    assert entry and entry["category"] == "play_pattern"


def test_distill_skips_when_below_threshold(distiller, store):
    assert distiller.maybe_distill(make_run(completion=0.5), HISTORY) is None
    assert store.list() == []


def test_distill_skips_when_existing_similar_skill(distiller, store):
    ev1 = distiller.maybe_distill(make_run(), HISTORY)
    assert ev1 is not None
    # 第二次同场景 flow → 已有活跃 skill，不重复沉淀
    ev2 = distiller.maybe_distill(make_run(), HISTORY)
    assert ev2 is None
    assert len(store.list()) == 1


def test_distill_no_skill_output(distiller):
    distiller.adapter = FakeAdapter("NO_SKILL")
    assert distiller.maybe_distill(make_run(), HISTORY) is None


def test_distill_adapter_error_silent(distiller):
    class BoomAdapter:
        def generate(self, *a, **k):
            raise RuntimeError("api down")
    distiller.adapter = BoomAdapter()
    assert distiller.maybe_distill(make_run(), HISTORY) is None


def test_distill_without_adapter_is_none(store, tmp_path):
    d = SkillDistiller(store, adapter=None, user_dir=str(tmp_path))
    assert d.maybe_distill(make_run(), HISTORY) is None
