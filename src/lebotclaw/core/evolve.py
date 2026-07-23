"""Skill 自我演化引擎（spec 2.6 / P3 3.1-3.6）。

定位：**内部机制**，用户侧只感知「本领被打磨得更顺手了」——不出现"升级/Lv"话术
（哥哥明确要求避免与现有"升级"概念混淆，对外只讲陪伴天数 + token）。

触发（record_usage 之后由 agent 调 on_usage）：
  ① 复用次数满 5 的倍数；② 距上次打磨 effectiveness 漂移 > 0.15。
动作：
  - 打磨：LLM 参考近期复用变体重写正文 → 旧版归档 versions/ → version+1
  - 回滚：连续 3 次低分（<0.4）→ 回退上一版 + 30 天冷却不打磨
  - 合并：正文 ngram 相似度 > 0.7 的两个内部 skill → 留长者/强者，另一个退休
宁缺毋滥：LLM 输出不合格/解析失败 → 保持原样，返回 None。
"""
import json
import re
import shutil
import time
from pathlib import Path
from typing import Optional

EVOLVE_EVERY = 5          # 复用满 N 次触发打磨
DELTA_TRIGGER = 0.15      # effectiveness 漂移触发阈值
LOW_SCORE = 0.4           # 低分线
LOW_STREAK = 3            # 连续低分回滚
COOLDOWN_SECONDS = 30 * 86400
MERGE_NGRAM = 0.7         # 合并相似度阈值
MERGE_EVERY = 20          # 每 N 次复用做一次合并体检

_EVOLVE_PROMPT = """你是小博（一个陪小朋友的通用智能体）的本领打磨器。下面是小博的一张本领卡（SKILL）和它最近的复用记录。
请参考复用中出现的新变体/新场景，把本领正文打磨得更通用、更好用。

【本领】{title}（v{version}，类目 {category}）
【当前正文】
{body}

【近期复用记录】
{usage}

要求：
1. 保持原有结构（Markdown 小节），把变体里的好做法吸收进来，删掉被验证无效的部分。
2. 用小朋友看得懂的说法，别写成说明书。
3. 如果当前正文已经足够好、没有可吸收的新东西，只输出：KEEP
4. 否则只输出打磨后的完整正文（Markdown，200-800 字），不要输出其他内容。"""


def _ngrams(text: str, n: int = 2) -> set:
    text = re.sub(r"\s+", "", (text or "").lower())
    return {text[i:i + n] for i in range(max(0, len(text) - n + 1))}


def _similarity(a: str, b: str) -> float:
    ga, gb = _ngrams(a), _ngrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / float(min(len(ga), len(gb)))


def _bump_version(v: str) -> str:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", str(v or ""))
    if not m:
        return "1.0.1"
    return "%d.%d.%d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)) + 1)


class EvolveEngine:
    def __init__(self, store, adapter=None):
        self.store = store
        self.adapter = adapter

    # ---- 复用档案读取 ----

    def _usage_tail(self, slug: str, limit: int = 10) -> list:
        path = self.store.skills_dir / slug / "usage_log.jsonl"
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return []
        rows = []
        for line in lines[-limit:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    # ---- 触发判定 ----

    def should_evolve(self, entry: dict) -> bool:
        usage = int(entry.get("usage_count") or 0)
        if usage <= 0:
            return False
        cooldown = float(entry.get("evolve_cooldown_until") or 0)
        if cooldown > time.time():
            return False
        if usage % EVOLVE_EVERY == 0:
            return True
        last_eff = entry.get("effectiveness_at_last_evolve")
        if last_eff is not None:
            try:
                if abs(float(entry.get("effectiveness") or 0) - float(last_eff)) > DELTA_TRIGGER:
                    return True
            except (TypeError, ValueError):
                pass
        return False

    def _low_streak(self, slug: str) -> int:
        streak = 0
        for row in reversed(self._usage_tail(slug, LOW_STREAK)):
            eff = row.get("effectiveness")
            if eff is None or float(eff) >= LOW_SCORE:
                break
            streak += 1
        return streak

    # ---- 主入口 ----

    def on_usage(self, slug: str) -> Optional[dict]:
        """record_usage 后调用。返回 skill_evolved 事件或 None。"""
        entry = self.store.get(slug)
        if not entry or entry.get("source") == "external":
            return None
        title = entry.get("title", slug)

        # 回滚优先于打磨：连续低分说明最近一版改坏了
        if self._low_streak(slug) >= LOW_STREAK:
            if self._rollback(slug, entry):
                return {"type": "skill_evolved", "slug": slug, "title": title,
                        "action": "rollback"}

        if not self.should_evolve(entry):
            if int(entry.get("usage_count") or 0) % MERGE_EVERY == 0:
                merged = self.maybe_merge()
                if merged:
                    return merged
            return None
        if self.adapter is None:
            return None

        usage = self._usage_tail(slug, 8)
        usage_txt = "\n".join(
            "- 效果 %s%s" % (
                ("%d%%" % round(float(r["effectiveness"]) * 100)) if r.get("effectiveness") is not None else "未知",
                ("，变体：" + r["variant"]) if r.get("variant") else "")
            for r in usage) or "（暂无）"
        try:
            resp = self.adapter.generate(
                [{"role": "user", "content": _EVOLVE_PROMPT.format(
                    title=title, version=entry.get("version", "1.0.0"),
                    category=entry.get("category", "task_flow"),
                    body=(entry.get("body") or "")[:2000], usage=usage_txt)}],
                temperature=0.3, max_tokens=1500)
            text = getattr(resp, "text", "") or getattr(resp, "content", "") or ""
        except Exception:  # noqa: BLE001
            return None
        new_body = text.strip()
        if not new_body or "KEEP" in new_body[:100] or len(new_body) < 200:
            return None

        # 旧版归档 versions/ → 写新版
        self._archive_version(slug, entry)
        new_version = _bump_version(entry.get("version", "1.0.0"))
        self.store.update_fields(
            slug,
            body=new_body[:2500],
            version=new_version,
            last_evolved_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            effectiveness_at_last_evolve=float(entry.get("effectiveness") or 0),
        )
        return {"type": "skill_evolved", "slug": slug, "title": title,
                "action": "polish", "version": new_version}

    # ---- 版本归档与回滚 ----

    def _archive_version(self, slug: str, entry: dict) -> None:
        pkg = self.store.skills_dir / slug
        versions = pkg / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        src = pkg / "SKILL.md"
        if src.exists():
            dst = versions / ("v%s.md" % entry.get("version", "1.0.0"))
            try:
                shutil.copy2(str(src), str(dst))
            except OSError:
                pass

    def _rollback(self, slug: str, entry: dict) -> bool:
        versions = self.store.skills_dir / slug / "versions"
        if not versions.is_dir():
            return False
        candidates = sorted(versions.glob("v*.md"))
        if not candidates:
            return False
        prev = candidates[-1].read_text(encoding="utf-8")
        from lebotclaw.core.skillstore import parse_frontmatter
        fm, body = parse_frontmatter(prev)
        self.store.update_fields(
            slug,
            body=body,
            version=fm.get("version", entry.get("version", "1.0.0")),
            evolve_cooldown_until=time.time() + COOLDOWN_SECONDS,
            rolled_back_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        return True

    # ---- 合并（内部 skill 限定）----

    def maybe_merge(self) -> Optional[dict]:
        skills = [s for s in self.store.list(status="active")
                  if s.get("source") != "external"]
        for i in range(len(skills)):
            for j in range(i + 1, len(skills)):
                a = self.store.get(skills[i]["slug"])
                b = self.store.get(skills[j]["slug"])
                if not a or not b:
                    continue
                if _similarity(a.get("body", ""), b.get("body", "")) <= MERGE_NGRAM:
                    continue
                # 留长者（created_at 早）/强者（usage 多）：另一个退休并记 merged_into
                keeper, loser = (a, b) if (
                    int(a.get("usage_count") or 0) >= int(b.get("usage_count") or 0)
                ) else (b, a)
                self.store.update_fields(
                    loser["slug"], status="deprecated",
                    merged_into=keeper["slug"],
                    parent_note=(loser.get("parent_note") or "") +
                    "（已并入「%s」）" % keeper.get("title", keeper["slug"]))
                return {"type": "skill_evolved", "slug": keeper["slug"],
                        "title": keeper.get("title", keeper["slug"]),
                        "action": "merge", "absorbed": loser.get("title", loser["slug"])}
        return None
