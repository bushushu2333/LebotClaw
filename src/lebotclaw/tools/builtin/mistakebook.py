from lebotclaw.tools.base import Tool, ToolResult
from lebotclaw.tools.builtin.store import JsonListStore

_SUBJECTS = {"math": "数学", "chinese": "语文", "science": "科学", "general": "通用"}


class MistakeBookTool(Tool):
    name = "mistake_book"
    description = (
        "错题本：帮学生记录、查看和复习错题。"
        "action=add 记一道错题（需 question，最好带 wrong_answer/correct_answer/note）；"
        "action=list 列出错题（可按 subject 过滤，未掌握的排前面）；"
        "action=review 把某道错题标记为已掌握（需 mistake_id）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "review"],
                "description": "add=记错题, list=看错题本, review=标记已掌握",
            },
            "subject": {
                "type": "string",
                "description": "学科，如 math/chinese/science，add 时建议填，list 时用于过滤",
            },
            "question": {"type": "string", "description": "题目内容（add 必填）"},
            "wrong_answer": {"type": "string", "description": "学生当时的错误答案"},
            "correct_answer": {"type": "string", "description": "正确答案"},
            "note": {"type": "string", "description": "错因/易错点，如'忘记通分'"},
            "mistake_id": {"type": "integer", "description": "错题编号（review 必填）"},
        },
        "required": ["action"],
    }

    def __init__(self, store=None):
        # store 按用户隔离：Web 建会话时传入 per-user 路径，CLI 默认全局
        self.store = store or JsonListStore("~/.lebotclaw/mistakes.json")

    def execute(self, **kwargs) -> ToolResult:
        action = kwargs.get("action", "").strip()
        if action == "add":
            return self._add(kwargs)
        if action == "list":
            return self._list(kwargs)
        if action == "review":
            return self._review(kwargs)
        return ToolResult(success=False, output="", error=f"unknown action: {action}")

    def _add(self, kw) -> ToolResult:
        question = (kw.get("question") or "").strip()
        if not question:
            return ToolResult(success=False, output="", error="question 不能为空")
        item = self.store.add({
            "subject": (kw.get("subject") or "general").strip(),
            "question": question,
            "wrong_answer": (kw.get("wrong_answer") or "").strip(),
            "correct_answer": (kw.get("correct_answer") or "").strip(),
            "note": (kw.get("note") or "").strip(),
            "mastered": False,
        })
        return ToolResult(
            success=True,
            output=f"已记到错题本（第 {item['id']} 题）：{question[:40]}",
            metadata={"id": item["id"]},
        )

    def _list(self, kw) -> ToolResult:
        subject = (kw.get("subject") or "").strip()
        items = self.store.all()
        if subject:
            items = [i for i in items if i.get("subject") == subject]
        if not items:
            return ToolResult(success=True, output="错题本还是空的，继续保持！")
        items.sort(key=lambda i: (i.get("mastered", False), -i.get("created_at", 0)))
        lines = []
        for i in items[:20]:
            mark = "✅" if i.get("mastered") else "📌"
            sub = _SUBJECTS.get(i.get("subject"), i.get("subject", ""))
            line = f"{mark} #{i['id']} [{sub}] {i['question'][:50]}"
            if i.get("note"):
                line += f"（易错点：{i['note']}）"
            lines.append(line)
        return ToolResult(success=True, output="\n".join(lines),
                          metadata={"count": len(items)})

    def _review(self, kw) -> ToolResult:
        mid = kw.get("mistake_id")
        if not mid:
            return ToolResult(success=False, output="", error="mistake_id 不能为空")
        item = self.store.update(int(mid), mastered=True)
        if not item:
            return ToolResult(success=False, output="", error=f"没找到第 {mid} 题")
        return ToolResult(success=True, output=f"第 {mid} 题已标记为掌握 ✅")
