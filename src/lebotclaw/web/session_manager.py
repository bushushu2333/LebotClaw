"""每会话独立 registry + lock；Web 与飞书各自会话隔离。

会话 = 一组独立的对话历史 + 学科状态。Web 用 session_id（存 app.storage.tab），
飞书用 channel:chatId 绑定。两个来源天然不共享 SessionContext，避免串话。
"""
import threading
import time
import uuid
from typing import Optional

from lebotclaw.core.agent import Agent, AgentRegistry
from lebotclaw.core.memory import MemoryStore
from lebotclaw.core.router import IntentRouter


class SessionContext:
    """单个会话的运行时上下文。满足 core.commands.CommandContext 协议。"""

    def __init__(self, sid: str, registry: AgentRegistry, memory: MemoryStore,
                 channel: str = "web", chat_hint: str = "", uid: str = "",
                 user_dir: str = None):
        self.sid = sid
        self.uid = uid                  # 用户标识：Web 多档案隔离用（空=全局/CLI）
        self.registry = registry
        self.router = IntentRouter()
        self.memory = memory
        self.channel = channel          # "web" | "feishu"
        self.chat_hint = chat_hint      # 飞书 chat_id 等
        self.user_dir = user_dir        # per-user 目录（spec v2.1：SOUL/MEMORY/skills/flow 落盘根）
        self.lock = threading.RLock()   # 串行化本会话的 chat（Agent.chat 非原子）
        self.created_at = time.time()
        self._active_name = "general"
        self._flow_engine = None
        self._workspace = None
        self._skill_store = None

    @property
    def workspace(self):
        """spec 2.11：SOUL.md / MEMORY.md / companion.json（懒加载，首次访问即确保 SOUL 存在）。"""
        if self._workspace is None:
            from lebotclaw.core.workspace import WorkspaceFiles
            ud = self.user_dir or "~/.lebotclaw"
            self._workspace = WorkspaceFiles(base_dir=ud, uid="")
        return self._workspace

    @property
    def skill_store(self):
        """spec 2.1：per-user SKILL.md 文件包存储（懒加载）。"""
        if self._skill_store is None:
            from lebotclaw.core.skillstore import SkillStore
            ud = self.user_dir or "~/.lebotclaw"
            self._skill_store = SkillStore(store_dir=ud)
        return self._skill_store

    @property
    def flow_engine(self):
        """spec 1.1：每会话一个 FlowEngine（active_run 状态随会话隔离）。"""
        if self._flow_engine is None:
            from lebotclaw.core.flow import FlowEngine
            self._flow_engine = FlowEngine(
                router=self.router, user_dir=self.user_dir or "~/.lebotclaw")
        return self._flow_engine

    @property
    def active_agent(self) -> Agent:
        """当前活跃 Agent。switch_to 后跟随 registry._active_agent 变化。"""
        name = self.registry._active_agent or self._active_name
        try:
            return self.registry.get(name)
        except KeyError:
            return self.registry.get("general")

    def classify_and_route(self, user_input: str):
        """意图路由 → 必要时切换 agent。副作用仅限本会话 registry。"""
        context = {
            "active_agent": self.registry._active_agent or self._active_name,
            "history": self.active_agent._history[-6:],
        }
        decision = self.router.classify(user_input, context=context)
        current = self.registry._active_agent
        if decision.target_agent and decision.target_agent != current:
            try:
                self.registry.switch_to(decision.target_agent)
                self._active_name = decision.target_agent
            except KeyError:
                pass
        return decision

    def active_name(self) -> str:
        return self.registry._active_agent or self._active_name

    def reset(self):
        with self.lock:
            self.active_agent.reset()


class SessionManager:
    def __init__(self, runtime):
        self.rt = runtime
        self._sessions: dict[str, SessionContext] = {}
        self._channel_map: dict[str, str] = {}   # "channel:chat_id" -> sid
        self._lock = threading.Lock()

    def get_or_create(self, sid: Optional[str] = None, channel: str = "web",
                      chat_hint: str = "", uid: str = "") -> SessionContext:
        with self._lock:
            if channel != "web" and chat_hint:
                key = f"{channel}:{chat_hint}"
                existing = self._channel_map.get(key)
                if existing and existing in self._sessions:
                    return self._sessions[existing]
            if not sid or sid not in self._sessions:
                sid = sid or str(uuid.uuid4())
                memory = self.rt.memory_for(uid) if uid else self.rt.memory
                ctx = SessionContext(sid, self.rt.build_registry(uid), memory, channel, chat_hint, uid,
                                     user_dir=self.rt.user_dir_for(uid) if uid else None)
                self._sessions[sid] = ctx
                if channel != "web" and chat_hint:
                    self._channel_map[f"{channel}:{chat_hint}"] = sid
            return self._sessions[sid]

    def by_channel_key(self, channel: str, chat_id: str) -> SessionContext:
        """飞书等 IM 通道：按 channel:chatId 绑定/创建会话。"""
        return self.get_or_create(channel=channel, chat_hint=chat_id)

    def get(self, sid: str) -> Optional[SessionContext]:
        with self._lock:
            return self._sessions.get(sid)

    def list_sessions(self) -> list:
        with self._lock:
            return list(self._sessions.values())
