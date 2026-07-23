import json
import time
from typing import Optional

from lebotclaw.core.memory import MemoryStore
from lebotclaw.tools.registry import ToolRegistry
from lebotclaw.adapters.base import ModelAdapter, ModelResponse
from lebotclaw.core.planner import Planner
from lebotclaw.tools.base import Tool


def _fmt_tool_input(raw_arguments) -> str:
    """工具入参 → 前端卡片展示文本。{'expression': '3.14*2.5'} → '3.14*2.5'。"""
    if isinstance(raw_arguments, str):
        try:
            raw_arguments = json.loads(raw_arguments)
        except (json.JSONDecodeError, TypeError):
            return raw_arguments[:120]
    if isinstance(raw_arguments, dict):
        return "，".join(str(v)[:60] for v in raw_arguments.values())[:120]
    return str(raw_arguments)[:120]


def _split(text: str, max_len: int = 24):
    """按句切分文本（模拟流式用）。"""
    import re
    for piece in re.split(r"(?<=[。！？!?\n；;])", text or ""):
        if piece:
            yield piece


# 固定日期节日（农历节日如春节/中秋不硬算，交给模型自由发挥）
_FESTIVALS = {
    (1, 1): "元旦", (3, 8): "妇女节", (3, 12): "植树节", (4, 1): "愚人节",
    (5, 1): "劳动节", (5, 4): "青年节", (6, 1): "儿童节", (7, 1): "建党节",
    (8, 1): "建军节", (9, 10): "教师节", (10, 1): "国庆节", (12, 25): "圣诞节",
}


def _time_context(memory) -> str:
    """当前时间情境注入：让超级小博的问候和话题有'生活感'。"""
    from datetime import datetime
    now = datetime.now()
    hour = now.hour
    if 5 <= hour < 9:
        slot = "一大早"
    elif 9 <= hour < 12:
        slot = "上午"
    elif 12 <= hour < 14:
        slot = "中午"
    elif 14 <= hour < 18:
        slot = "下午"
    elif 18 <= hour < 22:
        slot = "晚上"
    else:
        slot = "深夜（如果是深夜，可以关心一句'这么晚还在学习呀，别太累'）"

    lines = [f"现在是{now.strftime('%Y年%m月%d日')} {slot}，星期{'一二三四五六日'[now.weekday()]}。"]

    festival = _FESTIVALS.get((now.month, now.day))
    if festival:
        lines.append(f"今天是{festival}，可以自然地带一句节日氛围（比如儿童节给个小惊喜、教师节聊聊喜欢的老师），别生硬。")
    if now.month in (1, 6) and 10 <= now.day <= 30:
        lines.append("最近是考试季，如果他提到考试，多给点打气和实用的复习建议。")
    if (8, 25) <= (now.month, now.day) <= (9, 5):
        lines.append("正值开学季，可以聊聊新学期的打算。")

    try:
        birthday = memory.get_student_profile().get("生日", "")
        if birthday:
            import re as _re
            m = _re.search(r"(\d{1,2})\s*[月/-]\s*(\d{1,2})", birthday)
            if m and (now.month, now.day) == (int(m.group(1)), int(m.group(2))):
                lines.append(f"🎂 今天是他的生日！一定要主动送上生日祝福，让他感觉到你记得。")
    except Exception:  # noqa: BLE001
        pass

    return "【当下时间】" + "".join(lines)


class Agent:

    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: ToolRegistry = None,
        model_adapter: ModelAdapter = None,
        memory: MemoryStore = None,
        planner: Planner = None,
        wiki=None,
        user_dir: str = None,
        workspace=None,
        skill_store=None,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools or ToolRegistry()
        self.model_adapter = model_adapter
        self.memory = memory or MemoryStore()
        self.planner = planner or Planner()
        self.wiki = wiki
        self.user_dir = user_dir or "~/.lebotclaw"
        # spec v2.1：SOUL/MEMORY 文件（core.workspace.WorkspaceFiles）+ skill 复用（core.skillstore.SkillStore）
        # 均为可选——不传时行为与旧版完全一致（89 测试回归保障）
        self.workspace = workspace
        self.skill_store = skill_store
        self._last_skill_used: Optional[dict] = None  # 本轮注入的 skill（stream_events 外露用）
        self._history: list[dict] = []
        self._frozen_context_id: Optional[str] = None
        # config 开关：flow.enabled（Flow 可见总闸，默认开；关掉后 stream_events
        # 即使收到 flow_engine 也走普通问答路径，行为与旧版完全一致）
        self.flow_enabled = True

    def chat(self, user_input: str) -> str:
        if not user_input.strip():
            return "请输入你的问题，我来帮你学习！"

        enriched_prompt = self._build_system_prompt_with_memory(user_input)

        messages = [{"role": "system", "content": enriched_prompt}]
        messages.extend(self._history)
        messages.append({"role": "user", "content": user_input})

        tool_schemas = self.tools.list_tools() if self.tools._tools else None

        if self.model_adapter is None:
            response_text = self._offline_respond(user_input, messages)
            self._history.append({"role": "user", "content": user_input})
            self._history.append({"role": "assistant", "content": response_text})
            self.memory.summarize_session(self._history)
            return response_text

        response = self.model_adapter.generate(
            messages=messages,
            tools=tool_schemas,
            temperature=0.7,
            max_tokens=2048,
        )

        if response.tool_calls:
            self._history.append({"role": "user", "content": user_input})
            # assistant 消息须带 tool_calls 结构（OpenAI/DeepSeek API 要求）
            asst_msg = {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc.get("tool_name", ""),
                            "arguments": tc.get("arguments", "")
                            if isinstance(tc.get("arguments"), str)
                            else json.dumps(tc.get("arguments", {}), ensure_ascii=False),
                        },
                    }
                    for tc in response.tool_calls
                ],
            }
            self._history.append(asst_msg)
            messages.append(asst_msg)

            tool_results = self._handle_tool_calls(response.tool_calls)
            for tr in tool_results:
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tr.get("tool_call_id", ""),
                    "content": tr.get("output", ""),
                }
                messages.append(tool_msg)
                self._history.append(tool_msg)

            second_response = self.model_adapter.generate(
                messages=messages,
                tools=tool_schemas,
                temperature=0.7,
                max_tokens=2048,
            )
            final_text = second_response.content or ""
            self._history.append({"role": "assistant", "content": final_text})
        else:
            final_text = response.content or ""
            self._history.append({"role": "user", "content": user_input})
            self._history.append({"role": "assistant", "content": final_text})

        self.memory.summarize_session(self._history)
        return final_text

    def chat_stream(self, user_input: str):
        if not user_input.strip():
            yield "请输入你的问题，我来帮你学习！"
            return

        enriched_prompt = self._build_system_prompt_with_memory(user_input)

        messages = [{"role": "system", "content": enriched_prompt}]
        messages.extend(self._history)
        messages.append({"role": "user", "content": user_input})

        tool_schemas = self.tools.list_tools() if self.tools._tools else None

        if self.model_adapter is None:
            response_text = self._offline_respond(user_input, messages)
            self._history.append({"role": "user", "content": user_input})
            self._history.append({"role": "assistant", "content": response_text})
            self.memory.summarize_session(self._history)
            yield response_text
            return

        collected_chunks = []
        for chunk in self.model_adapter.stream(
            messages=messages,
            tools=tool_schemas,
            temperature=0.7,
            max_tokens=2048,
        ):
            collected_chunks.append(chunk)
            yield chunk

        full_response = "".join(collected_chunks)
        self._history.append({"role": "user", "content": user_input})
        self._history.append({"role": "assistant", "content": full_response})
        self.memory.summarize_session(self._history)

    def chat_stream_with_tools(self, user_input: str):
        """流式且不丢工具：先非流式探测 tool_calls，有则执行后第二轮真流式；
        无工具时把已生成的完整回复按句切分，模拟流式节奏。

        相比 chat_stream（直接 stream、丢弃 tool_calls），本方法保证工具调用被执行，
        数学/知识库等场景不会算错。
        """
        if not user_input.strip():
            yield "请输入你的问题，我来帮你学习！"
            return

        enriched_prompt = self._build_system_prompt_with_memory(user_input)
        messages = [{"role": "system", "content": enriched_prompt}]
        messages.extend(self._history)
        messages.append({"role": "user", "content": user_input})
        tool_schemas = self.tools.list_tools() if self.tools._tools else None

        if self.model_adapter is None:
            response_text = self._offline_respond(user_input, messages)
            self._history.append({"role": "user", "content": user_input})
            self._history.append({"role": "assistant", "content": response_text})
            self.memory.summarize_session(self._history)
            for piece in _split(response_text):
                yield piece
            return

        # 第一轮：非流式探测工具调用
        first = self.model_adapter.generate(
            messages=messages, tools=tool_schemas, temperature=0.7, max_tokens=2048)

        if first.tool_calls:
            self._history.append({"role": "user", "content": user_input})
            asst_msg = {
                "role": "assistant",
                "content": first.content or "",
                "tool_calls": [
                    {
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc.get("tool_name", ""),
                            "arguments": tc.get("arguments", "")
                            if isinstance(tc.get("arguments"), str)
                            else json.dumps(tc.get("arguments", {}), ensure_ascii=False),
                        },
                    }
                    for tc in first.tool_calls
                ],
            }
            self._history.append(asst_msg)
            messages.append(asst_msg)
            for tr in self._handle_tool_calls(first.tool_calls):
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tr.get("tool_call_id", ""),
                    "content": tr.get("output", ""),
                }
                messages.append(tool_msg)
                self._history.append(tool_msg)
            # 第二轮：真流式
            collected = []
            for chunk in self.model_adapter.stream(
                    messages=messages, tools=tool_schemas, temperature=0.7, max_tokens=2048):
                collected.append(chunk)
                yield chunk
            self._history.append({"role": "assistant", "content": "".join(collected)})
        else:
            # 无工具：first 已是完整回复，按句切分模拟流式
            full = first.content or ""
            self._history.append({"role": "user", "content": user_input})
            self._history.append({"role": "assistant", "content": full})
            for piece in _split(full):
                yield piece

        self.memory.summarize_session(self._history)

    def stream_events(self, user_input: str, extra_system: str = "", flow_engine=None):
        """事件流版对话：yield dict 事件，供 Web SSE 把"智能体动作"亮到前端。

        多轮工具循环（≤3 轮，可连环调用）→ 真流式输出最终回答；纯新增不改旧行为。
        事件类型：
          {"type": "wiki", "pages": [title, ...]}              知识库命中
          {"type": "tool", "name": ..., "input": ..., "output": ..., "success": bool}
          {"type": "delta", "text": ...}                       文本片段
        extra_system：额外拼到 system prompt 末尾的指令（内容守护用，如化解粗口）。

        flow_engine（可选，core.flow.FlowEngine）：传入且命中规划触发判定时走
        Flow 可见模式，新增事件 plan/step/tool_round/replanned/flow_done（FR-F1~F5）；
        不传或 self.flow_enabled=False 或未命中判定时，行为与现状完全一致。
        """
        if not user_input.strip():
            yield {"type": "delta", "text": "请输入你的问题，我来帮你学习！"}
            return

        # wiki 命中先外露为事件（_build_system_prompt_with_memory 里会再搜一次做注入，代价可忽略）
        if self.wiki:
            try:
                hits = self.wiki.search_relevant(user_input, limit=3)
                if hits:
                    yield {"type": "wiki", "pages": [p.title for p in hits]}
            except Exception:  # noqa: BLE001
                pass

        enriched_prompt = self._build_system_prompt_with_memory(user_input)
        # skill 复用外露（spec FR-S5）：前端标注「复用了本领：xxx」
        if self._last_skill_used:
            yield {"type": "skill_used", **self._last_skill_used}
        if extra_system:
            enriched_prompt = enriched_prompt + "\n\n" + extra_system
        messages = [{"role": "system", "content": enriched_prompt}]
        messages.extend(self._history)
        messages.append({"role": "user", "content": user_input})
        tool_schemas = self.tools.list_tools() if self.tools._tools else None

        if self.model_adapter is None:
            response_text = self._offline_respond(user_input, messages)
            self._history.append({"role": "user", "content": user_input})
            self._history.append({"role": "assistant", "content": response_text})
            self.memory.summarize_session(self._history)
            for piece in _split(response_text):
                yield {"type": "delta", "text": piece}
            return

        # ---- Flow 可见分支（spec P1 1.2-1.6）：命中规划触发或 replan 反馈时进入 ----
        run = None
        continuation = False
        if flow_engine is not None and self.flow_enabled:
            try:
                if (flow_engine.active_run is not None
                        and flow_engine.is_replan_feedback(user_input)):
                    run, continuation = flow_engine.active_run, True
                elif flow_engine.should_trigger(user_input):
                    run = flow_engine.create_run(user_input)
            except Exception:  # noqa: BLE001 — flow 判定失败不拖垮正常对话
                run = None
        if run is not None:
            for ev in self._stream_flow(
                    run, flow_engine, user_input, enriched_prompt, tool_schemas, continuation):
                yield ev
            usage_ev = self._record_skill_usage()
            if usage_ev:
                yield usage_ev
            self.memory.summarize_session(self._history)
            return

        # 真流式工具循环（上限 3 轮）：每轮边吐 token 边探测工具，普通对话一次到位
        user_msg = {"role": "user", "content": user_input}
        self._history.append(user_msg)
        for ev in self._stream_tool_loop(messages, tool_schemas):
            yield ev

        usage_ev = self._record_skill_usage()
        if usage_ev:
            yield usage_ev
        self.memory.summarize_session(self._history)

    def _record_skill_usage(self):
        """复用档案（spec FR-U1）+ 演化触发（spec 2.6）。

        本轮注入了 skill 则记录使用 + 解析 [变体] 标注；
        随后交给 EvolveEngine 判定是否打磨/回滚/合并，
        有动作则返回 skill_evolved 事件（调用方负责 yield）。
        """
        if not self._last_skill_used or self.skill_store is None:
            return None
        slug = self._last_skill_used["slug"]
        try:
            reply = ""
            for msg in reversed(self._history):
                if msg.get("role") == "assistant" and msg.get("content"):
                    reply = msg["content"]
                    break
            variant = ""
            for line in reply.splitlines():
                if line.strip().startswith("[变体]"):
                    variant = line.strip()[4:].strip()[:100]
                    break
            self.skill_store.record_usage(
                slug,
                outcome={"via": "chat"},
                variant=variant,
                effectiveness=0.75,
            )
            from lebotclaw.core.evolve import EvolveEngine
            return EvolveEngine(self.skill_store, adapter=self.model_adapter).on_usage(slug)
        except Exception:  # noqa: BLE001
            return None
        finally:
            self._last_skill_used = None

    def _stream_tool_loop(self, messages: list, tool_schemas, node_id=None):
        """≤3 轮真流式工具循环（自 stream_events 原样抽取，行为不变）。

        node_id 非空（Flow 模式）时，tool 事件增强为 tool_round：
        带轮次 round、所属 node_id 与原因 reason（FR-F3）。
        """
        tool_rounds = 0
        got_final = False
        last_failed_tools: set = set()  # 重试风暴防护：记录上一轮全败的工具组合
        while tool_rounds < 3:
            collected = []
            pending_tools = []
            for kind, data in self.model_adapter.stream_deltas(
                    messages=messages, tools=tool_schemas, temperature=0.7, max_tokens=4096):
                if kind == "text":
                    collected.append(data)
                    yield {"type": "delta", "text": data}
                elif kind == "tool_calls":
                    pending_tools = data
            if not pending_tools:
                # 模型给出最终文本（已真流式输出），收工
                self._history.append({"role": "assistant", "content": "".join(collected)})
                got_final = True
                break
            # 模型要求工具：记录 assistant 的 tool_calls 并执行
            tool_rounds += 1
            asst_msg = {
                "role": "assistant",
                "content": "".join(collected) or "",
                "tool_calls": [
                    {
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc.get("tool_name", ""),
                            "arguments": tc.get("arguments", "")
                            if isinstance(tc.get("arguments"), str)
                            else json.dumps(tc.get("arguments", {}), ensure_ascii=False),
                        },
                    }
                    for tc in pending_tools
                ],
            }
            self._history.append(asst_msg)
            messages.append(asst_msg)
            args_by_id = {tc.get("id", ""): tc.get("arguments", "") for tc in pending_tools}
            results = self._handle_tool_calls(pending_tools)
            # 重试风暴防护（2026-07-23）：整轮全败且失败工具组合与上一轮相同，
            # 说明模型在死磕一个不可用的工具——下一轮摘掉工具清单强制直接回答。
            names = {tr.get("tool_name", "") for tr in results}
            all_failed = results and all(not tr.get("success") for tr in results)
            storm = all_failed and names == last_failed_tools
            last_failed_tools = names if all_failed else set()
            for tr in results:
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tr.get("tool_call_id", ""),
                    "content": tr.get("output", ""),
                }
                messages.append(tool_msg)
                self._history.append(tool_msg)
                if node_id is not None:
                    yield {
                        "type": "tool_round",
                        "node_id": node_id,
                        "round": tool_rounds,
                        "tool": tr.get("tool_name", ""),
                        "input": _fmt_tool_input(args_by_id.get(tr.get("tool_call_id", ""), "")),
                        "output": (tr.get("output", "") or "")[:200],
                        "reason": "需要调用%s完成当前步骤" % tr.get("tool_name", ""),
                        "success": bool(tr.get("success", True)),
                    }
                else:
                    yield {
                        "type": "tool",
                        "name": tr.get("tool_name", ""),
                        "input": _fmt_tool_input(args_by_id.get(tr.get("tool_call_id", ""), "")),
                        "output": (tr.get("output", "") or "")[:200],
                        "success": bool(tr.get("success", True)),
                    }
            if storm:
                messages.append({
                    "role": "user",
                    "content": "（系统提示：工具连续失败、暂时不可用。请不要再调用任何工具，"
                               "直接用你自己的知识完整回答。）",
                })
                tool_schemas = None  # 下一轮强制纯文本回答

        # 达到工具轮上限仍未收敛：不带工具兜底，真流式补一次最终回答
        if not got_final:
            collected = []
            for kind, data in self.model_adapter.stream_deltas(
                    messages=messages, tools=None, temperature=0.7, max_tokens=4096):
                if kind == "text":
                    collected.append(data)
                    yield {"type": "delta", "text": data}
            self._history.append({"role": "assistant", "content": "".join(collected)})

    def _stream_flow(self, run, flow_engine, user_input: str,
                     base_prompt: str, tool_schemas, continuation: bool):
        """Flow 模式：plan → 逐节点执行（step/tool_round/delta）→ flow_done → 归档。

        continuation=True 表示本轮是 replan 嫁接（学生反馈触发）：先出 replanned
        事件，再执行新插入/未完成的节点。
        """
        from lebotclaw.core.flow import NodeStatus

        if continuation:
            actions = flow_engine.apply_replan(run, user_input)
            self._history.append({"role": "user", "content": user_input})
            for a in actions:
                yield {
                    "type": "replanned",
                    "trigger": user_input,
                    "action": a["action"],
                    "node": a["node"].to_dict(),
                }
        else:
            yield {
                "type": "plan",
                "flow_id": run.id,
                "goal": run.goal,
                "subject": run.subject,
                "grade": run.grade,
                "nodes": [
                    {
                        "node_id": n.id,
                        "title": n.title,
                        "description": n.description,
                        "status": n.status.value,
                        "tools_needed": list(n.tools_needed),
                    }
                    for n in run.nodes
                ],
                "knowledge_points": list(run.knowledge_points),
            }
            self._history.append({"role": "user", "content": user_input})

        total = len(run.nodes)
        for idx, node in enumerate(run.nodes):
            if node.status in (NodeStatus.DONE, NodeStatus.SKIPPED, NodeStatus.FAILED):
                continue
            flow_engine.start_node(run, idx)
            yield {
                "type": "step", "node_id": node.id, "status": "running",
                "note": node.title, "index": idx, "total": total,
            }
            directive = (
                "\n\n当前在执行第 %d 步：%s。纪律：\n"
                "1. 只输出这一步要讲的内容，绝对不要重复之前步骤已经说过的内容；\n"
                "2. 不要重新罗列完整计划，不要预告后面的步骤；\n"
                "3. 这一步讲完就停，控制在 150 字以内。" % (idx + 1, node.title))
            if continuation:
                directive += "\n学生刚才的反馈是：「%s」，请结合反馈调整讲解。" % user_input
            # 紧凑上下文（2026-07-23 修复「自言自语重复方案」）：不再带完整
            # _history（模型看到自己的长篇输出会忍不住复述整份计划），
            # 只给原始诉求 + 已完成步骤的一句话要点。_history 仍照常累积供记忆。
            done_notes = "；".join(
                "%d.%s（%s）" % (i + 1, n.title, (n.note or "")[:60])
                for i, n in enumerate(run.nodes[:idx])
                if n.status == NodeStatus.DONE and getattr(n, "note", ""))
            node_user = user_input
            if done_notes:
                node_user += "\n\n（已完成的步骤：%s。接着做当前这一步，别重复它们。）" % done_notes
            messages = [{"role": "system", "content": base_prompt + directive},
                        {"role": "user", "content": node_user}]
            text_parts = []
            for ev in self._stream_tool_loop(messages, tool_schemas, node_id=node.id):
                if ev.get("type") == "delta":
                    text_parts.append(ev.get("text", ""))
                yield ev
            note = "".join(text_parts).strip()[:120]
            flow_engine.complete_node(run, idx, note=note)
            yield {
                "type": "step", "node_id": node.id, "status": "done",
                "note": note, "index": idx, "total": total,
            }

        flow_engine.finalize(run)
        yield {
            "type": "flow_done",
            "flow_id": run.id,
            "summary": run.summary,
            "completion_rate": run.completion_rate,
            "knowledge_points": list(run.knowledge_points),
        }
        # Skill 蒸馏（spec 2.3-2.5）：从严阈值+黑名单+双闸，宁缺毋滥；
        # 蒸出来了就 yield skill_saved（前端卡片带查看/撤销）
        if self.skill_store is not None:
            try:
                from lebotclaw.core.distiller import SkillDistiller
                distiller = SkillDistiller(self.skill_store, adapter=self.model_adapter,
                                           user_dir=self.user_dir)
                saved = distiller.maybe_distill(run, self._history)
                if saved:
                    yield saved
            except Exception:  # noqa: BLE001 — 蒸馏任何异常都不影响对话
                pass
        try:
            flow_engine.archive(run)
        except Exception:  # noqa: BLE001 — 归档失败不拖垮对话
            pass

    def freeze(self) -> str:
        context_data = {
            "agent_name": self.name,
            "history": self._history,
            "frozen_at": time.time(),
        }
        self._frozen_context_id = self.memory.freeze_context(self.name, context_data)
        return self._frozen_context_id

    def restore(self, context_id: str) -> None:
        data = self.memory.restore_context(context_id)
        if not data:
            return
        self._frozen_context_id = context_id
        inner = data.get("data", data)
        self._history = inner.get("history", [])

    def reset(self) -> None:
        self._history = []

    def _build_system_prompt_with_memory(self, user_input: str) -> str:
        # spec FR-E6 prompt 组合顺序：SOUL（底座人格）→ pack 学科人设 → MEMORY → 检索注入
        prompt_parts = []
        if self.workspace is not None:
            try:
                prompt_parts.append(self.workspace.read_soul())
            except Exception:  # noqa: BLE001
                pass
        prompt_parts.append(self.system_prompt)

        if self.workspace is not None:
            try:
                mem_md = self.workspace.read_memory()
            except Exception:  # noqa: BLE001
                mem_md = ""
            if mem_md:
                prompt_parts.append("\n\n🧠 你记住的关于他的事（长期记忆，当成自己的记忆自然使用）：\n" + mem_md)

        prompt_parts.append("\n\n" + _time_context(self.memory))

        # skill 复用注入（spec FR-S5）：命中高分 skill → 正文进 prompt，模型照着做
        self._last_skill_used = None
        if self.skill_store is not None:
            try:
                hits = self.skill_store.find(scenario=user_input)
                if hits and float(hits[0].get("effectiveness") or 0) >= 0.6:
                    top = hits[0]
                    slug = top.get("slug", "")
                    detail = self.skill_store.get(slug) or {}
                    body = (detail.get("body") or "").strip()
                    if body:
                        prompt_parts.append(
                            "\n\n🎯 复用本领「{}」（之前验证有效的套路，照着它的思路来做；"
                            "如果你做了与它不同的调整，在回答最后用一行「[变体] ...」简述）：\n{}".format(
                                top.get("title", slug), body[:1500]))
                        self._last_skill_used = {
                            "slug": slug,
                            "title": top.get("title", slug),
                            "source": top.get("source", "internal"),
                        }
            except Exception:  # noqa: BLE001 — skill 检索失败不影响正常对话
                self._last_skill_used = None


        related = self.memory.search_memory(query=user_input, limit=5)
        if related:
            memory_lines = []
            for entry in related:
                content = entry.content
                if len(content) > 150:
                    content = content[:150] + "..."
                memory_lines.append(f"- [{entry.category}] {content}")
            prompt_parts.append("\n\n相关学习记忆：\n" + "\n".join(memory_lines))

        profile = self.memory.get_student_profile()
        if profile:
            profile_summary = json.dumps(profile, ensure_ascii=False, indent=None)
            prompt_parts.append(f"\n\n学生画像：{profile_summary}")

        if self.wiki:
            try:
                wiki_hits = self.wiki.search_relevant(user_input, limit=3)
            except Exception:  # noqa: BLE001
                wiki_hits = []
            if wiki_hits:
                lines = []
                for p in wiki_hits:
                    snippet = p.content[:200] + ("…" if len(p.content) > 200 else "")
                    lines.append(f"- [{p.title}] {snippet}")
                prompt_parts.append(
                    "\n\n📖 知识库参考（以下内容已为你检索好，回答时请**直接采用**，不要再调用 knowledge 工具重复查询，也不要对学生说找不到）：\n"
                    + "\n".join(lines))

        return "\n".join(prompt_parts)

    def _handle_tool_calls(self, tool_calls: list[dict]) -> list[dict]:
        results = []
        for tc in tool_calls:
            tool_name = tc.get("tool_name", "")
            raw_args = tc.get("arguments", {})

            if isinstance(raw_args, str):
                try:
                    raw_args = json.loads(raw_args)
                except json.JSONDecodeError:
                    raw_args = {}

            try:
                result = self.tools.execute(tool_name, **raw_args)
                results.append({
                    "tool_call_id": tc.get("id", ""),
                    "tool_name": tool_name,
                    "success": result.success,
                    "output": result.output,
                    "error": result.error or "",
                })
            except Exception as e:
                results.append({
                    "tool_call_id": tc.get("id", ""),
                    "tool_name": tool_name,
                    "success": False,
                    "output": "",
                    "error": str(e),
                })
        return results

    def _offline_respond(self, user_input: str, messages: list[dict]) -> str:
        # 无模型时，尝试直接执行工具
        tool_calls = Tool.parse_tool_calls(user_input)
        if tool_calls:
            results = self._handle_tool_calls(tool_calls)
            parts = []
            for r in results:
                if r["success"]:
                    parts.append(r["output"])
                else:
                    parts.append(f"工具 {r['tool_name']} 执行失败: {r['error']}")
            if parts:
                return "\n\n".join(parts)

        # 尝试按常见模式直接调用工具
        import re
        calc_pattern = re.compile(r'[\d.]+\s*[+\-*/×÷^]\s*[\d.]+|sqrt|sin|cos|tan|log')
        if calc_pattern.search(user_input):
            expr = user_input.strip()
            try:
                result = self.tools.execute("calculator", expression=expr)
                if result.success:
                    return result.output
            except (KeyError, Exception):
                pass

        if "查" in user_input or "字典" in user_input or "词典" in user_input:
            word = re.sub(r'查[一下]?[字典词典]*', '', user_input).strip()
            if word:
                try:
                    result = self.tools.execute("dictionary", word=word)
                    if result.success:
                        return result.output
                except (KeyError, Exception):
                    pass

        return (
            f"⚠ 未连接 AI 模型，无法回答你的问题。\n"
            f"请配置 API Key 后重启 LebotClaw（推荐 DeepSeek 或 Qwen）。\n"
            f"设置方法：export DEEPSEEK_API_KEY=\"your-key\""
        )


class AgentRegistry:

    def __init__(self):
        self._agents: dict[str, Agent] = {}
        self._active_agent: Optional[str] = None

    def register(self, agent: Agent) -> None:
        self._agents[agent.name] = agent

    def get(self, name: str) -> Agent:
        if name not in self._agents:
            raise KeyError(
                f"Agent '{name}' not found. Available: {list(self._agents.keys())}"
            )
        return self._agents[name]

    def list_agents(self) -> list[str]:
        return list(self._agents.keys())

    def get_active(self) -> Optional[Agent]:
        if self._active_agent:
            return self._agents.get(self._active_agent)
        return None

    def switch_to(self, name: str) -> Agent:
        if self._active_agent and self._active_agent in self._agents:
            self._agents[self._active_agent].freeze()
        self._active_agent = name
        agent = self.get(name)
        if agent._frozen_context_id:
            agent.restore(agent._frozen_context_id)
        return agent
