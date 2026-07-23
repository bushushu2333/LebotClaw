"""SkillStore（SKILL.md 文件包后端）与 SkillLibrary 新后端的测试。"""
import json
import re
import time

import pytest

from lebotclaw.core.skills import SkillLibrary, TeachingSkill
from lebotclaw.core.skillstore import (
    SkillStore,
    dump_frontmatter,
    parse_frontmatter,
)


@pytest.fixture
def store(tmp_path):
    return SkillStore(str(tmp_path))


def _sample_dict():
    return {
        "title": "分披萨讲分数",
        "category": "teaching_tactic",
        "subject": "math",
        "grades": ["三年级", "四年级"],
        "knowledge_points": ["分数的初步认识"],
        "bloom": ["理解", "应用"],
        "trigger": "分数 入门 概念 切分 平均",
        "effectiveness": 0.82,
        "usage_count": 12,
        "source_flow": "flow_8f3a",
        "body": "# 适用场景\n\n孩子第一次接触分数。\n\n# 提问链示例\n\n1. 怎么分才公平？\n",
    }


# ---------------------------------------------------------------------------
# frontmatter 写入 / 解析 round-trip
# ---------------------------------------------------------------------------

class TestFrontmatter:
    def test_roundtrip_scalars_and_lists(self):
        fm = {
            "name": "fen-pizza",
            "title": "分披萨讲分数",
            "version": "2.1.0",
            "effectiveness": 0.82,
            "usage_count": 12,
            "grades": ["三年级", "四年级"],
            "empty_list": [],
            "created_at": "2026-07-25T10:00:00",
        }
        text = dump_frontmatter(fm) + "\n\n# 正文\n内容。\n"
        parsed, body = parse_frontmatter(text)
        assert parsed["name"] == "fen-pizza"
        assert parsed["title"] == "分披萨讲分数"
        assert parsed["version"] == "2.1.0"  # 两个点的版本号不能被解析成数字
        assert parsed["effectiveness"] == 0.82
        assert parsed["usage_count"] == 12
        assert parsed["grades"] == ["三年级", "四年级"]
        assert parsed["empty_list"] == []
        assert parsed["created_at"] == "2026-07-25T10:00:00"  # 含冒号的时间戳
        assert "# 正文" in body and "内容。" in body

    def test_quoted_and_special_values(self):
        fm = {"trigger": "分数: 入门", "note": '他说"平均分"'}
        parsed, _ = parse_frontmatter(dump_frontmatter(fm))
        assert parsed["trigger"] == "分数: 入门"
        assert parsed["note"] == '他说"平均分"'

    def test_no_frontmatter_returns_body(self):
        fm, body = parse_frontmatter("# 只有正文\n")
        assert fm == {}
        assert body == "# 只有正文\n"

    def test_garbage_lines_tolerated(self):
        text = "---\nname: ok\n这不是合法行\n\ntitle: 容忍\n---\n正文\n"
        fm, body = parse_frontmatter(text)
        assert fm["name"] == "ok"
        assert fm["title"] == "容忍"
        assert body.strip() == "正文"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class TestCrud:
    def test_add_get_roundtrip(self, store):
        slug = store.add(_sample_dict())
        assert slug
        entry = store.get(slug)
        assert entry["title"] == "分披萨讲分数"
        assert entry["category"] == "teaching_tactic"
        assert entry["effectiveness"] == 0.82
        assert entry["usage_count"] == 12
        # 去教学化：subject/grades/knowledge_points/bloom 写出即剥掉
        for key in ("subject", "grades", "knowledge_points", "bloom"):
            assert key not in entry, key
        assert "孩子第一次接触分数" in entry["body"]
        # 文件包结构
        pkg = store.skills_dir / slug
        assert (pkg / "SKILL.md").exists()
        assert (pkg / "versions").is_dir()
        assert (store.skills_dir / "index.json").exists()

    def test_slug_defaults_and_uniqueness(self, store):
        a = store.add({"title": "分披萨讲分数"})
        b = store.add({"title": "分披萨讲分数"})
        assert a != b  # 同名不覆盖

    def test_get_missing_returns_none(self, store):
        assert store.get("no-such-skill") is None

    def test_list_and_status_filter(self, store):
        s1 = store.add({"title": "活跃本领"})
        store.add({"title": "退休本领", "status": "deprecated"})
        assert len(store.list()) == 2
        active = store.list(status="active")
        assert len(active) == 1 and active[0]["slug"] == s1

    def test_update_fields(self, store):
        slug = store.add(_sample_dict())
        assert store.update_fields(slug, status="deprecated", trigger="新触发 场景")
        entry = store.get(slug)
        assert entry["status"] == "deprecated"
        assert entry["trigger"] == "新触发 场景"
        assert entry["title"] == "分披萨讲分数"  # 其他字段不动
        assert store.update_fields("ghost", x=1) is False

    def test_update_body(self, store):
        slug = store.add(_sample_dict())
        store.update_fields(slug, body="# 新正文\n")
        assert store.get(slug)["body"] == "# 新正文\n"

    def test_delete(self, store):
        slug = store.add(_sample_dict())
        assert store.delete(slug)
        assert store.get(slug) is None
        assert not (store.skills_dir / slug).exists()
        assert store.list() == []
        assert store.delete(slug) is False


# ---------------------------------------------------------------------------
# 检索：过滤与排序
# ---------------------------------------------------------------------------

class TestFind:
    def _write_legacy_pkg(self, store, slug, fm_text, body="# 正文\n"):
        """模拟磁盘上未洗净的老文件（写出接口已会剥教学字段，只能手写）。"""
        pkg = store.skills_dir / slug
        pkg.mkdir(parents=True)
        (pkg / "SKILL.md").write_text(fm_text + "\n\n" + body, encoding="utf-8")

    def test_subject_and_grade_filters(self, store):
        """旧数据兼容：未洗净的老文件（带 subject/grades）按学科/年级过滤仍生效。"""
        self._write_legacy_pkg(store, "fenru", "---\ntitle: 分数入门\ntrigger: 分数 概念\nsubject: math\ngrades: [三年级]\neffectiveness: 0.9\n---")
        self._write_legacy_pkg(store, "gushi", "---\ntitle: 古诗背诵\ntrigger: 古诗 背诵\nsubject: chinese\ngrades: [三年级]\neffectiveness: 0.9\n---")
        self._write_legacy_pkg(store, "fangcheng", "---\ntitle: 方程入门\ntrigger: 方程 概念\nsubject: math\ngrades: [六年级]\neffectiveness: 0.9\n---")
        store.rebuild_index()
        hit = store.find(scenario="分数", subject="math")
        assert len(hit) == 1 and hit[0]["title"] == "分数入门"
        hit = store.find(scenario="分数", grade="六年级")
        assert hit == []  # 年级硬过滤
        hit = store.find(scenario="", subject="chinese")
        assert len(hit) == 1 and hit[0]["title"] == "古诗背诵"

    def test_ngram_gates_irrelevant(self, store):
        store.add({"title": "分数入门", "trigger": "分数 概念 切分", "subject": "math"})
        store.add({"title": "练字姿势", "trigger": "握笔 坐姿", "subject": "chinese"})
        hit = store.find(scenario="孩子不懂分数概念")
        assert len(hit) == 1 and hit[0]["title"] == "分数入门"

    def test_ngram_partial_overlap_matches(self, store):
        store.add({"title": "分披萨讲分数", "trigger": "分数 入门 概念 切分 平均"})
        hit = store.find(scenario="平均分是什么意思")
        assert len(hit) == 1  # “平均分”与 trigger 的“平均/切分”有 2-gram 重合

    def test_effectiveness_usage_ordering(self, store):
        store.add({"title": "低分少用神功", "trigger": "分数 概念", "subject": "math",
                   "effectiveness": 0.5, "usage_count": 1})
        store.add({"title": "高分常用神功", "trigger": "分数 概念", "subject": "math",
                   "effectiveness": 0.9, "usage_count": 10})
        hit = store.find(scenario="分数概念")
        assert [e["title"] for e in hit] == ["高分常用神功", "低分少用神功"]

    def test_deprecated_excluded_by_default(self, store):
        store.add({"title": "退休分数法", "trigger": "分数 概念",
                   "status": "deprecated", "effectiveness": 0.99, "usage_count": 99})
        assert store.find(scenario="分数概念") == []
        hit = store.find(scenario="分数概念", status="deprecated")
        assert len(hit) == 1

    def test_write_strips_legacy_teaching_fields(self, store, tmp_path):
        """去教学化：遗留教学字段写出时一律剥掉（读取侧仍兼容）。"""
        slug = store.add({"title": "带旧字段的神功", "trigger": "分数 概念",
                          "subject": "math", "grades": ["3年级"],
                          "knowledge_points": ["分数"], "bloom": ["理解"],
                          "body": "# 什么时候用\n想学时\n# 怎么做\n慢慢来"})
        raw = (tmp_path / "skills" / slug / "SKILL.md").read_text(encoding="utf-8")
        for key in ("subject", "grades", "knowledge_points", "bloom"):
            assert not re.search(r"^%s:" % key, raw, flags=re.M), key


# ---------------------------------------------------------------------------
# 旧 skills.json 自动迁移
# ---------------------------------------------------------------------------

def _write_legacy(dirpath, skills, next_id=3):
    legacy = dirpath / "skills.json"
    legacy.write_text(
        json.dumps({"skills": skills, "next_id": next_id}, ensure_ascii=False),
        encoding="utf-8",
    )
    return legacy


class TestMigration:
    LEGACY = [
        {
            "id": 1,
            "name": "分数概念讲解",
            "trigger_scenario": "学生问什么是分数",
            "applicable_grades": ["3年级", "4年级"],
            "subject": "math",
            "steps_template": [
                {"title": "引入", "prompt_hint": "用一个披萨来举例", "result": "孩子笑了"}
            ],
            "common_questions": [{"question": "1/2 大还是 1/4 大", "answer": "用图对比"}],
            "effectiveness_score": 0.9,
            "usage_count": 3,
            "created_at": 1750000000.0,
            "source_session": "sess_1",
        },
        {"id": 2, "name": "古诗背诵", "subject": "chinese"},
    ]

    def test_migrates_legacy_json(self, tmp_path):
        legacy = _write_legacy(tmp_path, list(self.LEGACY))
        lib = SkillLibrary(store_path=str(legacy))
        skills = lib.list_skills()
        assert len(skills) == 2
        top = skills[0]  # usage_count 高的排前
        assert top.name == "分数概念讲解"
        assert top.id == 1
        assert top.trigger_scenario == "学生问什么是分数"
        # 去教学化后学科/年级等教学字段不再随迁移写入
        assert top.applicable_grades == []
        assert top.effectiveness_score == 0.9
        assert top.usage_count == 3
        assert top.steps_template[0]["title"] == "引入"
        assert top.common_questions[0]["question"] == "1/2 大还是 1/4 大"
        # 原文件改名，文件包落盘
        assert not legacy.exists()
        assert (tmp_path / "skills.json.migrated").exists()
        assert (tmp_path / "skills").is_dir()

    def test_migration_idempotent(self, tmp_path):
        legacy = _write_legacy(tmp_path, list(self.LEGACY))
        lib = SkillLibrary(store_path=str(legacy))
        lib2 = SkillLibrary(store_path=str(legacy))  # 重跑不重复迁移
        assert len(lib2.list_skills()) == 2
        lib3 = SkillLibrary(store_path=str(tmp_path / "skills.json"))
        assert len(lib3.list_skills()) == 2

    def test_id_counter_continues_after_migration(self, tmp_path):
        legacy = _write_legacy(tmp_path, list(self.LEGACY))
        lib = SkillLibrary(store_path=str(legacy))
        sid = lib.add_skill(TeachingSkill(name="新本领", subject="math"))
        assert sid > 2  # 不复用旧 id


# ---------------------------------------------------------------------------
# 复用档案 record_usage（FR-U1）
# ---------------------------------------------------------------------------

class TestRecordUsage:
    def test_appends_log_and_rolls_score(self, store):
        slug = store.add({"title": "分数入门", "trigger": "分数 概念",
                          "effectiveness": 0.8, "usage_count": 1})
        ok = store.record_usage(
            slug,
            outcome={"completion": 0.9, "feedback": "positive"},
            variant="把披萨换成了蛋糕",
            effectiveness=1.0,
        )
        assert ok
        log = (store.skills_dir / slug / "usage_log.jsonl").read_text(encoding="utf-8")
        lines = [json.loads(l) for l in log.splitlines() if l.strip()]
        assert len(lines) == 1
        assert lines[0]["outcome"]["completion"] == 0.9
        assert lines[0]["variant"] == "把披萨换成了蛋糕"
        entry = store.get(slug)
        assert entry["usage_count"] == 2
        assert entry["effectiveness"] == pytest.approx((0.8 * 1 + 1.0) / 2, abs=1e-4)
        # 再记一次，滚动均值正确
        store.record_usage(slug, outcome={}, effectiveness=0.0)
        entry = store.get(slug)
        assert entry["usage_count"] == 3
        assert entry["effectiveness"] == pytest.approx((0.8 + 1.0 + 0.0) / 3, abs=1e-4)

    def test_index_updated_after_record(self, store):
        slug = store.add({"title": "分数入门", "trigger": "分数 概念",
                          "effectiveness": 0.1, "usage_count": 0})
        store.record_usage(slug, effectiveness=0.9)
        idx = json.loads((store.skills_dir / "index.json").read_text(encoding="utf-8"))
        meta = idx["skills"][slug]
        assert meta["usage_count"] == 1
        assert meta["effectiveness"] == pytest.approx(0.9, abs=1e-4)

    def test_record_without_effectiveness_keeps_score(self, store):
        slug = store.add({"title": "分数入门", "effectiveness": 0.7, "usage_count": 5})
        store.record_usage(slug, outcome={"note": "仅归档"})
        entry = store.get(slug)
        assert entry["effectiveness"] == 0.7
        assert entry["usage_count"] == 5

    def test_missing_slug(self, store):
        assert store.record_usage("ghost", effectiveness=0.5) is False


# ---------------------------------------------------------------------------
# 索引重建一致性 + 手改生效
# ---------------------------------------------------------------------------

class TestIndex:
    def test_rebuild_matches_disk(self, store):
        slugs = [store.add({"title": "本领{}".format(i)}) for i in range(3)]
        store.delete(slugs[1])
        idx = store.rebuild_index()
        assert sorted(idx["skills"].keys()) == sorted([slugs[0], slugs[2]])
        assert idx["skills"][slugs[0]]["tokens"]  # 索引带检索 token

    def test_hand_edit_takes_effect_after_rebuild(self, store):
        slug = store.add({"title": "分数入门", "trigger": "分数 概念"})
        md = store.skills_dir / slug / "SKILL.md"
        text = md.read_text(encoding="utf-8").replace("分数入门", "分数入门（手改版）")
        md.write_text(text, encoding="utf-8")
        store.rebuild_index()
        entry = store.get(slug)
        assert entry["title"] == "分数入门（手改版）"
        assert store.list()[0]["title"] == "分数入门（手改版）"

    def test_index_tokens_cover_chinese_trigger(self, store):
        slug = store.add({"title": "分披萨讲分数", "trigger": "分数 入门 概念"})
        idx = json.loads((store.skills_dir / "index.json").read_text(encoding="utf-8"))
        tokens = idx["skills"][slug]["tokens"]
        assert "分数" in tokens and "入门" in tokens


# ---------------------------------------------------------------------------
# SkillLibrary 兼容层
# ---------------------------------------------------------------------------

class TestSkillLibraryBackend:
    def test_add_find_roundtrip(self, tmp_path):
        lib = SkillLibrary(store_path=str(tmp_path / "skills.json"))
        skill = TeachingSkill(
            name="分数概念讲解",
            trigger_scenario="学生问什么是分数",
            subject="math",
            applicable_grades=["3年级", "4年级"],
            steps_template=[{"title": "引入", "prompt_hint": "用一个披萨来举例"}],
            knowledge_points=["分数的初步认识"],
            category="teaching_tactic",
        )
        sid = lib.add_skill(skill)
        assert sid > 0
        assert skill.slug  # add 后回写 slug
        found = lib.find_skill(scenario="分数", subject="math")
        assert len(found) >= 1
        assert found[0].name == "分数概念讲解"
        assert found[0].category == "teaching_tactic"
        # 去教学化：knowledge_points 等教学字段写出即剥掉，读回为空
        assert found[0].knowledge_points == []
        assert found[0].steps_template[0]["prompt_hint"] == "用一个披萨来举例"
        assert "披萨" in found[0].body

    def test_ids_are_unique(self, tmp_path):
        lib = SkillLibrary(store_path=str(tmp_path / "skills.json"))
        a = lib.add_skill(TeachingSkill(name="a"))
        b = lib.add_skill(TeachingSkill(name="b"))
        assert a != b
