"""记忆页 '/memory'：展示 4 类教育记忆（学生画像/学习进度/已讲知识点/会话摘要）。"""
from nicegui import ui

_CATEGORIES = [
    ("student_profile", "👤 学生画像"),
    ("learning_progress", "📈 学习进度 / 薄弱点"),
    ("skill_memory", "💡 已讲知识点"),
    ("session_summary", "💬 会话摘要"),
]


def register(runtime):
    @ui.page("/memory")
    def memory_page():
        ui.label("🧠 学习记忆").classes("text-2xl font-bold m-4")
        with ui.column().classes("w-full max-w-3xl mx-auto p-4 gap-4"):
            for cat, label in _CATEGORIES:
                entries = runtime.memory.search_memory(category=cat, limit=50)
                with ui.card().classes("w-full"):
                    ui.label(f"{label}（{len(entries)} 条）").classes("font-bold")
                    if not entries:
                        ui.label("（暂无，多用几次就会积累起来～）").classes("text-gray-500 text-sm")
                    for e in reversed(entries[-8:]):
                        content = e.content
                        if len(content) > 140:
                            content = content[:140] + "…"
                        with ui.row().classes("items-start w-full mt-1"):
                            ui.label(e.key).classes("text-xs text-gray-400 w-40 shrink-0")
                            ui.label(content).classes("text-sm break-all")
        ui.button("← 返回聊天", on_click=lambda: ui.navigate.to("/")).classes("m-4")
