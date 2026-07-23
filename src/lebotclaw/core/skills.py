"""SkillLibrary —— 旧版技能库公开 API 的兼容壳（生产代码已不再使用，仅存量
测试与旧调用方保留）。

v2.0 起存储后端从单个 skills.json 换成 SKILL.md 文件包（core/skillstore.py），
本类只做 TeachingSkill <-> 文件包条目的转换与委托。旧 skills.json 在首次
初始化时自动迁移为文件包并改名 skills.json.migrated（幂等）。
"""
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Union, List, Dict

from lebotclaw.core.skillstore import (
    SkillStore,
    _iso,
    _parse_time,
    parse_body_sections,
    render_body,
)


@dataclass
class TeachingSkill:
    id: Optional[int] = None
    name: str = ""
    trigger_scenario: str = ""
    applicable_grades: List[str] = field(default_factory=list)
    subject: str = ""
    recommended_tools: List[str] = field(default_factory=list)
    steps_template: List[Dict] = field(default_factory=list)
    common_questions: List[Dict] = field(default_factory=list)
    effectiveness_score: float = 0.0
    usage_count: int = 0
    created_at: float = 0.0
    source_session: str = ""
    # v2.0 新增字段（均有默认值，旧调用方不受影响）
    slug: str = ""
    category: str = "task_flow"  # task_flow | play_pattern（旧数据可能有 teaching_tactic）
    version: str = "1.0.0"
    status: str = "active"  # active | deprecated
    source: str = "internal"  # internal | external
    knowledge_points: List[str] = field(default_factory=list)
    bloom: List[str] = field(default_factory=list)
    body: str = ""  # SKILL.md Markdown 正文（复用注入用）


class SkillLibrary:
    """公开方法签名与旧版一致；内部委托 SkillStore 文件包后端。

    store_path 兼容两种形态：
      - 以 .json 结尾：视为旧版 skills.json 路径，存储根目录取其父目录
        （默认 ~/.lebotclaw/skills.json -> 文件包落在 ~/.lebotclaw/skills/）
      - 其他：直接作为存储根目录
    """

    def __init__(self, store_path: Union[str, Path] = "~/.lebotclaw/skills.json"):
        self.store_path = Path(store_path).expanduser()
        if self.store_path.suffix == ".json":
            store_dir = self.store_path.parent
            legacy_json = self.store_path
        else:
            store_dir = self.store_path
            legacy_json = self.store_path / "skills.json"
        self._store = SkillStore(store_dir, legacy_json=legacy_json)

    @property
    def store(self) -> SkillStore:
        return self._store

    # ------------------------------------------------------------------
    # TeachingSkill <-> 文件包条目
    # ------------------------------------------------------------------

    def _skill_to_dict(self, skill: TeachingSkill) -> Dict:
        body = skill.body or render_body(
            skill.trigger_scenario,
            skill.steps_template,
            skill.common_questions,
        )
        data = {
            "slug": skill.slug or "",
            "title": skill.name,
            "category": skill.category,
            "version": skill.version,
            "status": skill.status,
            "source": skill.source,
            "subject": skill.subject,
            "grades": list(skill.applicable_grades),
            "knowledge_points": list(skill.knowledge_points),
            "bloom": list(skill.bloom),
            "trigger": skill.trigger_scenario,
            "effectiveness": skill.effectiveness_score,
            "usage_count": skill.usage_count,
            "created_at": _iso(skill.created_at or time.time()),
            "source_flow": skill.source_session,
            "body": body,
        }
        if skill.id is not None:
            data["id"] = skill.id
        if skill.recommended_tools:
            data["recommended_tools"] = list(skill.recommended_tools)
        return data

    def _dict_to_skill(self, entry: Dict) -> TeachingSkill:
        body = entry.get("body", "") or ""
        steps, questions = parse_body_sections(body)
        return TeachingSkill(
            id=entry.get("id"),
            name=entry.get("title") or entry.get("name") or "",
            trigger_scenario=entry.get("trigger", "") or "",
            applicable_grades=list(entry.get("grades") or []),
            subject=entry.get("subject", "") or "",
            recommended_tools=list(entry.get("recommended_tools") or []),
            steps_template=steps,
            common_questions=questions,
            effectiveness_score=float(entry.get("effectiveness") or 0.0),
            usage_count=int(entry.get("usage_count") or 0),
            created_at=_parse_time(entry.get("created_at")),
            source_session=entry.get("source_flow", "") or "",
            slug=entry.get("slug") or entry.get("name") or "",
            category=entry.get("category", "task_flow") or "task_flow",
            version=str(entry.get("version", "1.0.0") or "1.0.0"),
            status=entry.get("status", "active") or "active",
            source=entry.get("source", "internal") or "internal",
            knowledge_points=list(entry.get("knowledge_points") or []),
            bloom=list(entry.get("bloom") or []),
            body=body,
        )

    # ------------------------------------------------------------------
    # 公开 API（签名不变）
    # ------------------------------------------------------------------

    def add_skill(self, skill: TeachingSkill) -> int:
        if not skill.created_at:
            skill.created_at = time.time()
        if skill.id is None:
            skill.id = self._store.allocate_id()
        skill.slug = self._store.add(self._skill_to_dict(skill))
        return skill.id

    def find_skill(
        self,
        scenario: str = "",
        subject: str = "",
        grade: str = "",
    ) -> List[TeachingSkill]:
        entries = self._store.find(scenario=scenario, subject=subject, grade=grade)
        return [self._dict_to_skill(e) for e in entries]

    def update_effectiveness(self, skill_id: int, score: float) -> None:
        slug = self._store.slug_by_id(skill_id)
        if not slug:
            return
        self._store.record_usage(
            slug, outcome={"source": "update_effectiveness"}, effectiveness=score
        )

    def list_skills(self, subject: str = "") -> List[TeachingSkill]:
        results = []
        for entry in self._store.list():
            full = self._store.get(entry["slug"]) or entry
            skill = self._dict_to_skill(full)
            if subject and skill.subject != subject:
                continue
            results.append(skill)
        results.sort(key=lambda x: x.usage_count, reverse=True)
        return results
