"""设置页 '/settings'：编辑学生信息（写回 config + memory）、查看模型状态。"""
from nicegui import ui

from lebotclaw.core import cli as cli_mod


def register(runtime):
    @ui.page("/settings")
    def settings_page():
        ui.label("⚙️ 设置").classes("text-2xl font-bold m-4")
        profile = runtime.memory.get_student_profile()
        cfg = cli_mod._load_config()

        with ui.column().classes("w-full max-w-3xl mx-auto p-4 gap-4"):
            # 学生信息
            with ui.card().classes("w-full"):
                ui.label("学生信息").classes("font-bold")
                name_in = ui.input("名字", value=profile.get("名字", cfg.get("student_name", "")))
                grade_in = ui.input("年级", value=profile.get("年级", cfg.get("grade", "")))

                def save_profile():
                    runtime.memory.save_memory("student_profile", "general", "名字",
                                               name_in.value, ["名字"])
                    if grade_in.value:
                        runtime.memory.save_memory("student_profile", "general", "年级",
                                                   grade_in.value, ["年级"])
                    cfg2 = cli_mod._load_config()
                    cfg2["student_name"] = name_in.value
                    cfg2["grade"] = grade_in.value
                    cli_mod._save_config(cfg2)
                    runtime.config["student_name"] = name_in.value
                    ui.notify("已保存！", type="positive")

                ui.button("保存", on_click=save_profile)

            # AI 大脑
            with ui.card().classes("w-full"):
                ui.label("AI 大脑").classes("font-bold")
                if runtime.has_model():
                    ui.label(f"✅ 当前模型：{runtime.default_model}").classes("text-green-600")
                else:
                    ui.label("⚠ 还没连上模型。在终端运行 lebotclaw setup 配置 API Key。") \
                        .classes("text-orange-600")
                avail = ", ".join(runtime.model_adapters.keys()) or "无"
                ui.label(f"可用模型：{avail}").classes("text-sm text-gray-500")

            # 对话风格
            with ui.card().classes("w-full"):
                ui.label("对话风格").classes("font-bold")
                ui.label(f"当前：{runtime.config.get('style', 'warm')}"
                         f"（在终端 lebotclaw setup 可切换）").classes("text-sm text-gray-500")

        ui.button("← 返回聊天", on_click=lambda: ui.navigate.to("/")).classes("m-4")
