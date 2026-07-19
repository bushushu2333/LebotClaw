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
                "enabled": False, "app_id": "", "app_secret": "", "bot_name": "小博",
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

    def _load_env(self):
        """与 CLI 一致：从 ~/.lebotclaw/.env 注入 env var（setdefault 不覆盖）。"""
        env_file = _CONFIG_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

    def build_registry(self):
        """每会话工厂：独立 registry（4 个 Agent 独立 _history），共享 memory/adapters。

        直接复用 ``cli.create_default_registry``，零改 core。
        """
        return cli_mod.create_default_registry(
            model_adapters=self.model_adapters,
            default_model=self.default_model,
            memory=self.memory,
            style_extra=self.style_extra,
            wiki=self.wiki,
        )

    def student_name(self) -> str:
        return self.config.get("student_name") or self.memory.get_student_profile().get("名字", "")

    def has_model(self) -> bool:
        return bool(self.model_adapters) and self.default_model is not None
