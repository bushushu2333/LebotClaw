"""/api/* 路由（挂在 NiceGUI 的 FastAPI app 上）。

供脚本/测试/外部对接使用。聊天同样经 chat_bridge.io_bound，不阻塞事件循环。
多用户：每个需要 per-user 数据的端点接收 uid（query 参数），按 uid 取独立记忆/错题/生词。
"""
import json

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from nicegui import app

from lebotclaw.web.chat_bridge import api_chat, blocking_stream_chat, blocking_stream_events

def _clean_tts_text(text: str) -> str:
    """去掉 markdown 符号/链接，纯文本才喂给 TTS。"""
    import re
    return re.sub(r"[*#`>\[\]()_~]|https?://\S+", "", text).strip() or "嗯"


def _tts_bytes(text: str) -> bytes:
    """edge-tts 生成 mp3（fallback：豆包未配 key 或合成失败时用）。云希少年音。"""
    import asyncio
    import io
    import edge_tts

    buf = io.BytesIO()

    async def _go():
        async for chunk in edge_tts.Communicate(_clean_tts_text(text), "zh-CN-YunxiNeural").stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])

    asyncio.run(_go())
    return buf.getvalue()


# TTS 结果小缓存：同一段文本重复点🔊不必再合成（FIFO，64 条封顶）
_TTS_CACHE: dict = {}


def _gen_title(adapter, messages):
    """LLM 给对话起 5-9 字标题（会话列表用，对齐 ChatGPT 逻辑）。"""
    import re
    convo = "\n".join(
        f"{'学生' if m.get('role') == 'user' else '超级小博'}：{(m.get('content') or '')[:150]}"
        for m in (messages or [])[:6] if m.get("content")
    )
    if not convo.strip():
        return None
    prompt = (
        "请用5到9个汉字给下面这段学习对话起一个简短标题，"
        "概括这次聊的主题（如\"分数加减法\"\"光合作用\"\"唐朝诗人\"\"英语现在时\"）。"
        "只输出标题本身，不要标点、不要引号、不要任何多余的话：\n\n" + convo
    )
    resp = adapter.generate(messages=[{"role": "user", "content": prompt}],
                            max_tokens=512, temperature=0.3)
    t = re.sub(r"[^一-龥A-Za-z0-9]+", "", resp.content or "")
    return t[:9] or None


# 可切换的模型（设置页卡片）
ARKCODING_MODELS = [
    ("deepseek-v4-pro", "DeepSeek V4 Pro", "最强大脑 · 想得深", "🧠"),
    ("glm-5.2", "GLM 5.2", "快如闪电 · 反应快", "⚡"),
    ("seed-2-1-pro", "Seed 2.1 Pro", "灵感多多 · 点子多", "🎨"),
    ("kimi-k2-7", "Kimi K2", "长文高手 · 读得多", "📖"),
]


def _authorized(runtime, request: Request) -> bool:
    """简易 Bearer 校验。api_token 为空则放行（仅本地）。"""
    token = runtime.config.get("web", {}).get("api_token", "")
    if not token:
        return True
    return request.headers.get("authorization", "") == f"Bearer {token}"


def register_api_routes(runtime):
    # per-user 数据目录 / 记忆：uid 为空回落全局（CLI 兼容）
    def ud(uid: str) -> str:
        return runtime.user_dir_for(uid) if uid else "~/.lebotclaw"

    def mem(uid: str):
        return runtime.memory_for(uid) if uid else runtime.memory

    @app.get("/api/models")
    async def models_list():
        """设置页模型卡片：仅开放套餐内子模型（官方 API 按量计费，不开放切换）。"""
        sub = runtime.model_adapters["arkcoding"].model if "arkcoding" in runtime.model_adapters else ""
        options = [
            {"id": f"arkcoding:{mid}", "name": name, "tag": tag, "emoji": emoji}
            for mid, name, tag, emoji in ARKCODING_MODELS
        ]
        current = f"arkcoding:{sub}" if runtime.default_model == "arkcoding" else ""
        return {"current": current, "options": options, "label": runtime.model_label()}

    @app.post("/api/models/select")
    async def models_select(request: Request):
        payload = await request.json()
        sel = (payload.get("id") or "").strip()
        adapter_name, _, sub = sel.partition(":")
        valid = {m for m, _, _, _ in ARKCODING_MODELS}
        if adapter_name != "arkcoding" or sub not in valid:
            return JSONResponse({"error": f"unknown model {sel}"}, status_code=400)
        runtime.switch_model(adapter_name, sub)
        return {"ok": True, "current": sel, "label": runtime.model_label()}

    @app.get("/api/health")
    async def health(uid: str = ""):
        return {
            "ok": True,
            "student": runtime.student_name(uid),
            "has_model": runtime.has_model(),
            "default_model": runtime.model_label(),
            "adapter": runtime.default_model,
            "active_sessions": len(runtime.sessions.list_sessions()) if runtime.sessions else 0,
        }

    @app.post("/api/chat")
    async def chat(request: Request, uid: str = ""):
        if not _authorized(runtime, request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            payload = {}
        sid = payload.get("session_id") or "api-default"
        message = payload.get("message", "")
        ctx = runtime.sessions.get_or_create(sid, channel="web", uid=uid)
        reply = await api_chat(ctx, message)
        return {"session_id": ctx.sid, "reply": reply}

    @app.get("/api/chat/stream")
    async def chat_stream(message: str, session_id: str = "api-stream", uid: str = ""):
        """SSE 事件流：route/wiki/tool/delta 事件逐条下发（保留工具调用并外露）。

        同步 generator 由 Starlette 在 threadpool 迭代，不阻塞事件循环。
        """
        ctx = runtime.sessions.get_or_create(session_id, channel="web", uid=uid)

        def gen():
            for ev in blocking_stream_events(ctx, message):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            gen(), media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    @app.post("/api/bridge/messages")
    async def bridge_messages(request: Request):
        """微信 sidecar 占位契约。本期未接 sidecar → 501。"""
        return JSONResponse(
            {
                "error": "wechat bridge not implemented",
                "hint": "本期微信通道仅留接口，未接 sidecar（规避非官方协议封号风险）",
            },
            status_code=501,
        )

    # ── cron jobs（scheduler 未启用时返回 503）──

    @app.get("/api/jobs")
    async def list_jobs():
        if not runtime.scheduler:
            return JSONResponse({"error": "scheduler disabled"}, status_code=503)
        return {"jobs": [j.to_dict() for j in runtime.scheduler.list_jobs()]}

    @app.post("/api/jobs")
    async def create_job(request: Request):
        if not runtime.scheduler:
            return JSONResponse({"error": "scheduler disabled"}, status_code=503)
        from lebotclaw.scheduler.models import Job, TaskType, new_job_id
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            payload = {}
        job = Job(
            id=new_job_id(),
            task_type=TaskType(payload.get("task_type", "custom_prompt")),
            cron=payload.get("cron", "0 9 * * *"),
            prompt=payload.get("prompt", ""),
            channel=payload.get("channel"),
            chat_id=payload.get("chat_id"),
            one_shot=payload.get("one_shot", False),
            name=(payload.get("prompt") or payload.get("task_type", ""))[:20],
        )
        runtime.scheduler.add(job)
        return {"job": job.to_dict()}

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(job_id: str):
        if not runtime.scheduler:
            return JSONResponse({"error": "scheduler disabled"}, status_code=503)
        runtime.scheduler.remove(job_id)
        return {"deleted": job_id}

    @app.post("/api/jobs/{job_id}/run")
    async def run_job(job_id: str):
        if not runtime.scheduler:
            return JSONResponse({"error": "scheduler disabled"}, status_code=503)
        from nicegui import run
        result = await run.io_bound(runtime.scheduler.run_now, job_id)
        return {"run": result}

    # ── dashboard 数据接口 ──

    @app.get("/api/overview")
    async def overview(uid: str = ""):
        """侧栏状态 + 顶栏模型徽标。"""
        fcfg = runtime.config.get("channels", {}).get("feishu", {})
        return {
            "ok": True,
            "student": runtime.student_name(uid),
            "has_model": runtime.has_model(),
            "default_model": runtime.model_label(),
            "adapter": runtime.default_model,
            "active_sessions": len(runtime.sessions.list_sessions()) if runtime.sessions else 0,
            "wiki_pages": len(runtime.wiki.list_pages()),
            "feishu_enabled": bool(fcfg.get("enabled") and fcfg.get("app_id")),
            "scheduler_enabled": runtime.scheduler is not None,
        }

    @app.get("/api/memory")
    async def memory_all(uid: str = ""):
        """4 类记忆，dashboard 记忆页用。"""
        from nicegui import run
        m = mem(uid)
        cats = ["student_profile", "learning_progress", "skill_memory", "session_summary"]

        def _load():
            return {
                c: [
                    {"key": e.key, "content": e.content, "tags": e.tags,
                     "updated_at": e.updated_at}
                    for e in reversed(m.search_memory(category=c, limit=50))
                ]
                for c in cats
            }
        return {"memory": await run.io_bound(_load)}

    @app.get("/api/starmap")
    async def starmap(uid: str = ""):
        """知识星图：知识页=星星（聊过的点亮）、已掌握错题=金星。"""
        from lebotclaw.tools.builtin.store import JsonListStore
        d = ud(uid)
        covered: dict = {}
        for c in JsonListStore(f"{d}/covered.json").all():
            t = c.get("title", "")
            if t:
                covered[t] = covered.get(t, 0) + 1
        stars = [
            {"title": p.title, "covered": covered.get(p.title, 0) > 0, "hits": covered.get(p.title, 0)}
            for p in runtime.wiki.list_pages()
        ]
        gold = [
            {"title": i.get("question", "")[:24], "note": i.get("note", "")[:30]}
            for i in JsonListStore(f"{d}/mistakes.json").all()
            if i.get("mastered")
        ]
        return {"stars": stars, "gold": gold,
                "covered_count": sum(1 for s in stars if s["covered"])}

    @app.get("/api/report/weekly")
    async def report_weekly(uid: str = ""):
        """家长周报：有缓存先返回缓存（带上 stats 供页面渲染）。"""
        from lebotclaw.web import report as report_mod
        d = ud(uid)
        cached = report_mod.cached_report(d)
        if not cached:
            return {"report": None, "stats": report_mod.collect_stats(mem(uid), d)}
        return {"report": cached["text"], "generated_at": cached["generated_at"], "stats": cached.get("stats", {})}

    @app.post("/api/report/weekly/refresh")
    async def report_weekly_refresh(uid: str = ""):
        """重新生成周报（LLM 阻塞调用走 io_bound）。"""
        from nicegui import run
        from lebotclaw.web import report as report_mod
        d = ud(uid)
        adapter = runtime.model_adapters.get(runtime.default_model)
        if adapter is None:
            return JSONResponse({"error": "no model"}, status_code=503)
        text = await run.io_bound(report_mod.generate_report, adapter, mem(uid), d)
        cached = report_mod.cached_report(d) or {}
        return {"report": text, "generated_at": cached.get("generated_at"), "stats": cached.get("stats", {})}

    @app.post("/api/quiz/generate")
    async def quiz_generate(request: Request, uid: str = ""):
        """按错题生成专属选择题（LLM 出题，走 io_bound 不阻塞事件循环）。"""
        from nicegui import run
        from lebotclaw.web import quiz as quiz_mod
        d = ud(uid)
        payload = await request.json()
        adapter = runtime.model_adapters.get(runtime.default_model)
        if adapter is None:
            return JSONResponse({"error": "no model"}, status_code=503)
        qz = await run.io_bound(
            quiz_mod.generate_quiz, adapter, mem(uid),
            payload.get("mistake_ids") or [], int(payload.get("count", 3)), d,
        )
        if not qz:
            return JSONResponse({"error": "错题本还是空的，先去聊几道错题吧"}, status_code=400)
        return {"quiz_id": qz["id"], "count": len(qz["questions"])}

    @app.get("/api/quiz/{quiz_id}")
    async def quiz_get(quiz_id: str, uid: str = ""):
        from lebotclaw.web import quiz as quiz_mod
        qz = quiz_mod.get_quiz(quiz_id, ud(uid))
        if not qz:
            return JSONResponse({"error": "not found"}, status_code=404)
        return quiz_mod.public_quiz(qz)

    @app.post("/api/quiz/answer")
    async def quiz_answer(request: Request, uid: str = ""):
        from lebotclaw.web import quiz as quiz_mod
        payload = await request.json()
        r = quiz_mod.answer_question(
            payload.get("quiz_id", ""), int(payload.get("q_index", 0)), payload.get("choice", ""), ud(uid))
        if r is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return r

    @app.get("/api/tts")
    async def tts(text: str):
        """小博开口说话：优先豆包假小子 2.0（配了 DOUBAO_TTS_API_KEY 时），失败回退 edge-tts。"""
        import asyncio

        from fastapi.responses import Response

        clean = _clean_tts_text(text[:300])
        if clean in _TTS_CACHE:
            audio = _TTS_CACHE[clean]
        else:
            audio = None
            from lebotclaw.web import tts_doubao
            if tts_doubao.available():
                try:
                    audio = await asyncio.wait_for(tts_doubao.synth(clean), timeout=30)
                except Exception as e:
                    print(f"⚠ 豆包 TTS 失败，回退 edge-tts：{e}")
            if not audio:
                from nicegui import run
                audio = await run.io_bound(_tts_bytes, clean)
            if len(_TTS_CACHE) >= 64:
                _TTS_CACHE.pop(next(iter(_TTS_CACHE)))
            _TTS_CACHE[clean] = audio
        return Response(content=audio, media_type="audio/mpeg",
                        headers={"Cache-Control": "max-age=3600"})

    @app.post("/api/chat/photo")
    async def chat_photo(request: Request):
        """拍照讲题：图片走 seed-2-1-pro 视觉模型（io_bound 不阻塞事件循环）。"""
        from nicegui import run
        from lebotclaw.web.chat_bridge import blocking_photo_chat
        payload = await request.json()
        image = payload.get("image", "")
        if not image.startswith("data:image"):
            return JSONResponse({"error": "需要图片"}, status_code=400)
        reply = await run.io_bound(blocking_photo_chat, image, payload.get("text", ""))
        return {"reply": reply}

    @app.get("/api/proactive")
    async def proactive(consume: bool = False, uid: str = ""):
        """小博主动来信：晨间问候/错题间隔重复/生日。consume=1 时标记已发。"""
        from lebotclaw.web.proactive import pending_messages
        return {"messages": pending_messages(mem(uid), consume=consume, user_dir=ud(uid))}

    @app.get("/api/mistakes")
    async def mistakes_list(uid: str = ""):
        """错题本列表（记忆页页签用），未掌握在前。"""
        from lebotclaw.tools.builtin.store import JsonListStore
        items = JsonListStore(f"{ud(uid)}/mistakes.json").all()
        items.sort(key=lambda i: (i.get("mastered", False), -i.get("created_at", 0)))
        return {"items": items}

    @app.get("/api/words")
    async def words_list(uid: str = ""):
        """生词本列表（记忆页页签用），未掌握在前。"""
        from lebotclaw.tools.builtin.store import JsonListStore
        items = JsonListStore(f"{ud(uid)}/wordbank.json").all()
        items.sort(key=lambda i: (i.get("mastered", False), -i.get("created_at", 0)))
        return {"items": items}

    @app.get("/api/wiki")
    async def wiki_list():
        pages = runtime.wiki.list_pages()
        return {"pages": [
            {"id": p.id, "title": p.title, "content": p.content,
             "source": p.source, "tags": p.tags}
            for p in pages
        ]}

    @app.post("/api/wiki")
    async def wiki_add(request: Request):
        payload = await request.json()
        title = (payload.get("title") or "").strip()
        content = (payload.get("content") or "").strip()
        if not title or not content:
            return JSONResponse({"error": "title/content required"}, status_code=400)
        pid = runtime.wiki.add_page(title, content,
                                    source=payload.get("source", ""),
                                    tags=payload.get("tags") or [])
        return {"id": pid}

    @app.delete("/api/wiki/{page_id}")
    async def wiki_del(page_id: str):
        runtime.wiki.delete_page(page_id)
        return {"deleted": page_id}

    @app.post("/api/plan")
    async def make_plan(request: Request, uid: str = ""):
        from nicegui import run
        from lebotclaw.core.planner import Planner
        payload = await request.json()
        goal = (payload.get("goal") or "").strip()
        if not goal:
            return JSONResponse({"error": "goal required"}, status_code=400)
        grade = (mem(uid).get_student_profile().get("年级", "") if uid else runtime.config.get("grade", ""))
        plan = await run.io_bound(Planner().decompose, goal, "", grade)
        return {"goal": goal, "steps": [
            {"id": s.id, "title": s.title, "description": s.description,
             "status": s.status}
            for s in plan.steps
        ]}

    @app.get("/api/profile")
    async def profile_get(uid: str = ""):
        m = mem(uid)
        prof = m.get_student_profile()
        return {
            "name": prof.get("名字", "") or (runtime.config.get("student_name", "") if not uid else ""),
            "grade": prof.get("年级", "") or (runtime.config.get("grade", "") if not uid else ""),
            "style": runtime.config.get("style", "warm"),
            "has_model": runtime.has_model(),
            "default_model": runtime.model_label(),
            "available_models": list(runtime.model_adapters.keys()),
            "profile": prof,
        }

    @app.post("/api/profile")
    async def profile_save(request: Request, uid: str = ""):
        from lebotclaw.core import cli as cli_mod
        payload = await request.json()
        name = (payload.get("name") or "").strip()
        grade = (payload.get("grade") or "").strip()
        m = mem(uid)
        if name:
            m.save_memory("student_profile", "general", "名字", name, ["名字"])
        if grade:
            m.save_memory("student_profile", "general", "年级", grade, ["年级"])
        if not uid:  # 全局模式才写 config（兼容旧单用户），per-user 名字只存 memory
            cfg = cli_mod._load_config()
            cfg["student_name"] = name
            cfg["grade"] = grade
            cli_mod._save_config(cfg)
            runtime.config["student_name"] = name
            runtime.config["grade"] = grade
        return {"ok": True}

    @app.get("/api/session/info")
    async def session_info(session_id: str = ""):
        ctx = runtime.sessions.get(session_id) if session_id else None
        return {"active_subject": ctx.active_name() if ctx else "general"}

    @app.post("/api/session/subject")
    async def session_subject(request: Request, uid: str = ""):
        payload = await request.json()
        subject = payload.get("subject", "general")
        ctx = runtime.sessions.get_or_create(payload.get("session_id"), channel="web", uid=uid)
        with ctx.lock:
            try:
                ctx.registry.switch_to(subject)
                ctx._active_name = subject
            except KeyError:
                return JSONResponse({"error": f"unknown subject {subject}"}, status_code=400)
        return {"active_subject": subject}

    @app.post("/api/session/title")
    async def session_title(request: Request):
        """对话满3轮后，LLM 用5-9字总结这次对话作为会话标题（对齐 ChatGPT）。"""
        from nicegui import run
        payload = await request.json()
        messages = payload.get("messages") or []
        if len([m for m in messages if m.get("role") == "user"]) < 3:
            return {"title": None}
        adapter = runtime.model_adapters.get(runtime.default_model)
        if adapter is None:
            return JSONResponse({"error": "no model"}, status_code=503)
        title = await run.io_bound(_gen_title, adapter, messages)
        return {"title": title}

    # ── 内容守护：词库管理（增删词，引擎热加载自动生效）──
    @app.get("/api/moderation/words")
    async def mod_words_list():
        from lebotclaw.core import moderation as _mod
        out = {}
        for cat, wl in _mod._Store.get().items():
            out[cat] = {"severity": wl.severity, "hint": wl.hint,
                        "count": len(wl.words), "words": wl.words}
        return out

    @app.post("/api/moderation/words")
    async def mod_words_edit(request: Request):
        from pathlib import Path as _Path
        from lebotclaw.core import moderation as _mod
        payload = await request.json()
        cat = payload.get("category", "")
        word = (payload.get("word") or "").strip()
        action = payload.get("action", "add")
        if cat not in _mod._ORDER or not word:
            return JSONResponse({"error": "bad category/word"}, status_code=400)
        p = _mod._WORD_DIR / f"{cat}.json"
        if not p.exists():
            return JSONResponse({"error": "wordlist not found"}, status_code=404)
        data = json.loads(p.read_text(encoding="utf-8"))
        words = [w for w in data.get("words", []) if isinstance(w, str) and not w.startswith("_")]
        nw = _mod._normalize(word)
        if action == "add":
            if nw and nw not in [_mod._normalize(w) for w in words]:
                words.append(word)
        elif action == "del":
            words = [w for w in words if _mod._normalize(w) != nw]
        else:
            return JSONResponse({"error": "bad action"}, status_code=400)
        data["words"] = words
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        _mod._Store._inst = None  # 触发下次请求热重载
        return {"ok": True, "category": cat, "count": len(words)}
