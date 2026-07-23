"""演化引擎 + 外部源挂载 + ScriptSandbox 测试（spec 2.6/2.9/2.10，P3 3.1-3.6）。"""
import json
import time

import pytest

from lebotclaw.core.evolve import EvolveEngine, _bump_version, _similarity
from lebotclaw.core.sandbox import ScriptSandbox, ScriptSandboxDisabled
from lebotclaw.core.skillstore import SkillStore, load_external_dirs


class FakeResp:
    def __init__(self, text):
        self.text = text


class FakeAdapter:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def generate(self, messages, tools=None, temperature=0.7, max_tokens=2048):
        self.calls += 1
        return FakeResp(self.text)


POLISHED_BODY = ("# 适用场景\n打磨后的适用场景描述，覆盖更多变体情况。"
                 + "这是一段足够长的打磨后正文，" * 12
                 + "\n# 引导步骤\n1. 第一步\n2. 第二步\n# 常见卡点与对策\n卡点对策。")

BODY_A = ("# 适用场景\n孩子第一次接触分数时。\n# 引导步骤\n1. 画披萨\n2. 切块\n"
          "# 常见卡点与对策\n份数和大小混淆。" + "补充内容。" * 20)


@pytest.fixture
def store(tmp_path):
    return SkillStore(store_dir=str(tmp_path))


def _add_skill(store, title="分数披萨法", body=BODY_A, **kw):
    data = {"title": title, "trigger": "分数 披萨", "category": "teaching_tactic",
            "body": body, "effectiveness": 0.8}
    data.update(kw)
    return store.add(data)


def _log_usage(store, slug, n=5, eff=0.75, variant=""):
    for _ in range(n):
        store.record_usage(slug, outcome={"via": "chat"}, variant=variant,
                           effectiveness=eff)


# ---- 触发判定 ----

def test_bump_version():
    assert _bump_version("1.0.0") == "1.0.1"
    assert _bump_version("2.3.9") == "2.3.10"
    assert _bump_version("bad") == "1.0.1"


def test_should_evolve_at_every_5(store):
    slug = _add_skill(store)
    _log_usage(store, slug, n=5)
    engine = EvolveEngine(store)
    assert engine.should_evolve(store.get(slug))


def test_should_not_evolve_below_5(store):
    slug = _add_skill(store)
    _log_usage(store, slug, n=3)
    assert not EvolveEngine(store).should_evolve(store.get(slug))


def test_should_evolve_on_effectiveness_delta(store):
    slug = _add_skill(store)
    store.update_fields(slug, effectiveness_at_last_evolve=0.8)
    _log_usage(store, slug, n=2, eff=0.2)  # 滚存后 effectiveness 大跌
    assert EvolveEngine(store).should_evolve(store.get(slug))


def test_cooldown_blocks_evolve(store):
    slug = _add_skill(store)
    store.update_fields(slug, evolve_cooldown_until=time.time() + 1000)
    _log_usage(store, slug, n=5)
    assert not EvolveEngine(store).should_evolve(store.get(slug))


# ---- 打磨 ----

def test_polish_bumps_version_and_archives(store):
    slug = _add_skill(store)
    _log_usage(store, slug, n=5, variant="用蛋糕代替披萨")
    engine = EvolveEngine(store, adapter=FakeAdapter(POLISHED_BODY))
    ev = engine.on_usage(slug)
    assert ev and ev["type"] == "skill_evolved" and ev["action"] == "polish"
    entry = store.get(slug)
    assert entry["version"] == "1.0.1"
    assert "打磨后" in entry["body"]
    # 旧版归档
    versions = list((store.skills_dir / slug / "versions").glob("v*.md"))
    assert versions and "披萨" in versions[0].read_text(encoding="utf-8")


def test_keep_output_leaves_body_unchanged(store):
    slug = _add_skill(store)
    _log_usage(store, slug, n=5)
    engine = EvolveEngine(store, adapter=FakeAdapter("KEEP"))
    assert engine.on_usage(slug) is None
    assert store.get(slug)["body"] == BODY_A
    assert store.get(slug)["version"] == "1.0.0"


def test_adapter_error_silent(store):
    class Boom:
        def generate(self, *a, **k):
            raise RuntimeError("down")
    slug = _add_skill(store)
    _log_usage(store, slug, n=5)
    assert EvolveEngine(store, adapter=Boom()).on_usage(slug) is None


def test_no_adapter_no_evolve(store):
    slug = _add_skill(store)
    _log_usage(store, slug, n=5)
    assert EvolveEngine(store, adapter=None).on_usage(slug) is None


# ---- 回滚 ----

def test_rollback_after_low_streak(store):
    slug = _add_skill(store)
    _log_usage(store, slug, n=5)
    engine = EvolveEngine(store, adapter=FakeAdapter(POLISHED_BODY))
    engine.on_usage(slug)  # v1.0.1，v1.0.0 归档
    assert store.get(slug)["version"] == "1.0.1"
    _log_usage(store, slug, n=3, eff=0.2)  # 连续 3 次低分
    ev = engine.on_usage(slug)
    assert ev and ev["action"] == "rollback"
    entry = store.get(slug)
    assert entry["version"] == "1.0.0"
    assert "披萨" in entry["body"]
    assert float(entry["evolve_cooldown_until"]) > time.time()


def test_no_rollback_without_versions(store):
    slug = _add_skill(store)
    _log_usage(store, slug, n=3, eff=0.2)
    assert EvolveEngine(store).on_usage(slug) is None


# ---- 合并 ----

def test_merge_similar_skills(store):
    slug1 = _add_skill(store, title="分数披萨法")
    slug2 = _add_skill(store, title="分数切饼法", body=BODY_A)  # 正文几乎相同
    _log_usage(store, slug1, n=20)  # 触发合并体检
    ev = EvolveEngine(store).maybe_merge()
    assert ev and ev["action"] == "merge"
    loser = store.get(slug2)
    assert loser["status"] == "deprecated"
    assert loser["merged_into"] == slug1


def test_no_merge_for_dissimilar(store):
    _add_skill(store, title="分数披萨法")
    _add_skill(store, title="古诗背诵技巧",
               body="# 适用场景\n背古诗。\n# 步骤\n1. 朗读\n2. 想象画面。" + "完全不同。" * 30)
    assert EvolveEngine(store).maybe_merge() is None


# ---- 外部源挂载 ----

@pytest.fixture
def ext_dir(tmp_path):
    ext = tmp_path / "ext_skills"
    pkg = ext / "my-cool-skill"
    pkg.mkdir(parents=True)
    (pkg / "SKILL.md").write_text(
        "---\nname: my-cool-skill\ntitle: 外部酷炫技能\ntrigger: 酷炫 外部\n"
        "category: task_flow\n---\n\n# 步骤\n这是外部 skill 的正文。\n",
        encoding="utf-8")
    return ext


def test_external_mounted_readonly(store, ext_dir):
    s = SkillStore(store_dir=str(store.store_dir), external_dirs=[str(ext_dir)])
    entries = s.list()
    ext_entries = [e for e in entries if e["slug"].startswith("ext-")]
    assert len(ext_entries) == 1
    assert ext_entries[0]["source"] == "external"
    assert ext_entries[0]["title"] == "外部酷炫技能"
    # 正文可读
    full = s.get("ext-my-cool-skill")
    assert full and "外部 skill 的正文" in full["body"]
    # 只读：改/删/记复用全部拒绝
    assert not s.update_fields("ext-my-cool-skill", title="改名")
    assert not s.delete("ext-my-cool-skill")
    assert not s.record_usage("ext-my-cool-skill", effectiveness=0.9)
    # 原文件原样
    assert "外部酷炫技能" in (ext_dir / "my-cool-skill" / "SKILL.md").read_text(encoding="utf-8")
    # 检索能命中外部 skill
    hits = s.find(scenario="来个酷炫的外部技能")
    assert any(h["slug"] == "ext-my-cool-skill" for h in hits)


def test_external_never_evolves(store, ext_dir):
    s = SkillStore(store_dir=str(store.store_dir), external_dirs=[str(ext_dir)])
    assert EvolveEngine(s, adapter=FakeAdapter(POLISHED_BODY)).on_usage("ext-my-cool-skill") is None


def test_load_external_dirs(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"skills": {"external_dirs": ["/a", "/b"]}}), encoding="utf-8")
    assert load_external_dirs(str(cfg)) == ["/a", "/b"]
    assert load_external_dirs(str(tmp_path / "nonexist.json")) == []


# ---- ScriptSandbox ----

def test_sandbox_disabled_by_default(tmp_path):
    sb = ScriptSandbox()
    with pytest.raises(ScriptSandboxDisabled):
        sb.run(str(tmp_path / "x.py"))


def test_sandbox_from_config():
    assert not ScriptSandbox.from_config({}).enabled
    assert not ScriptSandbox.from_config({"skills": {"scripts_enabled": False}}).enabled
    assert ScriptSandbox.from_config({"skills": {"scripts_enabled": True}}).enabled


def test_sandbox_extension_whitelist(tmp_path):
    sb = ScriptSandbox(enabled=True)
    with pytest.raises(ScriptSandboxDisabled):
        sb.run(str(tmp_path / "evil.exe"))
