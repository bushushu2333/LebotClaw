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
    """事件流版：命令/路由/工具/知识库/内容守护全部以 dict 事件外露（SSE 用）。

    事件类型：route（学科切换）→ moderation（违禁/心理命中）→ wiki → tool → delta。
    整个流程在 ``ctx.lock`` 内串行化。

    内容守护双闸口：
    - 入口闸：先查 user_input。nsfw/politics 命中→拦截（不走LLM，固定话术）；
      mental/abuse 命中→放行，注入化解指令 + 发 moderation 事件让前端弹窗。
    - 出口闸：聚合 delta 文本，结束后检查模型是否吐了违禁词（告警，不破坏流式）。
    """
    from lebotclaw.core import moderation
    from lebotclaw.web import moderation_log

    with ctx.lock:
        # ── 入口闸：内容守护 ──
        mod = moderation.check(user_input)
        if mod.hit:
            moderation_log.log_hit(ctx.uid, mod)
            yield {
                "type": "moderation",
                "category": mod.category,
                "severity": mod.severity,
                "hint": mod.hint,
                "hotline": mod.hotline,
                "blocked": mod.blocked,
                "high": mod.priority_high,
            }
            if mod.blocked:
                # nsfw/politics：拦截，不走 LLM，固定兜底话术
                yield {"type": "delta", "text": mod.bot_fallback}
                return

        cr = handle_command(user_input, ctx)
        if cr.handled:
            yield {"type": "delta", "text": cr.text}
            return
        before = ctx.active_name()
        ctx.classify_and_route(user_input)
        after = ctx.active_name()
        # 路由结果始终外露（changed 标记是否换了伙伴），前端据此亮"谁在接棒"
        yield {"type": "route", "from": before, "to": after, "changed": after != before}

        # spec v2.1：陪伴计数（touch 活跃日 + 尾部 token 入账，FR-E7）
        ctx.workspace.touch_companion()
        try:
            dm = ctx.workspace.check_day_milestone()
            if dm:
                yield {"type": "companion_milestone", **dm}
        except Exception:  # noqa: BLE001
            pass

        # mental/abuse 放行时，把化解指令注入 system prompt
        extra_system = mod.bot_instruction if (mod.hit and mod.bot_instruction) else ""
        collected = []
        for ev in ctx.active_agent.stream_events(
                user_input, extra_system=extra_system, flow_engine=ctx.flow_engine):
            if ev.get("type") == "delta":
                collected.append(ev.get("text", ""))
            yield ev

        # token 估算入账：无 adapter usage 字段，按中英混排 ~2 字符/token 粗估；
        # 跨里程碑（陪伴天数/token 档位）时补发 companion_milestone 事件
        try:
            ms = ctx.workspace.add_tokens(max(1, (len(user_input) + sum(map(len, collected))) // 2))
            if ms:
                yield {"type": "companion_milestone", **ms}
        except Exception:  # noqa: BLE001
            pass

        # ── 出口闸：模型输出兜底告警（不破坏已流式输出的内容）──
        try:
            full = "".join(collected)
            _safe, out_mod = moderation.check_output(full)
            if out_mod.hit:
                moderation_log.log_hit(ctx.uid, out_mod)
                # 已流式发出的无法替换，但记日志；严重时前端可后续优化
                print(f"⚠ 出口闸命中 [{out_mod.category}] words={out_mod.words}")
        except Exception:  # noqa: BLE001
            pass


async def chat_and_emit(ctx: SessionContext, user_input: str) -> str:
    """NiceGUI async handler 调用入口。"""
    from nicegui import run
    return await run.io_bound(blocking_chat, ctx, user_input)


def collapse_events_to_text(ctx: SessionContext, user_input: str) -> str:
    """纯文本通道（飞书/CLI）透传（spec 1.9）：消费事件流，折叠成单条文本。

    delta 拼成正文；Flow/Skill/陪伴事件转成简短文字行前置，
    让纯文本通道也能看到「工作流形态」而不是裸回答。
    """
    body = []
    meta = []
    for ev in blocking_stream_events(ctx, user_input):
        t = ev.get("type")
        if t == "delta":
            body.append(ev.get("text", ""))
        elif t == "plan":
            titles = " → ".join(n.get("title", "") for n in ev.get("nodes", []))
            meta.append("🗺️ 计划「%s」：%s" % (ev.get("goal", ""), titles))
        elif t == "replanned":
            meta.append("🌱 调整路线：%s" % (ev.get("node") or {}).get("title", ""))
        elif t == "flow_done":
            meta.append("🎉 " + (ev.get("summary") or "任务完成"))
        elif t == "skill_saved":
            meta.append("💾 小博学会了新本领「%s」（图鉴页可查看/撤销）" % ev.get("title", ""))
        elif t == "skill_used":
            meta.append("🎯 复用了本领「%s」" % ev.get("title", ""))
        elif t == "skill_evolved":
            meta.append("✨ 本领「%s」被打磨得更顺手了" % ev.get("title", ""))
        elif t == "companion_milestone":
            if ev.get("kind") == "days":
                meta.append("🎉 今天是小博陪伴你的第 %s 天！" % ev.get("value"))
            else:
                meta.append("🎉 你们一起聊过 %s token 啦！" % ev.get("value"))
    text = "".join(body).strip()
    return ("\n".join(meta) + "\n\n" + text) if meta else text


# ── 拍照讲题（多模态）──────────────────────────────────────
# 视觉走 seed-2-1-pro（同一 key，实测 coding 端点只有它支持图像输入）

_PHOTO_RULE = """

【拍照讲题】他会拍作业/试卷的照片给你。先认出题目和他的作答：
- 全对：具体夸到点上（哪一步做得好），别泛泛说"真棒"
- 有错：指出错在哪一步、为什么错，然后用提问引导他自己改对，绝不直接报答案
- 看不清的地方老实说看不清，让他重拍一张，别瞎猜"""

_vision_adapter = None


def _get_vision_adapter():
    """拍照讲题视觉适配器：优先智云网关（kimi-k2.6 实测真视觉最准），
    无 AIGW key 时回落火山 seed-2-1-pro。"""
    global _vision_adapter
    if _vision_adapter is None:
        import os as _os
        if _os.getenv("AIGW_API_KEY"):
            from lebotclaw.adapters.aigw import AigwAdapter
            _vision_adapter = AigwAdapter(
                model=_os.getenv("AIGW_VISION_MODEL", "kimi-k2.6"))
        else:
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
