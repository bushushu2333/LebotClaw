"""同步 Agent.chat ↔ NiceGUI/SSE 的桥。

红线：同步核心（Agent.chat / MemoryStore / adapter）绝不能在 NiceGUI 事件循环里
直接调用（会阻塞所有用户/标签页）。必须经 ``run.io_bound`` 丢进线程池。
绝不能用 ``run.cpu_bound``（多进程，需 pickle，adapter/memory 带活动连接与锁无法 pickle）。
"""
from lebotclaw.core.commands import handle_command
from lebotclaw.web.session_manager import SessionContext


def blocking_chat(ctx: SessionContext, user_input: str) -> str:
    """在线程池中执行：命令分流 → 路由 → agent.chat()（保工具+记忆）。

    整个流程在 ``ctx.lock`` 内串行化，防止同会话并发请求交错 _history。
    """
    with ctx.lock:
        # 1. 命令分流（/帮助 /切换 /重置 ...）
        cr = handle_command(user_input, ctx)
        if cr.handled:
            return cr.text
        # 2. 意图路由（可能 switch_to，副作用仅限本会话）
        ctx.classify_and_route(user_input)
        # 3. 跑 agent.chat —— 保留工具调用两轮 + 记忆注入 + summarize
        return ctx.active_agent.chat(user_input)


def blocking_stream_chat(ctx: SessionContext, user_input: str):
    """流式版：命令/路由后，用 chat_stream_with_tools 逐 chunk 输出（不丢工具）。

    同步 generator：供 SSE 直接迭代，或 Web UI 在后台线程迭代写共享 buffer。
    整个流式在 ``ctx.lock`` 内串行化。
    """
    with ctx.lock:
        cr = handle_command(user_input, ctx)
        if cr.handled:
            yield cr.text
            return
        ctx.classify_and_route(user_input)
        for chunk in ctx.active_agent.chat_stream_with_tools(user_input):
            yield chunk


def blocking_stream_events(ctx: SessionContext, user_input: str):
    """事件流版：命令/路由/工具/知识库全部以 dict 事件外露（SSE 用）。

    事件类型：route（学科切换）→ wiki（知识库命中）→ tool（工具调用）→ delta（文本）。
    整个流程在 ``ctx.lock`` 内串行化。
    """
    with ctx.lock:
        cr = handle_command(user_input, ctx)
        if cr.handled:
            yield {"type": "delta", "text": cr.text}
            return
        before = ctx.active_name()
        ctx.classify_and_route(user_input)
        after = ctx.active_name()
        # 路由结果始终外露（changed 标记是否换了伙伴），前端据此亮"谁在接棒"
        yield {"type": "route", "from": before, "to": after, "changed": after != before}
        for ev in ctx.active_agent.stream_events(user_input):
            yield ev


async def chat_and_emit(ctx: SessionContext, user_input: str) -> str:
    """NiceGUI async handler 调用入口。"""
    from nicegui import run
    return await run.io_bound(blocking_chat, ctx, user_input)


# ── 拍照讲题（多模态）──────────────────────────────────────
# 视觉走 seed-2-1-pro（同一 key，实测 coding 端点只有它支持图像输入）

_PHOTO_RULE = """

【拍照讲题】他会拍作业/试卷的照片给你。先认出题目和他的作答：
- 全对：具体夸到点上（哪一步做得好），别泛泛说"真棒"
- 有错：指出错在哪一步、为什么错，然后用提问引导他自己改对，绝不直接报答案
- 看不清的地方老实说看不清，让他重拍一张，别瞎猜"""

_vision_adapter = None


def _get_vision_adapter():
    global _vision_adapter
    if _vision_adapter is None:
        from lebotclaw.adapters.arkcoding import ArkCodingAdapter
        _vision_adapter = ArkCodingAdapter(model="seed-2-1-pro")
    return _vision_adapter


def blocking_photo_chat(image_data_url: str, text: str) -> str:
    """拍照讲题（线程池执行）：视觉模型 + 超级小博人设 + 不给答案规矩。"""
    from lebotclaw.education.heads import HEADSTemplate
    system = HEADSTemplate.general_prompt() + _PHOTO_RULE
    content = [
        {"type": "text", "text": (text or "").strip() or "这是我拍的作业，帮我看看"},
        {"type": "image_url", "image_url": {"url": image_data_url}},
    ]
    resp = _get_vision_adapter().generate(
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": content}],
        max_tokens=4096,
    )
    return resp.content or "这张照片我没太看清，能再拍清楚一点的吗？"


async def api_chat(ctx: SessionContext, user_input: str) -> str:
    """FastAPI /api/chat 路由调用入口（同样走 io_bound，不阻塞事件循环）。"""
    from nicegui import run
    return await run.io_bound(blocking_chat, ctx, user_input)
