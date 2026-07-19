"""计划页 '/plan'：接现有 Planner 生成学习计划（Planner 的首个落地点）。"""
from nicegui import ui

from lebotclaw.core.planner import Planner


def register(runtime):
    @ui.page("/plan")
    def plan_page():
        ui.label("📅 学习计划").classes("text-2xl font-bold m-4")
        grade = runtime.config.get("grade", "")
        with ui.column().classes("w-full max-w-3xl mx-auto p-4 gap-2"):
            goal_in = ui.input(
                "学习目标",
                placeholder="如：复习分数加减法 / 学光合作用 / 写一篇写景作文",
            ).classes("w-full")
            result = ui.column()

            def make_plan():
                goal = (goal_in.value or "").strip()
                if not goal:
                    ui.notify("先填个学习目标吧～", type="warning")
                    return
                plan = Planner().decompose(goal, grade=grade)
                result.clear()
                with result:
                    ui.label(f"把「{goal}」分成 {len(plan.steps)} 步：").classes("font-bold mt-2")
                    for s in plan.steps:
                        with ui.card().classes("w-full bg-blue-50"):
                            ui.label(f"第 {s.id + 1} 步：{s.title}").classes("font-semibold")
                            if s.description:
                                ui.label(s.description).classes("text-sm text-gray-600")

            ui.button("生成计划", on_click=make_plan)
        ui.button("← 返回聊天", on_click=lambda: ui.navigate.to("/")).classes("m-4")
