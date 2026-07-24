"""/api/* 路由（挂在 NiceGUI 的 FastAPI app 上）。

供脚本/测试/外部对接使用。聊天同样经 chat_bridge.io_bound，不阻塞事件循环。
多用户：每个需要 per-user 数据的端点接收 uid（query 参数），按 uid 取独立记忆/错题/生词。
"""
import json

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from nicegui import app

from lebotclaw.core.skillstore import VALID_CATEGORIES
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
        """小博开口说话：优先豆包炀炀 2.0（配了 DOUBAO_TTS_API_KEY 时），失败回退 edge-tts。"""
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

    # ── 技能图鉴（spec FR-S6 / FR-V3）：SKILL.md 文件包 CRUD ──
    # SkillStore 按 uid 缓存：__init__ 会扫外部挂载目录重建索引，
    # 每请求新建一次 = 挂载外部目录后每次 API 调用都全量重扫。
    _skill_stores: dict = {}

    def skill_store(uid: str):
        from lebotclaw.core.skillstore import SkillStore, load_external_dirs
        if uid not in _skill_stores:
            _skill_stores[uid] = SkillStore(
                store_dir=ud(uid), external_dirs=load_external_dirs())
        return _skill_stores[uid]

    def _skill_card(e: dict) -> dict:
        return {
            "slug": e.get("slug", ""),
            "title": e.get("title", e.get("name", "")),
            "category": e.get("category", "task_flow"),
            "status": e.get("status", "active"),
            "source": e.get("source", "internal"),
            "effectiveness": e.get("effectiveness", 0.0),
            "usage_count": e.get("usage_count", 0),
            "parent_note": e.get("parent_note", ""),
            "version": e.get("version", "1.0.0"),
        }

    @app.get("/api/skills")
    async def skills_list(uid: str = "", status: str = ""):
        """图鉴卡片列表（索引读，不含正文）。"""
        from nicegui import run
        items = await run.io_bound(skill_store(uid).list, status)
        return {"skills": [_skill_card(e) for e in items]}

    @app.get("/api/skills/{slug}")
    async def skills_detail(slug: str, uid: str = ""):
        """详情抽屉：SKILL.md 全文 + 复用档案（成长日记数据源）。"""
        from nicegui import run
        store = skill_store(uid)
        entry = await run.io_bound(store.get, slug)
        if not entry:
            return JSONResponse({"error": "skill not found"}, status_code=404)
        import json as _json
        from pathlib import Path as _P
        log_path = _P(ud(uid)) / "skills" / slug / "usage_log.jsonl"
        usage_log = []
        if log_path.exists():
            for line in log_path.read_text(encoding="utf-8").splitlines():
                try:
                    usage_log.append(_json.loads(line))
                except _json.JSONDecodeError:
                    pass
        card = _skill_card({**entry, "slug": slug})
        card["body"] = entry.get("body", "")
        card["usage_log"] = usage_log[-50:]
        return card

    @app.post("/api/skills")
    async def skills_create(request: Request, uid: str = ""):
        """手动新建（frontmatter 表单 + Markdown 正文）。"""
        from nicegui import run
        payload = await request.json()
        title = (payload.get("title") or "").strip()
        body = (payload.get("body") or "").strip()
        if not title or not body:
            return JSONResponse({"error": "title/body required"}, status_code=400)
        slug = await run.io_bound(skill_store(uid).add, {
            "title": title, "body": body,
            "category": payload.get("category", "task_flow"),
            "trigger": payload.get("trigger", ""),
            "parent_note": payload.get("parent_note", ""),
        })
        return {"ok": True, "slug": slug}

    @app.post("/api/skills/from-md")
    async def skills_from_md(request: Request, uid: str = ""):
        """上传/粘贴一个 SKILL.md 全文：自动解析 frontmatter 入库。
        可直接复用 Claude Code / OpenClaw 的 SKILL.md（无 frontmatter 也兼容，按文件名建条目）。"""
        from nicegui import run
        from lebotclaw.core.skillstore import parse_frontmatter
        payload = await request.json()
        md_text = (payload.get("md") or "").strip()
        if not md_text:
            return JSONResponse({"error": "md required"}, status_code=400)
        fm, body = parse_frontmatter(md_text)
        filename = (payload.get("filename") or "").strip()
        title = str(fm.get("title") or fm.get("name") or filename).strip()
        if not title:
            return JSONResponse(
                {"error": "frontmatter 缺 title/name，且无文件名可兜底"}, status_code=400
            )
        cat = fm.get("category")
        skill = {
            "title": title,
            "body": body or md_text,
            "trigger": fm.get("trigger") or fm.get("description") or "",
            "category": cat if cat in VALID_CATEGORIES else "task_flow",
            # source 用 internal：导入后即本地可编辑副本（ext- 挂载的外部目录才是只读）。
            # 来源信息记到 parent_note，卡片上能看到"从哪来的"。
            "parent_note": fm.get("parent_note")
            or (f"📥 从文件导入（{filename}）" if filename else "📥 从文件导入"),
        }
        slug = await run.io_bound(skill_store(uid).add, skill)
        return {"ok": True, "slug": slug, "parsed": bool(fm), "title": title}

    @app.post("/api/skills/{slug}/update")
    async def skills_update(slug: str, request: Request, uid: str = ""):
        """编辑（标题/正文/trigger/说明等字段，只允许白名单字段）。"""
        from nicegui import run
        payload = await request.json()
        allowed = {"title", "body", "category", "trigger", "parent_note", "status"}
        fields = {k: v for k, v in payload.items() if k in allowed}
        if not fields:
            return JSONResponse({"error": "nothing to update"}, status_code=400)
        ok = await run.io_bound(skill_store(uid).update_fields, slug, **fields)
        return {"ok": ok}

    @app.post("/api/skills/{slug}/optimize")
    async def skills_optimize(slug: str, request: Request, uid: str = ""):
        """✨ 小博化：用 LLM 把 skill 改写成"小博能用、小朋友能懂、步骤能落地"的版本。
        不改变原意（学习/玩/生活一视同仁，不硬往教学上靠）。原版存 versions/ 可回滚。"""
        from nicegui import run
        import time as _t
        from pathlib import Path as _P
        store = skill_store(uid)
        entry = await run.io_bound(store.get, slug)
        if not entry:
            return JSONResponse({"error": "skill not found"}, status_code=404)
        if str(slug).startswith("ext-"):
            return JSONResponse({"error": "外部只读 skill，请先复制为本地副本"}, status_code=400)
        adapter = runtime.model_adapters.get(runtime.default_model)
        if not adapter:
            return JSONResponse({"error": "还没配置模型，小博没法干活"}, status_code=503)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        grade_hint = (payload.get("grade") or "").strip()
        orig_body = entry.get("body", "")

        def _do():
            sys_msg = (
                "你是超级小博，15岁男孩。把下面这份'本领说明'改写成你自己真的能用、"
                "小朋友也听得懂、步骤能直接照着做的版本。要求：用你爽朗口语的口吻（别说明书腔）；"
                "对小朋友友好（用词简单、多打比方）；步骤具体可执行；"
                "保持这个本领原本要做的事不变——不管它是学习、玩游戏还是生活小事，一视同仁，"
                "别硬往'教学'上靠；直接输出改写后的 Markdown 正文，不要多余解释。"
            )
            user_msg = (
                f"本领名称：{entry.get('title', '')}\n"
                + (f"（对方是{grade_hint}的同学）\n" if grade_hint else "")
                + f"\n原始内容：\n{orig_body}\n\n请改写。"
            )
            resp = adapter.generate(
                messages=[{"role": "system", "content": sys_msg},
                          {"role": "user", "content": user_msg}],
                temperature=0.7, max_tokens=2048,
            )
            return (resp.content or "").strip()

        try:
            new_body = await run.io_bound(_do)
        except Exception as e:  # 模型挂了（配额/网络）不能裸 500，给可读错误
            return JSONResponse({"error": f"模型调用失败：{e}"}, status_code=503)
        if not new_body:
            return JSONResponse({"error": "小博这次没想出来，稍后再试"}, status_code=500)
        ver_dir = _P(ud(uid)) / "skills" / slug / "versions"
        ver_dir.mkdir(parents=True, exist_ok=True)
        (ver_dir / f"v{int(_t.time())}.md").write_text(orig_body, encoding="utf-8")
        await run.io_bound(store.update_fields, slug, body=new_body)
        return {"ok": True, "before": len(orig_body), "after": len(new_body)}

    @app.post("/api/skills/{slug}/clone")
    async def skills_clone(slug: str, uid: str = ""):
        """把外部只读 skill（ext- 挂载的 Claude Code/OpenClaw skill）复制为本地可编辑副本。"""
        from nicegui import run
        store = skill_store(uid)
        entry = await run.io_bound(store.get, slug)
        if not entry:
            return JSONResponse({"error": "skill not found"}, status_code=404)
        if not str(slug).startswith("ext-"):
            return JSONResponse({"error": "这不是外部 skill，无需复制"}, status_code=400)
        cat = entry.get("category")
        skill = {
            "title": entry.get("title") or entry.get("name") or slug,
            "body": entry.get("body", ""),
            "trigger": entry.get("trigger") or entry.get("description") or "",
            "category": cat if cat in VALID_CATEGORIES else "task_flow",
            "parent_note": "📥 从 Claude Code/OpenClaw 复制为可编辑副本",
        }
        new_slug = await run.io_bound(store.add, skill)
        return {"ok": True, "slug": new_slug, "title": skill["title"]}

    @app.get("/api/skills/sources")
    async def skills_sources(uid: str = ""):
        """列出已挂载的外部 skill 目录（Claude Code/OpenClaw）+ 各目录扫到的 skill 数。"""
        from nicegui import run
        from collections import Counter
        from lebotclaw.core.skillstore import load_external_dirs
        from pathlib import Path as _P
        dirs = load_external_dirs()
        store = skill_store(uid)
        items = await run.io_bound(store.list)
        cnt = Counter()
        for it in items:
            ep = it.get("ext_path")
            if ep:
                cnt[ep] += 1
        result = []
        for d in dirs:
            dp = str(_P(d).expanduser())
            result.append({"dir": d, "exists": _P(dp).is_dir(), "count": cnt.get(dp, 0)})
        total_ext = sum(1 for it in items if str(it.get("slug", "")).startswith("ext-"))
        return {"sources": result, "external_total": total_ext}

    @app.post("/api/skills/sources")
    async def skills_sources_add(request: Request):
        """添加一个外部 skill 目录（写入 ~/.lebotclaw/config.json 的 skills.external_dirs）。
        下次请求即生效（skill_store 每次都重新读 config）。"""
        import json as _json
        from pathlib import Path as _P
        payload = await request.json()
        new_dir = (payload.get("dir") or "").strip()
        if not new_dir:
            return JSONResponse({"error": "dir required"}, status_code=400)
        cfg_path = _P("~/.lebotclaw/config.json").expanduser()
        cfg = {}
        if cfg_path.exists():
            try:
                cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
            except _json.JSONDecodeError:
                cfg = {}
        dirs = cfg.setdefault("skills", {}).setdefault("external_dirs", [])
        expanded = str(_P(new_dir).expanduser())
        if expanded not in dirs:
            dirs.append(expanded)
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(_json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "dirs": cfg["skills"]["external_dirs"]}

    @app.post("/api/skills/{slug}/toggle")
    async def skills_toggle(slug: str, uid: str = ""):
        """停用/启用（active ↔ deprecated）。"""
        from nicegui import run
        store = skill_store(uid)
        entry = await run.io_bound(store.get, slug)
        if not entry:
            return JSONResponse({"error": "skill not found"}, status_code=404)
        new = "deprecated" if entry.get("status", "active") == "active" else "active"
        await run.io_bound(store.update_fields, slug, status=new)
        return {"ok": True, "status": new}

    @app.delete("/api/skills/{slug}")
    async def skills_delete(slug: str, uid: str = ""):
        from nicegui import run
        ok = await run.io_bound(skill_store(uid).delete, slug)
        return {"ok": ok}

    @app.post("/api/skills/{slug}/undo")
    async def skills_undo(slug: str, uid: str = ""):
        """撤销自动沉淀：删除 + 写入 skill_undos.json 黑名单（同场景 30 天不再自动沉淀）。"""
        import time as _t
        from nicegui import run
        store = skill_store(uid)
        entry = await run.io_bound(store.get, slug)
        if not entry:
            return JSONResponse({"error": "skill not found"}, status_code=404)
        trigger = entry.get("trigger") or entry.get("title", "")
        await run.io_bound(store.delete, slug)
        import json as _json
        from pathlib import Path as _P
        undo_path = _P(ud(uid)) / "skill_undos.json"
        undos = {}
        if undo_path.exists():
            try:
                undos = _json.loads(undo_path.read_text(encoding="utf-8"))
            except _json.JSONDecodeError:
                undos = {}
        undos[trigger] = _t.time()
        # 顺带清理 30 天前的黑名单条目
        undos = {k: v for k, v in undos.items() if _t.time() - v < 30 * 86400}
        undo_path.write_text(_json.dumps(undos, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "blacklisted": trigger}

    @app.get("/api/companion")
    async def companion(uid: str = ""):
        """陪伴档案：「陪伴小主人第 N 天 · 一起聊过 X token」（spec FR-E7）。"""
        from nicegui import run
        from lebotclaw.core.workspace import WorkspaceFiles
        # uid="" 表示文件直接落在该用户目录（SOUL.md/MEMORY.md/companion.json）
        ws = WorkspaceFiles(base_dir=ud(uid), uid="")
        return await run.io_bound(ws.companion_stats)

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
