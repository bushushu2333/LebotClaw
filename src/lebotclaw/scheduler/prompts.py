"""taskType → 提示模板：把学生画像/薄弱点拼进提示，交给 agent 生成内容。"""
from lebotclaw.scheduler.models import TaskType


def build_prompt(task_type: TaskType, runtime, base_prompt: str = "") -> str:
    profile = runtime.memory.get_student_profile()
    name = profile.get("名字", "同学")
    grade = profile.get("年级", "")
    ctx = f"学生：{name}，{grade}。" if grade else f"学生：{name}。"

    if task_type == TaskType.DAILY_REVIEW:
        return f"{ctx}请根据我的学习情况，生成一份简短的今日复习清单（3-5 条，具体可执行，语气鼓励）。{base_prompt}"
    if task_type == TaskType.WEEKLY_SUMMARY:
        return f"{ctx}请汇总我本周的学习进度，给出鼓励和下周建议（简短）。{base_prompt}"
    if task_type == TaskType.SPACED_REVIEW:
        weak = runtime.memory.search_memory(category="learning_progress", limit=3)
        weak_txt = "；".join(e.key for e in weak) or "暂无记录"
        return f"{ctx}我的薄弱点：{weak_txt}。请针对这些做一次间隔复习提醒。{base_prompt}"
    # CUSTOM_PROMPT 等
    return base_prompt or "请给我一个简短的学习提醒。"
