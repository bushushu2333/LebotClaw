"""聊天主页 '/'：学科切换 + 对话流（流式输出）+ 输入框。

流式实现：后台线程迭代 blocking_stream_chat 写共享 state，
ui.timer 每 0.15s 把 state 刷到 UI（NiceGUI 在 client context 定时回调，安全）。
"""
import threading

from nicegui import app, ui

from lebotclaw.core.commands import SUBJECT_LABELS
from lebotclaw.web.chat_bridge import blocking_stream_chat


def _switch_subject(ctx, name: str):
    with ctx.lock:
        try:
            ctx.registry.switch_to(name)
            ctx._active_name = name
            return True
        except KeyError:
            return False


def register(runtime):
    @ui.page("/classic")  # 旧版 UI 兜底；'/' 已由 dashboard SPA 接管（同时抑制 NiceGUI auto-index）
    def chat_page():
        # —— 会话 ——
        sid = app.storage.user.get("sid")
        ctx = runtime.sessions.get_or_create(sid, channel="web")
        app.storage.user["sid"] = ctx.sid

        # —— 顶栏 ——
        with ui.header().classes("items-center justify-between"):
            ui.label("🐾 LebotClaw").classes("text-xl font-bold")
            current_label = ui.label().classes("text-sm opacity-80")
            with ui.row():
                ui.link("记忆", "/memory").classes("text-sm")
                ui.link("知识库", "/wiki").classes("text-sm")
                ui.link("计划", "/plan").classes("text-sm")
                ui.link("设置", "/settings").classes("text-sm")

        def update_current():
            current_label.text = "当前：" + SUBJECT_LABELS.get(ctx.active_name(), ctx.active_name())
        update_current()

        # —— 左侧学科抽屉 ——
        with ui.left_drawer(top_corner=True, bottom_corner=True).props("width=200"):
            ui.label("学科小伙伴").classes("font-bold mb-2")
            for name, label in SUBJECT_LABELS.items():
                def make_cb(n=name):
                    def cb():
                        _switch_subject(ctx, n)
                        update_current()
                    return cb
                ui.button(label, on_click=make_cb()).classes("w-full mb-1").props("align=left")

        # —— 消息流 ——
        messages: list[dict] = []

        @ui.refreshable
        def message_list():
            if not messages:
                ui.label("👋 直接问问题就行，我会自动找最合适的小伙伴来帮你～").classes("text-gray-500")
                if not runtime.has_model():
                    with ui.card().classes("bg-orange-100"):
                        ui.markdown("⚠ **还没连上 AI 大脑**，先去 [设置](/settings) 配一个模型密钥。"
                                    "现在算术（如 `3.14*2.5`）、查字典仍可用。")
            for m in messages:
                with ui.chat_message(name=m["name"], sent=m["sent"]):
                    ui.markdown(m["text"])

        with ui.column().classes("w-full max-w-3xl mx-auto p-4 gap-2"):
            message_list()

        # —— 输入区 ——
        with ui.footer().classes("bg-transparent border-t"):
            with ui.row().classes("w-full max-w-3xl mx-auto items-center p-2"):
                inp = ui.input(placeholder="问点什么…（Enter 发送，/帮助 查命令）") \
                    .props("autogrow outlined").classes("flex-grow")
                send_btn = ui.button(icon="send", color="primary")

            bot_name = runtime.config.get("channels", {}).get("feishu", {}).get("bot_name", "小博")

            async def send():
                text = (inp.value or "").strip()
                if not text:
                    return
                inp.value = ""
                send_btn.disable()
                messages.append({"name": runtime.student_name() or "你", "sent": True, "text": text})
                messages.append({"name": bot_name, "sent": False, "text": "⏳ 思考中…"})
                message_list.refresh()

                state = {"full": "", "done": False}

                def _worker():
                    try:
                        for chunk in blocking_stream_chat(ctx, text):
                            state["full"] += chunk
                    except Exception as e:  # noqa: BLE001
                        state["full"] = (state["full"] or "") + f"\n\n⚠ 出了点问题：{e}"
                    finally:
                        state["done"] = True

                threading.Thread(target=_worker, daemon=True).start()

                def _tick():
                    if state["full"]:
                        messages[-1]["text"] = state["full"]
                        message_list.refresh()
                    if state["done"]:
                        send_btn.enable()
                        update_current()  # 路由可能切换了学科
                        return False  # 停止 timer
                    return True

                ui.timer(0.15, _tick)

            inp.on("keydown.enter", send)
            send_btn.on("click", send)
