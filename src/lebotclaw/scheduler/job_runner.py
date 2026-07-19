"""单次 job 执行：决定内容（直推 / 跑 agent）→ 推送到通道 → 记运行日志。

infer_target 三级回落：job.channel/chat_id → 通道默认目标 → scheduler.default_*。
"""
import time

from lebotclaw.channels.base import PushTarget
from lebotclaw.scheduler.models import TaskType
from lebotclaw.scheduler.prompts import build_prompt


def _infer_target(runtime, job):
    """返回 (channel, chat_id) 或 (None, None)。"""
    if job.channel and job.chat_id:
        return job.channel, job.chat_id
    if runtime.channels:
        t = (runtime.channels.default_target(job.channel) if job.channel
             else runtime.channels.default_target())
        if t:
            return t.channel, t.chat_id
    scfg = runtime.config.get("scheduler", {})
    dc = job.channel or scfg.get("default_channel")
    dci = job.chat_id or scfg.get("default_chat_id")
    if dc and dci:
        return dc, dci
    return None, None


def execute(runtime, job) -> dict:
    from lebotclaw.web.chat_bridge import blocking_chat

    run = {
        "job_id": job.id,
        "task_type": job.task_type.value,
        "ts": time.time(),
        "status": "skipped",
    }

    target_channel, target_chat = _infer_target(runtime, job)
    if not target_channel or not target_chat:
        run["error"] = "no_target"
        job.last_status = "skipped"
        job.last_run_at = time.time()
        return run

    channel = runtime.channels.get(target_channel) if runtime.channels else None
    if not channel:
        run["error"] = f"channel {target_channel} not registered"
        job.last_status = "skipped"
        job.last_run_at = time.time()
        return run

    try:
        if job.task_type == TaskType.PUSH_REMINDER:
            output = job.prompt  # 不跑 LLM，直接推
        else:
            prompt = build_prompt(job.task_type, runtime, job.prompt)
            ctx = runtime.sessions.by_channel_key(target_channel, target_chat)
            output = blocking_chat(ctx, prompt)
        channel.push(PushTarget(target_channel, target_chat), output)
        run["status"] = "ok"
        run["chars"] = len(output)
        job.last_status = "ok"
    except Exception as e:  # noqa: BLE001
        run["status"] = "error"
        run["error"] = str(e)[:200]
        job.last_status = "error"

    job.last_run_at = time.time()
    job.run_count += 1
    return run
