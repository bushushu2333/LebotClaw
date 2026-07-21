from lebotclaw.tools.base import Tool, ToolResult
from lebotclaw.tools.builtin.store import JsonListStore


class WordBankTool(Tool):
    name = "word_bank"
    description = (
        "生词本：帮学生收集、查看和复习生字/单词。"
        "action=add 收一个词（需 word，建议带 meaning/example）；"
        "action=list 列出生词（未掌握的排前面）；"
        "action=review 把某个词标记为已掌握（需 word_id）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "review"],
                "description": "add=收词, list=看生词本, review=标记已掌握",
            },
            "word": {"type": "string", "description": "生字或单词（add 必填）"},
            "pinyin": {"type": "string", "description": "拼音或音标"},
            "meaning": {"type": "string", "description": "释义"},
            "example": {"type": "string", "description": "例句"},
            "word_id": {"type": "integer", "description": "词条编号（review 必填）"},
        },
        "required": ["action"],
    }

    def __init__(self, store=None):
        # store 按用户隔离：Web 建会话时传入 per-user 路径，CLI 默认全局
        self.store = store or JsonListStore("~/.lebotclaw/wordbank.json")

    def execute(self, **kwargs) -> ToolResult:
        action = kwargs.get("action", "").strip()
        if action == "add":
            return self._add(kwargs)
        if action == "list":
            return self._list()
        if action == "review":
            return self._review(kwargs)
        return ToolResult(success=False, output="", error=f"unknown action: {action}")

    def _add(self, kw) -> ToolResult:
        word = (kw.get("word") or "").strip()
        if not word:
            return ToolResult(success=False, output="", error="word 不能为空")
        for it in self.store.all():
            if it.get("word") == word and not it.get("mastered"):
                return ToolResult(success=True,
                                  output=f"「{word}」已经在生词本里啦（第 {it['id']} 条）",
                                  metadata={"id": it["id"]})
        item = self.store.add({
            "word": word,
            "pinyin": (kw.get("pinyin") or "").strip(),
            "meaning": (kw.get("meaning") or "").strip(),
            "example": (kw.get("example") or "").strip(),
            "mastered": False,
        })
        return ToolResult(
            success=True,
            output=f"已收进生词本（第 {item['id']} 条）：{word}",
            metadata={"id": item["id"]},
        )

    def _list(self) -> ToolResult:
        items = self.store.all()
        if not items:
            return ToolResult(success=True, output="生词本还是空的，遇到新词随时告诉我～")
        items.sort(key=lambda i: (i.get("mastered", False), -i.get("created_at", 0)))
        lines = []
        for i in items[:30]:
            mark = "✅" if i.get("mastered") else "📌"
            line = f"{mark} #{i['id']} {i['word']}"
            if i.get("pinyin"):
                line += f"（{i['pinyin']}）"
            if i.get("meaning"):
                line += f"：{i['meaning'][:40]}"
            lines.append(line)
        return ToolResult(success=True, output="\n".join(lines),
                          metadata={"count": len(items)})

    def _review(self, kw) -> ToolResult:
        wid = kw.get("word_id")
        if not wid:
            return ToolResult(success=False, output="", error="word_id 不能为空")
        item = self.store.update(int(wid), mastered=True)
        if not item:
            return ToolResult(success=False, output="", error=f"没找到第 {wid} 条")
        return ToolResult(success=True, output=f"「{item['word']}」已标记为掌握 ✅")
