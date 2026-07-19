"""LebotClaw 公共命令层 —— CLI / Web / IM 三端复用。

把原本硬编码在 ``cli.py`` 主循环里的斜杠命令抽出来，统一成无副作用的
``handle_command``，供 Web 与飞书等新通道复用；CLI 可后续接入。
"""
from dataclasses import dataclass
from typing import Protocol

from lebotclaw.core.agent import AgentRegistry
from lebotclaw.core.memory import MemoryStore


SUBJECT_LABELS = {
    "math": "📐 数学小伙伴",
    "chinese": "📝 语文小伙伴",
    "science": "🔬 科学小伙伴",
    "general": "🌟 万能小伙伴",
}

# /切换 的中英文别名 → agent key
_SUBJECT_ALIASES = {
    "数学": "math", "math": "math",
    "语文": "chinese", "chinese": "chinese",
    "科学": "science", "science": "science",
    "通用": "general", "万能": "general", "general": "general",
}

HELP_TEXT = """\
🧭 命令列表
  /帮助         显示这个列表
  /切换 数学     找数学小伙伴来帮忙（也可：语文 / 科学 / 通用）
  /小伙伴       看看现在有哪些小伙伴
  /我的信息     看看我知道关于你的事
  /重置         清空这轮对话重新开始
  /再见         结束本轮对话

直接输入问题即可，我会自动找最合适的小伙伴来帮你！"""

# /再见 等退出命令的哨兵值，调用方据此决定是否关闭会话/会话
BYE_SENTINEL = "__bye__"


@dataclass
class CommandResult:
    handled: bool          # True=是命令并已处理；False=不是命令，应走正常对话
    text: str = ""         # 给用户的回复文本
    is_bye: bool = False   # 是否为退出命令


class CommandContext(Protocol):
    """命令执行所需的会话上下文（duck-typing）。Web/飞书各自的会话对象实现它。"""
    registry: AgentRegistry
    memory: MemoryStore

    @property
    def active_agent(self):  # type: ignore[empty-body]
        """当前活跃 Agent（property：读 registry.get_active()，跟随 switch_to 变化）。"""
        ...


def current_agent_name(ctx) -> str:
    """当前活跃 agent 名（registry._active_agent 或 general）。"""
    return getattr(ctx.registry, "_active_agent", None) or "general"


def handle_command(text: str, ctx: CommandContext) -> CommandResult:
    """识别并处理斜杠命令；非命令返回 ``handled=False``。

    命令产生的状态变更（switch_to / reset）直接作用于 ``ctx``，调用方无需再处理。
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return CommandResult(handled=False)

    # /帮助
    if stripped in ("/help", "/帮助"):
        return CommandResult(True, HELP_TEXT)

    # /小伙伴
    if stripped in ("/小伙伴", "/agents"):
        cur = current_agent_name(ctx)
        lines = [
            f"{SUBJECT_LABELS.get(name, name)}{'  ← 当前' if name == cur else ''}"
            for name in ctx.registry.list_agents()
        ]
        return CommandResult(True, "现在的小伙伴：\n" + "\n".join(lines))

    # /切换 <subject>
    if stripped.startswith("/切换 ") or stripped.startswith("/switch "):
        parts = stripped.split(maxsplit=1)
        target = parts[1].strip() if len(parts) > 1 else ""
        agent_key = _SUBJECT_ALIASES.get(target, target)
        try:
            ctx.registry.switch_to(agent_key)
            return CommandResult(True, f"好哒，已切换到 {SUBJECT_LABELS.get(agent_key, agent_key)}")
        except KeyError:
            return CommandResult(True, "没找到这个小伙伴，试试：数学、语文、科学、通用")

    # /我的信息
    if stripped in ("/我的信息", "/profile"):
        profile = ctx.memory.get_student_profile()
        if profile:
            lines = [f"{k}：{v}" for k, v in profile.items()]
            return CommandResult(True, "我知道关于你的事：\n" + "\n".join(lines))
        return CommandResult(True, "还不了解你呢，直接问问题吧～")

    # /重置
    if stripped in ("/重置", "/reset"):
        ctx.active_agent.reset()
        return CommandResult(True, "对话已清空！重新开始吧～")

    # /再见 /退出
    if stripped in ("/quit", "/exit", "/q", "/再见", "/退出"):
        return CommandResult(True, "再见！我记住你了，下次见～", is_bye=True)

    # 以 / 开头但未识别
    return CommandResult(True, f"没看懂「{stripped}」，输入 /帮助 看看能做什么～")
