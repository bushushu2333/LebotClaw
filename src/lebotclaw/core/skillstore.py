"""SKILL.md 文件包存储后端（super-agent spec §2.1 / §2.2）。

目录布局：
    {store_dir}/skills/<slug>/SKILL.md        frontmatter + Markdown 正文
    {store_dir}/skills/<slug>/usage_log.jsonl 复用档案（FR-U1）
    {store_dir}/skills/<slug>/versions/       历史版本归档（FR-U2，本模块仅建目录）
    {store_dir}/skills/index.json             检索索引缓存

frontmatter 解析为手写的最小 YAML 子集（项目无 pyyaml 依赖）：
支持 ``key: value`` 标量（字符串/整数/浮点/布尔）与 ``key: [a, b]`` 行内列表。
解析器对无法识别的行静默跳过，保证手改文件不会崩库。

纯同步、无第三方依赖、Python 3.9 兼容。
"""
import hashlib
import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# frontmatter 规范字段的写出顺序（spec §2.2），其余扩展字段排在其后
FRONTMATTER_ORDER = [
    "name",
    "title",
    "category",
    "version",
    "status",
    "source",
    "trigger",
    "effectiveness",
    "usage_count",
    "created_at",
    "source_flow",
]

# 新写入 skill 的类目（通用智能体向）。旧数据里的 teaching_tactic 等教学向
# 类目不在此列，但读取/展示完全兼容——这里只管「新写的允许什么」。
VALID_CATEGORIES = ("task_flow", "play_pattern")

# 教学向时期的遗留字段：写出时一律剥掉。旧数据只要被保存/打磨/小博化过
# 一次，SKILL.md 即洗净（读取侧始终兼容，不洗存量未触达的文件）。
LEGACY_TEACHING_KEYS = ("subject", "grades", "knowledge_points", "bloom",
                        "applicable_grades")
VALID_STATUS = ("active", "deprecated")
VALID_SOURCES = ("internal", "external")

_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?$")


# ---------------------------------------------------------------------------
# 最小 YAML frontmatter 解析 / 写出
# ---------------------------------------------------------------------------

def _format_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    s = str(value)
    if s == "":
        return '""'
    need_quote = (
        s != s.strip()
        or s.lower() in ("true", "false", "null", "~")
        or _INT_RE.match(s) is not None
        or _FLOAT_RE.match(s) is not None
        or re.search(r'[:#\[\]{}"\',&*!|>%@`]', s) is not None
    )
    if need_quote:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _format_value(value) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_scalar(v) for v in value) + "]"
    return _format_scalar(value)


def _parse_scalar(text: str):
    s = text.strip()
    if s == "":
        return ""
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        inner = s[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    if len(s) >= 2 and s.startswith("'") and s.endswith("'"):
        return s[1:-1].replace("''", "'")
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~"):
        return None
    if _INT_RE.match(s):
        try:
            return int(s)
        except ValueError:
            return s
    if _FLOAT_RE.match(s) and s.count(".") <= 1:
        try:
            return float(s)
        except ValueError:
            return s
    return s


def _split_list_items(inner: str) -> List[str]:
    """按顶层逗号切分行内列表（尊重引号，不支持嵌套）。"""
    items, buf, quote = [], [], ""
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ",":
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        items.append("".join(buf))
    return items


def parse_frontmatter(text: str) -> Tuple[Dict, str]:
    """把 SKILL.md 全文拆成 (frontmatter dict, markdown 正文)。

    没有 frontmatter 时返回 ({}, 原文)。无法解析的行静默跳过。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    data: Dict = {}
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, raw = stripped.partition(":")
        key = key.strip()
        raw = raw.strip()
        if not key:
            continue
        if raw.startswith("[") and raw.endswith("]"):
            inner = raw[1:-1].strip()
            if not inner:
                data[key] = []
            else:
                data[key] = [_parse_scalar(item) for item in _split_list_items(inner)]
        else:
            data[key] = _parse_scalar(raw)
    body_lines = lines[end + 1:]
    while body_lines and body_lines[0].strip() == "":
        body_lines.pop(0)  # 去掉 frontmatter 与正文之间的空行
    body = "\n".join(body_lines)
    if body and text.endswith("\n"):
        body += "\n"
    return data, body


def dump_frontmatter(data: Dict) -> str:
    """把 dict 写成 frontmatter 文本（含首尾 --- 行）。"""
    keys = [k for k in FRONTMATTER_ORDER if k in data]
    keys += sorted(k for k in data if k not in FRONTMATTER_ORDER)
    lines = ["---"]
    for key in keys:
        value = data[key]
        if value is None:
            continue
        lines.append("{}: {}".format(key, _format_value(value)))
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 正文渲染 / 解析（steps_template、common_questions 与 Markdown 互转）
# ---------------------------------------------------------------------------

def render_body(
    trigger: str = "",
    steps: Optional[List[Dict]] = None,
    questions: Optional[List[Dict]] = None,
    extra: str = "",
) -> str:
    """把结构化字段拼成 Markdown 正文（迁移与 add_skill 共用）。"""
    parts = ["# 适用场景\n\n" + (trigger or "（未记录）")]
    if steps:
        lines = ["# 执行步骤", ""]
        for i, step in enumerate(steps, 1):
            title = str(step.get("title", "")).strip()
            hint = str(step.get("prompt_hint", "")).strip()
            line = "{}. **{}**".format(i, title)
            if hint:
                line += "：{}".format(hint)
            lines.append(line)
            result = str(step.get("result", "")).strip()
            if result:
                lines.append("   结果：{}".format(result))
        parts.append("\n".join(lines))
    if questions:
        lines = ["# 常见问题", ""]
        for q in questions:
            lines.append("- Q: {}".format(q.get("question", "")))
            if q.get("answer"):
                lines.append("  A: {}".format(q["answer"]))
        parts.append("\n".join(lines))
    if extra:
        parts.append(extra.strip())
    return "\n\n".join(parts) + "\n"


_STEP_RE = re.compile(r"^\d+[.、)]\s+(.*)$")


def parse_body_sections(body: str) -> Tuple[List[Dict], List[Dict]]:
    """从 Markdown 正文尽力解析回 (steps_template, common_questions)。

    只识别本模块 render_body 产出的格式；手改正文解析不到时返回空列表，
    不影响其他字段（正文本身仍可通过 get() 的 body 取得）。
    """
    steps: List[Dict] = []
    questions: List[Dict] = []
    section = ""
    for raw_line in (body or "").splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            section = line.lstrip("#").strip()
            continue
        if not line:
            continue
        if section == "执行步骤":
            m = _STEP_RE.match(line)
            if m:
                content = m.group(1)
                title, _, hint = content.partition("**：")
                title = title.strip().strip("*").strip()
                if not hint:
                    title, _, hint = content.partition("：")
                    title = title.strip().strip("*").strip()
                steps.append({"title": title, "prompt_hint": hint.strip()})
                continue
            if line.startswith("结果：") and steps:
                steps[-1]["result"] = line[len("结果："):].strip()
        elif section == "常见问题":
            if line.startswith("- Q:"):
                questions.append({"question": line[len("- Q:"):].strip(), "answer": ""})
            elif line.startswith("A:") and questions:
                questions[-1]["answer"] = line[len("A:"):].strip()
    return steps, questions


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _parse_time(value) -> float:
    """created_at 兼容 ISO 字符串与 epoch 数字，统一返回 epoch 浮点。"""
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return 0.0


def _ngrams(text: str, n: int = 2) -> set:
    text = re.sub(r"\s+", "", (text or "").lower())
    if not text:
        return set()
    if len(text) <= n:
        return {text}
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return slug


def load_external_dirs(config_path=None) -> list:
    """spec 2.9：从 ~/.lebotclaw/config.json 读 skills.external_dirs（没有→[]）。

    配置示例：
      {"skills": {"external_dirs": ["~/.openclaw/workspace/skills",
                                    "~/.claude/skills"],
                  "scripts_enabled": false}}
    """
    path = (Path(config_path).expanduser() if config_path
            else Path("~/.lebotclaw/config.json").expanduser())
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    dirs = cfg.get("skills", {}).get("external_dirs", [])
    if not isinstance(dirs, list):
        return []
    return [str(d) for d in dirs if d]


class SkillStore:
    """SKILL.md 文件包存储后端。

    检索只读 index.json（命中后才读 SKILL.md 正文）；增删改后重建索引。
    """

    def __init__(
        self,
        store_dir: "object" = "~/.lebotclaw",
        legacy_json: "object" = None,
        external_dirs=None,
    ):
        self.store_dir = Path(store_dir).expanduser()
        self.skills_dir = self.store_dir / "skills"
        self.index_path = self.skills_dir / "index.json"
        # spec 2.9：外部 skill 目录只读挂载（哥哥 Claude Code/OpenClaw 在用的 skills）。
        # 外部条目只进索引（ext_path 指回原文件），绝不写删改。
        self.external_dirs = [Path(d).expanduser() for d in (external_dirs or [])]
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._legacy_json = (
            Path(legacy_json).expanduser()
            if legacy_json
            else self.store_dir / "skills.json"
        )
        self._migrate_legacy()
        if self.external_dirs or not self.index_path.exists():
            # 外部挂载内容可能随时变化（哥哥在 Claude Code/OpenClaw 侧增删 skill），
            # 有外部源时每次初始化都重建索引（扫描成本低）
            self.rebuild_index()

    # ------------------------------------------------------------------
    # 旧 skills.json 自动迁移（spec §2.3）
    # ------------------------------------------------------------------

    def _has_skill_packages(self) -> bool:
        return any(p.is_dir() for p in self.skills_dir.iterdir())

    def _migrate_legacy(self) -> int:
        """旧 skills.json 存在且 skills/ 为空时逐条转 SKILL.md，幂等。"""
        legacy = self._legacy_json
        if not legacy.exists() or self._has_skill_packages():
            return 0
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        migrated = 0
        for raw in data.get("skills", []):
            if not isinstance(raw, dict):
                continue
            steps = raw.get("steps_template") or []
            questions = raw.get("common_questions") or []
            trigger = raw.get("trigger_scenario", "") or ""
            fm = {
                "title": raw.get("name", "") or "",
                "category": raw.get("category", "task_flow"),
                "version": raw.get("version", "1.0.0"),
                "status": raw.get("status", "active"),
                "source": raw.get("source", "internal"),
                "trigger": trigger,
                "effectiveness": float(raw.get("effectiveness_score") or 0.0),
                "usage_count": int(raw.get("usage_count") or 0),
                "created_at": _iso(_parse_time(raw.get("created_at")) or time.time()),
                "source_flow": raw.get("source_session", "") or "",
            }
            if raw.get("id") is not None:
                fm["id"] = raw["id"]
            if raw.get("recommended_tools"):
                fm["recommended_tools"] = list(raw["recommended_tools"])
            body = render_body(trigger, steps, questions)
            slug = self._unique_slug(raw.get("slug") or fm["title"] or "skill")
            self._write_package(slug, fm, body)
            migrated += 1
        legacy.rename(legacy.with_name(legacy.name + ".migrated"))
        if migrated:
            self.rebuild_index()
        return migrated

    # ------------------------------------------------------------------
    # 文件读写
    # ------------------------------------------------------------------

    def _unique_slug(self, hint: str) -> str:
        base = _slugify(hint)
        if not base:
            digest = hashlib.md5((hint or "skill").encode("utf-8")).hexdigest()[:8]
            base = "skill-" + digest
        slug, n = base, 2
        while (self.skills_dir / slug).exists():
            slug = "{}-{}".format(base, n)
            n += 1
        return slug

    def _write_package(self, slug: str, fm: Dict, body: str) -> None:
        pkg = self.skills_dir / slug
        (pkg / "versions").mkdir(parents=True, exist_ok=True)
        fm = dict(fm)
        for k in LEGACY_TEACHING_KEYS:
            fm.pop(k, None)
        fm["name"] = slug
        content = dump_frontmatter(fm) + "\n\n" + (body or "").lstrip("\n")
        (pkg / "SKILL.md").write_text(content, encoding="utf-8")

    def _read_package(self, slug: str) -> Optional[Dict]:
        # 外部条目：从索引里的 ext_path 读，正文可能在外部目录（只读）
        if str(slug).startswith("ext-"):
            meta = self._load_index().get("skills", {}).get(slug)
            if not meta:
                return None
            md = Path(meta.get("ext_path", "")) / "SKILL.md"
        else:
            md = self.skills_dir / slug / "SKILL.md"
        if not md.exists():
            return None
        try:
            fm, body = parse_frontmatter(md.read_text(encoding="utf-8"))
        except OSError:
            return None
        entry = dict(fm)
        entry["slug"] = slug
        entry["name"] = slug
        entry["body"] = body
        if str(slug).startswith("ext-"):
            entry["source"] = "external"
            entry["external"] = True
        return entry

    def _is_external(self, slug: str) -> bool:
        return str(slug).startswith("ext-")

    # ------------------------------------------------------------------
    # 索引
    # ------------------------------------------------------------------

    def _read_index_file(self) -> Dict:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _load_index(self) -> Dict:
        idx = self._read_index_file()
        if not idx:
            idx = self.rebuild_index()
        return idx

    def _save_index(self, idx: Dict) -> None:
        self.index_path.write_text(
            json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def rebuild_index(self) -> Dict:
        """全量扫描 skills/*/SKILL.md 重建 index.json，返回索引内容。"""
        old = self._read_index_file()
        skills: Dict[str, Dict] = {}
        max_id = 0
        for pkg in sorted(self.skills_dir.iterdir()):
            if not pkg.is_dir():
                continue
            md = pkg / "SKILL.md"
            if not md.exists():
                continue
            try:
                fm, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
            except OSError:
                continue
            slug = str(fm.get("name") or pkg.name)
            entry = dict(fm)
            entry["slug"] = slug
            entry["tokens"] = sorted(
                _ngrams(str(fm.get("trigger", "")))
                | _ngrams(str(fm.get("title", "") or slug))
            )
            skills[slug] = entry
            try:
                max_id = max(max_id, int(fm.get("id") or 0))
            except (TypeError, ValueError):
                pass
        # 外部只读挂载：扫 external_dirs 下每个含 SKILL.md 的子目录
        for ext_root in self.external_dirs:
            if not ext_root.is_dir():
                continue
            for pkg in sorted(ext_root.iterdir()):
                md = pkg / "SKILL.md"
                if not pkg.is_dir() or not md.exists():
                    continue
                try:
                    fm, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
                except OSError:
                    continue
                slug = "ext-%s" % pkg.name
                n = 2
                while slug in skills:  # 多目录同名去重
                    slug = "ext-%s-%d" % (pkg.name, n)
                    n += 1
                entry = dict(fm)
                entry["slug"] = slug
                entry["source"] = "external"
                entry["external"] = True
                entry["ext_path"] = str(pkg)
                entry["tokens"] = sorted(
                    _ngrams(str(fm.get("trigger", "")))
                    | _ngrams(str(fm.get("title", "") or pkg.name))
                    | _ngrams(str(fm.get("name", "") or pkg.name))
                )
                skills[slug] = entry
        next_id = max(int(old.get("next_id", 1) or 1), max_id + 1)
        idx = {"version": 1, "next_id": next_id, "skills": skills}
        self._save_index(idx)
        return idx

    def allocate_id(self) -> int:
        idx = self._load_index()
        nid = int(idx.get("next_id", 1) or 1)
        idx["next_id"] = nid + 1
        self._save_index(idx)
        return nid

    def slug_by_id(self, skill_id: int) -> Optional[str]:
        for slug, meta in self._load_index().get("skills", {}).items():
            if meta.get("id") == skill_id:
                return slug
        return None

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, skill_dict: Dict) -> str:
        """新增 skill，返回 slug。skill_dict 的 body 键为 Markdown 正文。"""
        body = skill_dict.get("body", "") or ""
        fm = {k: v for k, v in skill_dict.items() if k not in ("body", "slug", "name")}
        fm.setdefault("title", skill_dict.get("title", ""))
        fm.setdefault("category", "task_flow")
        fm.setdefault("version", "1.0.0")
        fm.setdefault("status", "active")
        fm.setdefault("source", "internal")
        fm.setdefault("trigger", "")
        fm.setdefault("effectiveness", 0.0)
        fm.setdefault("usage_count", 0)
        fm.setdefault(
            "created_at",
            _iso(_parse_time(skill_dict.get("created_at")) or time.time()),
        )
        hint = skill_dict.get("slug") or fm.get("title") or skill_dict.get("name") or "skill"
        slug = self._unique_slug(str(hint))
        self._write_package(slug, fm, body)
        self.rebuild_index()
        return slug

    def get(self, slug: str) -> Optional[Dict]:
        """读取完整条目：frontmatter 字段 + body（Markdown 正文）+ slug。"""
        return self._read_package(slug)

    def list(self, status: str = "") -> List[Dict]:
        """列出索引中的条目（不含正文）。status 非空时按状态过滤。"""
        skills = self._load_index().get("skills", {})
        out = []
        for slug, meta in skills.items():
            if status and meta.get("status", "active") != status:
                continue
            entry = dict(meta)
            entry["slug"] = slug
            out.append(entry)
        out.sort(key=lambda e: (e.get("usage_count") or 0), reverse=True)
        return out

    def update_fields(self, slug: str, **fields) -> bool:
        if self._is_external(slug):
            return False  # 外部源只读
        entry = self._read_package(slug)
        if entry is None:
            return False
        body = fields.pop("body", entry.get("body", ""))
        entry.pop("body", None)
        entry.pop("slug", None)
        entry.update(fields)
        self._write_package(slug, entry, body)
        self.rebuild_index()
        return True

    def delete(self, slug: str) -> bool:
        if self._is_external(slug):
            return False  # 外部源只读，不能删
        pkg = self.skills_dir / slug
        if not pkg.is_dir():
            return False
        shutil.rmtree(pkg)
        self.rebuild_index()
        return True

    # ------------------------------------------------------------------
    # 检索（ngram 打分 + effectiveness 排序；subject/grade 参数仅为兼容旧
    # 调用方与旧数据保留——新 skill 已不再带学科/年级字段，过滤自然失效）
    # ------------------------------------------------------------------

    def find(
        self,
        scenario: str = "",
        subject: str = "",
        grade: str = "",
        status: str = "active",
    ) -> List[Dict]:
        idx = self._load_index()
        qgrams = _ngrams(scenario)
        scenario_lower = (scenario or "").lower()
        words = [w for w in (scenario or "").split() if len(w) > 1]
        scored: List[Tuple[float, str]] = []
        for slug, meta in idx.get("skills", {}).items():
            if status and meta.get("status", "active") != status:
                continue
            m_subject = meta.get("subject") or ""
            if subject and m_subject and m_subject != subject:
                continue
            m_grades = meta.get("grades") or []
            if grade and m_grades and grade not in m_grades:
                continue
            score = 0.0
            if scenario:
                trigger = str(meta.get("trigger", "") or "")
                title = str(meta.get("title", "") or slug)
                tgrams = set(meta.get("tokens") or []) or (
                    _ngrams(trigger) | _ngrams(title)
                )
                if qgrams and tgrams:
                    inter = len(qgrams & tgrams)
                    score = inter / float(max(1, min(len(qgrams), len(tgrams))))
                hay = (trigger + " " + title).lower()
                substring = (
                    scenario_lower in hay
                    or any(w in trigger or w in title for w in words)
                )
                if score <= 0.0 and not substring:
                    continue
                if score <= 0.0:
                    score = 0.1  # 子串兜底命中的保底分
            eff = float(meta.get("effectiveness") or 0.0)
            usage = int(meta.get("usage_count") or 0)
            rank = eff * (usage + 1) * (0.5 + score if scenario else 1.0)
            scored.append((rank, slug))
        scored.sort(key=lambda item: item[0], reverse=True)
        results = []
        for _, slug in scored:
            entry = self._read_package(slug)  # 命中后才读正文
            if entry is not None:
                results.append(entry)
        return results

    # ------------------------------------------------------------------
    # 复用档案（FR-U1）
    # ------------------------------------------------------------------

    def record_usage(
        self,
        slug: str,
        outcome: Optional[Dict] = None,
        variant: str = "",
        effectiveness: Optional[float] = None,
    ) -> bool:
        """追加 usage_log.jsonl，并滚动更新 effectiveness / usage_count + 重建索引。"""
        if self._is_external(slug):
            return False  # 外部源只读，不写复用档案（演化只针对内部 skill）
        entry = self._read_package(slug)
        if entry is None:
            return False
        record = {
            "ts": _iso(time.time()),
            "outcome": outcome or {},
            "variant": variant or "",
            "effectiveness": effectiveness,
        }
        log_path = self.skills_dir / slug / "usage_log.jsonl"
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        body = entry.pop("body", "")
        entry.pop("slug", None)
        if effectiveness is not None:
            old_eff = float(entry.get("effectiveness") or 0.0)
            old_count = int(entry.get("usage_count") or 0)
            entry["usage_count"] = old_count + 1
            entry["effectiveness"] = round(
                (old_eff * old_count + float(effectiveness)) / (old_count + 1), 4
            )
        self._write_package(slug, entry, body)
        self.rebuild_index()
        return True
