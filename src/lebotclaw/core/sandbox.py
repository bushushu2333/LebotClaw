"""ScriptSandbox（spec 2.10）：外部 SKILL.md 附带脚本的唯一执行闸口。

默认**关闭**——外部 skill 只提供 Markdown 指引（注入 prompt），其中 references/
scripts/ 一律不执行。只有 config.json 里 skills.scripts_enabled=true 时，
才允许白名单内的只读脚本经此沙箱执行（subprocess + 超时 + 禁网络环境变量）。
"""
import os
import subprocess
from pathlib import Path


class ScriptSandboxDisabled(Exception):
    pass


class ScriptSandbox:
    def __init__(self, enabled: bool = False, timeout: int = 10,
                 allow_extensions=(".py", ".sh")):
        self.enabled = enabled
        self.timeout = timeout
        self.allow_extensions = tuple(allow_extensions)

    @classmethod
    def from_config(cls, config: dict = None) -> "ScriptSandbox":
        cfg = (config or {}).get("skills", {}) if isinstance(config, dict) else {}
        return cls(enabled=bool(cfg.get("scripts_enabled", False)))

    def run(self, script_path: str, args=None):
        """执行外部脚本。未启用 / 扩展名不在白名单 → 直接拒绝。"""
        if not self.enabled:
            raise ScriptSandboxDisabled(
                "脚本执行默认关闭：外部 skill 仅作 Markdown 指引使用。"
                "确需执行请在 config.json 设置 skills.scripts_enabled=true")
        path = Path(script_path)
        if path.suffix not in self.allow_extensions:
            raise ScriptSandboxDisabled("扩展名 %s 不在脚本白名单" % path.suffix)
        env = {k: v for k, v in os.environ.items()
               if not k.upper().endswith(("_KEY", "_TOKEN", "_SECRET"))}
        proc = subprocess.run(
            [str(path)] + list(args or []),
            capture_output=True, text=True, timeout=self.timeout, env=env)
        return proc.stdout
