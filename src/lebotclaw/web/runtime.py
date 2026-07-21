"""全局运行时：持有共享 memory / model_adapters，提供每会话 registry 工厂。

多会话隔离的 linchpin：``build_registry()`` 每次调用都产出独立的 4 学科 Agent
（各自独立 ``_history``），但共享同一批 model_adapters（线程安全）与同一个
加锁的 MemoryStore——所以"切学科/历史"按会话隔离，"学生画像/记忆"全局共享。
"""
import os
import threading

from lebotclaw.core import cli as cli_mod
from lebotclaw.core.memory import MemoryStore
from lebotclaw.core.wiki import WikiStore

_CONFIG_DIR = cli_mod._CONFIG_DIR


def _default_config() -> dict:
    """config.json 缺字段的默认结构（web / channels / scheduler）。"""
    return {
        "web": {
            "host": "127.0.0.1",
            "port": 8080,
            "title": "LebotClaw 学习伙伴",
            "api_token": "",
            "cors_origins": [],
            "storage_secret": "lebotclaw-web-session",  # app.storage.tab 需要
        },
        "channels": {
            "feishu": {
                "enabled": False, "app_id": "", "app_secret": "", "bot_name": "超级小博",
                "default_chat_id": "", "stream_card": False,
                "stream_throttle_ms": 800, "stream_min_delta_chars": 20,
            },
            "wechat": {"enabled": False, "bridge_url": "", "bridge_token": ""},
        },
        "scheduler": {
            "enabled": False, "tz": "Asia/Shanghai",
            "default_channel": "feishu", "default_chat_id": "",
            "jobs_file": "~/.lebotclaw/jobs.json", "runs_file": "~/.lebotclaw/runs.jsonl",
        },
    }


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_merged_config() -> dict:
    """读取 ~/.lebotclaw/config.json 并与默认值递归合并。"""
    raw = cli_mod._load_config()
    return _deep_merge(_default_config(), raw)


class AppRuntime:
    """进程级单例运行时。Web / 飞书 / cron 共享同一实例。"""

    def __init__(self, config: dict = None):
        self.config = config or load_merged_config()
        self._load_env()
        self.model_adapters, self.default_model = cli_mod._scan_model_adapters()
        # 恢复上次在设置页选中的火山 Coding 子模型（重启不丢）
        saved_sub = (self.config.get("model") or {}).get("arkcoding_model", "")
        if saved_sub and "arkcoding" in self.model_adapters:
            self.model_adapters["arkcoding"].model = saved_sub
        self.style_extra = cli_mod._get_style_extra(self.config.get("style", "warm"))
        db_path = self.config.get("memory_db", "~/.lebotclaw/memory.db")
        self.memory = MemoryStore(db_path)
        self.wiki = WikiStore(self.config.get("wiki_db", "~/.lebotclaw/wiki.db"))

        # 以下在 app 装配阶段注入（避免循环 import）
        self.sessions = None       # SessionManager
        self.channels = None       # ChannelRegistry
        self.scheduler = None      # CronScheduler
        self._bg_started = False
        self._lock = threading.Lock()
        self._user_memory: dict = {}   # per-user MemoryStore 缓存（uid -> MemoryStore）

    def _load_env(self):
        """与 CLI 一致：从 ~/.lebotclaw/.env 注入 env var（setdefault 不覆盖）。"""
        env_file = _CONFIG_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    def user_dir_for(self, uid: str) -> str:
        """每个用户独立数据目录：~/.lebotclaw/users/<uid>/"""
        return f"~/.lebotclaw/users/{uid}"

    def memory_for(self, uid: str):
        """per-user MemoryStore（缓存复用单连接）。uid 为空则回落全局 memory。"""
        if not uid:
            return self.memory
        with self._lock:
            if uid not in self._user_memory:
                self._user_memory[uid] = MemoryStore(f"{self.user_dir_for(uid)}/memory.db")
            return self._user_memory[uid]

    def build_registry(self, uid=None):
        """每会话工厂：独立 registry（4 个 Agent 独立 _history）。

        uid 提供时 → per-user memory + per-user 错题/生词 store（记忆隔离）；
        uid 为空 → 全局共享（CLI 兼容 / 旧单用户）。wiki 始终全局共享。
        """
        return cli_mod.create_default_registry(
            model_adapters=self.model_adapters,
            default_model=self.default_model,
            memory=self.memory_for(uid) if uid else self.memory,
            style_extra=self.style_extra,
            wiki=self.wiki,
            user_dir=self.user_dir_for(uid) if uid else None,
        )

    def student_name(self, uid=None) -> str:
        mem = self.memory_for(uid) if uid else self.memory
        return mem.get_student_profile().get("名字", "") or (self.config.get("student_name", "") if not uid else "")

    def has_model(self) -> bool:
        return bool(self.model_adapters) and self.default_model is not None

    def model_label(self) -> str:
        """展示用模型名：arkcoding 展开为具体子模型（如 deepseek-v4-pro）。"""
        if self.default_model == "arkcoding" and "arkcoding" in self.model_adapters:
            return self.model_adapters["arkcoding"].model
        return self.default_model or ""

    def switch_model(self, adapter_name: str, sub_model: str = ""):
        """切换默认模型并热绑定到所有存活会话的 Agent，持久化到 config.json。"""
        adapter = self.model_adapters.get(adapter_name)
        if adapter is None:
            raise KeyError(f"unknown adapter {adapter_name}")
        if adapter_name == "arkcoding" and sub_model:
            adapter.model = sub_model  # 共享适配器实例，改字段即全局生效
        self.default_model = adapter_name
        # 热绑定：已建会话的 Agent 也换到新适配器
        if self.sessions is not None:
            for ctx in self.sessions.list_sessions():
                for agent in ctx.registry._agents.values():
                    agent.model_adapter = adapter
        # 持久化
        cfg = cli_mod._load_config()
        cfg.setdefault("model", {})["default"] = adapter_name
        if adapter_name == "arkcoding" and sub_model:
            cfg["model"]["arkcoding_model"] = sub_model
        cli_mod._save_config(cfg)
        self.config = load_merged_config()
