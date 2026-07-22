"""内容守护：违禁词 + 心理守护检测引擎。

设计要点（详见方案）：
- 归一化：拼音缩写/谐音/符号间隔/重复字/数字转中文 全部归一后再匹配
- DFA（trie）：毫秒级多词库扫描，避免逐词线性查找
- 四类优先级：mental（最高，绝不拦截）> nsfw（拦截）> politics（拦截）> abuse（放行）
- 心理类白名单短语：'想死你了'/'累死了' 等口语不计自伤
- 双闸口：入口查学生输入（入口闸），出口查模型输出（出口闸）
"""
import json
import re
from pathlib import Path

_WORD_DIR = Path(__file__).parent / "banned_words"

# 优先级顺序：越靠前优先级越高（同一句命中多类，取最前的）
_ORDER = ["mental", "nsfw", "politics", "abuse"]

# 数字 → 中文（用于归一化 "520" 这类，也可辅助"3p"等）
_DIGIT_MAP = str.maketrans("0123456789", "零一二三四五六七八九")

# 全角→半角 + 常见干扰符归一化
_SPACE_RE = re.compile(r"\s+")
# 把字母间插入的符号、星号遮挡（傻*逼 / 傻 B / s.b）去掉再匹配
_JUNK_RE = re.compile(r"[\s\*\-_.·,,!！?？/\\|~`'\"@#$%^&()+={}\[\]<>:;]")
# 连续重复字压缩成一个（"傻逼逼"→"傻逼"），但保留至少一个
_REPEAT_RE = re.compile(r"(.)\1+")


def _normalize(text: str) -> str:
    """把用户输入归一化到便于匹配的形式：小写 + 半角 + 去干扰符 + 重复字压缩。"""
    if not text:
        return ""
    t = text.translate(str.maketrans({chr(0xFF01 + i): chr(33 + i) for i in range(94)}))  # 全角→半角
    t = t.lower()
    t = t.translate(_DIGIT_MAP)
    t = _JUNK_RE.sub("", t)
    t = _REPEAT_RE.sub(r"\1", t)
    return t


def _build_trie(words):
    """words → 嵌套 dict trie（{'傻': {'逼': {'__end__': '傻逼'}}}）。"""
    trie = {}
    for w in words:
        w = _normalize(w)
        if not w:
            continue
        node = trie
        for ch in w:
            node = node.setdefault(ch, {})
        node["__end__"] = w
    return trie


class _Wordlist:
    def __init__(self, category, data):
        self.category = category
        self.severity = data.get("severity", "pass")  # care / pass / block
        self.hint = data.get("hint", "")
        self.words = [w for w in data.get("words", []) if isinstance(w, str) and w and not w.startswith("_")]
        self.whitelist = [p for p in data.get("whitelist_phrases", []) if p]
        self.raw = data
        self.trie = _build_trie(self.words)
        self._wl_norm = [_normalize(p) for p in self.whitelist]
        # 归一化后的白名单集合，用于"想死"命中后排除"想死你了"
        self._wl_anchors = {p[:2] for p in self._wl_norm if len(p) >= 2}

    def scan(self, norm_text):
        """返回命中的归一化词列表（去重、保序）。"""
        if not norm_text:
            return []
        hits, found = [], set()
        n = len(norm_text)
        for i in range(n):
            node = self.trie
            j = i
            while j < n and norm_text[j] in node:
                node = node[norm_text[j]]
                j += 1
                if "__end__" in node:
                    w = node["__end__"]
                    if w not in found:
                        found.add(w)
                        hits.append(w)
        return hits


class _Store:
    _inst = None
    _mtime = {}  # path -> mtime，用于热加载检测

    @classmethod
    def get(cls):
        if cls._inst is None or cls._changed():
            cls._inst = cls._load()
        return cls._inst

    @classmethod
    def _changed(cls):
        cur = {}
        for cat in _ORDER:
            p = _WORD_DIR / f"{cat}.json"
            if p.exists():
                cur[str(p)] = p.stat().st_mtime
        changed = cur != cls._mtime
        cls._mtime = cur
        return changed

    @classmethod
    def _load(cls):
        lists = {}
        for cat in _ORDER:
            p = _WORD_DIR / f"{cat}.json"
            if p.exists():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    lists[cat] = _Wordlist(cat, data)
                except Exception as e:  # noqa: BLE001
                    print(f"⚠ 词库 {cat} 加载失败：{e}")
        return lists


class Result:
    """检测结果。命中时 hit=True，否则空结果。"""

    def __init__(self, category="", severity="pass", words=None, hint="",
                 bot_instruction="", bot_fallback="", hotline=""):
        self.category = category
        self.severity = severity
        self.words = words or []
        self.hint = hint
        self.bot_instruction = bot_instruction
        self.bot_fallback = bot_fallback
        self.hotline = hotline
        self.hit = bool(category)

    @property
    def blocked(self):
        return self.hit and self.severity == "block"

    @property
    def priority_high(self):
        """心理/伤害类，家长周报单独提示。"""
        return self.hit and self.category == "mental"


def _mask(word):
    """词打码用于日志：首字 + ** 。"""
    if len(word) <= 1:
        return word[0] + "*"
    return word[0] + "*" * (len(word) - 1)


def check(text: str):
    """入口闸：检查用户输入。返回 Result（未命中也返回 Result，hit=False）。"""
    store = _Store.get()
    if not store or not text:
        return Result()
    norm = _normalize(text)

    # 先看 mental（白名单短语优先排除）
    wl = store.get("mental")
    if wl:
        # 白名单短语整句命中，说明是口语（想死你了/累死了），排除对应锚点
        excluded_anchors = set()
        for p in wl._wl_norm:
            if p in norm:
                # 该短语前两字（如"想死""累死"）在本句中属于口语，排除
                excluded_anchors.add(p[:2] if len(p) >= 2 else p)
        hits = wl.scan(norm)
        real = []
        for h in hits:
            anchor = h[:2]
            if anchor in excluded_anchors:
                continue
            # 二次确认：该命中是否被某个白名单短语"包裹"（想死 vs 想死你了）
            if any(h in p and p != h for p in wl._wl_norm if p in norm):
                continue
            real.append(h)
        if real:
            data = wl.raw
            return Result(
                category="mental", severity=wl.severity, words=real, hint=wl.hint,
                bot_instruction=data.get("bot_instruction", ""),
                bot_fallback=data.get("bot_fallback", ""),
                hotline=data.get("hotline", ""),
            )

    # nsfw
    wl = store.get("nsfw")
    if wl:
        hits = wl.scan(norm)
        if hits:
            return Result(category="nsfw", severity=wl.severity, words=hits, hint=wl.hint,
                          bot_fallback=wl.raw.get("bot_fallback", ""))

    # politics
    wl = store.get("politics")
    if wl:
        hits = wl.scan(norm)
        if hits:
            return Result(category="politics", severity=wl.severity, words=hits, hint=wl.hint,
                          bot_fallback=wl.raw.get("bot_fallback", ""))

    # abuse
    wl = store.get("abuse")
    if wl:
        hits = wl.scan(norm)
        if hits:
            return Result(category="abuse", severity=wl.severity, words=hits, hint=wl.hint,
                          bot_instruction=wl.raw.get("bot_instruction", ""))

    return Result()


def check_output(text: str):
    """出口闸：检查模型输出是否含违禁内容。命中色情/政治/骂人则触发兜底替换。

    mental 类对输出不做拦截（模型本就该回应心理问题）。
    返回 (safe_text, Result)；若命中，safe_text 为兜底文案。
    """
    store = _Store.get()
    if not store or not text:
        return text, Result()
    norm = _normalize(text)
    for cat in ("nsfw", "politics", "abuse"):
        wl = store.get(cat)
        if wl and wl.scan(norm):
            fallback = wl.raw.get("bot_fallback") or "这个话题我们换个聊吧～"
            return fallback, Result(
                category=cat, severity="block", words=wl.scan(norm),
                hint=wl.hint, bot_fallback=fallback)
    return text, Result()


def mask_words(words):
    return [_mask(w) for w in words]
