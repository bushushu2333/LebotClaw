"""知识库页 '/wiki'：添加 / 浏览 L2 wiki 页面。

添加的知识页会被 agent 每轮按当前问题检索并注入 system prompt（见
Agent._build_system_prompt_with_memory），让回答有据可依。
"""
from nicegui import ui


def register(runtime):
    @ui.page("/wiki")
    def wiki_page():
        ui.label("📚 知识库").classes("text-2xl font-bold m-4")
        with ui.column().classes("w-full max-w-3xl mx-auto p-4 gap-4"):
            # 添加
            with ui.card().classes("w-full"):
                ui.label("添加知识页").classes("font-bold")
                title_in = ui.input("标题（如：光合作用 / 分数加减法）").classes("w-full")
                source_in = ui.input("来源（可选，如：人教版三年级下册）").classes("w-full")
                content_in = ui.textarea(
                    "内容（知识点讲解，agent 会按学生问题自动检索引用）",
                ).classes("w-full")
                tags_in = ui.input("标签（逗号分隔，可选）").classes("w-full")

                def add():
                    t = (title_in.value or "").strip()
                    c = (content_in.value or "").strip()
                    if not t or not c:
                        ui.notify("标题和内容都要填哦", type="warning")
                        return
                    tags = [x.strip() for x in (tags_in.value or "").split(",") if x.strip()]
                    pid = runtime.wiki.add_page(
                        t, c, source=(source_in.value or ""), tags=tags)
                    ui.notify(f"已添加知识页 {pid}", type="positive")
                    title_in.value = source_in.value = content_in.value = tags_in.value = ""
                    ui.navigate.to("/wiki")

                ui.button("添加", on_click=add)

            # 列表
            with ui.card().classes("w-full"):
                pages = runtime.wiki.list_pages()
                ui.label(f"现有知识页（{len(pages)}）").classes("font-bold")
                if not pages:
                    ui.label("（暂无。添加后，agent 回答相关问题时会自动检索引用～）") \
                        .classes("text-gray-500 text-sm")
                for p in pages:
                    with ui.card().classes("w-full bg-gray-50"):
                        with ui.row().classes("w-full items-center justify-between"):
                            ui.label(p.title).classes("font-semibold")
                            if p.source:
                                ui.label(p.source).classes("text-xs text-gray-400")
                        snippet = p.content[:200] + ("…" if len(p.content) > 200 else "")
                        ui.label(snippet).classes("text-sm text-gray-700 break-all")
                        if p.tags:
                            ui.label(f"标签：{p.tags}").classes("text-xs text-gray-400")

        ui.button("← 返回聊天", on_click=lambda: ui.navigate.to("/")).classes("m-4")
