"""定时任务页 '/jobs'：新建 / 列出 / 立即运行 cron job。"""
from nicegui import ui

from lebotclaw.scheduler.models import TaskType, new_job_id, Job


def register(runtime):
    @ui.page("/jobs")
    def jobs_page():
        ui.label("⏰ 定时任务").classes("text-2xl font-bold m-4")
        sched = runtime.scheduler
        enabled = sched is not None

        with ui.column().classes("w-full max-w-3xl mx-auto p-4 gap-4"):
            if not enabled:
                with ui.card().classes("bg-orange-100 w-full"):
                    ui.markdown("⚠ **cron 未启用**。在 `~/.lebotclaw/config.json` 设 "
                                "`scheduler.enabled=true` 并配飞书 `default_chat_id` 后重启即可。")

            # 新建
            with ui.card().classes("w-full"):
                ui.label("新建任务").classes("font-bold")
                tt = ui.select(
                    {t.value: t.value for t in TaskType}, value="push_reminder",
                ).classes("w-full")
                cron_in = ui.input(
                    "cron 表达式（分 时 日 月 周）",
                    value="0 21 * * *",
                    placeholder="0 21 * * * = 每天 21:00",
                ).classes("w-full")
                prompt_in = ui.input(
                    "内容 / 提示",
                    placeholder="push_reminder 填要推送的文本；其余填给 AI 的提示（可空）",
                ).classes("w-full")
                chat_in = ui.input(
                    "飞书 chat_id（可空，回落默认）",
                    value=runtime.config.get("channels", {}).get("feishu", {}).get("default_chat_id", ""),
                ).classes("w-full")

                async def add_job():
                    if not enabled:
                        ui.notify("cron 未启用", type="warning")
                        return
                    job = Job(
                        id=new_job_id(),
                        task_type=TaskType(tt.value),
                        cron=cron_in.value.strip() or "0 9 * * *",
                        prompt=prompt_in.value or "",
                        channel="feishu" if chat_in.value else None,
                        chat_id=chat_in.value or None,
                        name=(prompt_in.value or tt.value)[:20],
                    )
                    sched.add(job)
                    ui.notify(f"已添加任务 {job.id}", type="positive")

                ui.button("添加任务", on_click=add_job)

            # 列表
            with ui.card().classes("w-full"):
                ui.label("现有任务").classes("font-bold")
                jobs = sched.list_jobs() if enabled else []
                if not jobs:
                    ui.label("（暂无）").classes("text-gray-500 text-sm")
                for j in jobs:
                    with ui.row().classes("w-full items-center gap-2"):
                        ui.label(f"[{j.task_type.value}] {j.cron}").classes("font-mono text-sm w-56")
                        ui.label((j.prompt or "")[:30]).classes("text-sm flex-grow truncate")
                        ui.badge("启用" if j.enabled else "停用",
                                 color="green" if j.enabled else "gray")
                        async def run_now(jid=j.id):
                            if not enabled:
                                return
                            from nicegui import run as _run
                            await _run.io_bound(sched.run_now, jid)
                            ui.notify(f"已触发 {jid}", type="positive")
                        ui.button("立即运行", on_click=run_now).props("dense flat")

        ui.button("← 返回聊天", on_click=lambda: ui.navigate.to("/")).classes("m-4")
