"""LebotClaw Web 入口（NiceGUI）。``lebotclaw web`` → main()。

NiceGUI 即 FastAPI：同一进程同端口，聊天页 / 记忆 / 计划 / 设置 / jobs 与 /api/* 共存；
飞书 WS / cron 在 on_startup 起 daemon 线程（config 启用后自动生效）。
"""
from lebotclaw.web.runtime import AppRuntime, load_merged_config


def _build_channels(runtime: AppRuntime):
    """按 config 注册启用的通道。默认全 disabled → 空 registry。"""
    from lebotclaw.channels import ChannelRegistry
    reg = ChannelRegistry()

    fcfg = runtime.config.get("channels", {}).get("feishu", {})
    if fcfg.get("enabled") and fcfg.get("app_id"):
        try:
            from lebotclaw.channels.feishu.client import FeishuChannel
            reg.register(FeishuChannel(fcfg, runtime))
        except ImportError:
            print("⚠ 启用了飞书但未安装 lark-oapi，请：pip install 'lebotclaw[feishu]'")

    wcfg = runtime.config.get("channels", {}).get("wechat", {})
    if wcfg.get("enabled"):
        from lebotclaw.channels.bridge import WechatBridgeChannel
        reg.register(WechatBridgeChannel(wcfg))

    return reg


def _build_scheduler(runtime: AppRuntime):
    """按 config 启用 cron 调度器（Step 7 实现 CronScheduler 后生效）。"""
    scfg = runtime.config.get("scheduler", {})
    if not scfg.get("enabled"):
        return None
    try:
        from lebotclaw.scheduler.scheduler import CronScheduler
        return CronScheduler(runtime)
    except ImportError:
        print("⚠ 启用了 cron 但 scheduler 模块未就绪")
        return None


def create_app(runtime: AppRuntime):
    from pathlib import Path

    from fastapi.responses import HTMLResponse
    from nicegui import app

    # OpenClaw 风格 dashboard SPA（单文件，无构建步骤）
    dashboard_file = Path(__file__).parent / "static" / "dashboard.html"
    app.add_static_files("/static", str(dashboard_file.parent))

    # NiceGUI import 时硬编码注册了 '/' 的 auto-index（nicegui.py::_get_index），
    # 且在路由表最前，会抢占我们的 dashboard —— 先把它从路由表摘掉再挂自己的。
    app.router.routes = [
        r for r in app.router.routes
        if not (getattr(r, "path", None) == "/" and getattr(r, "name", "") == "_get_index")
    ]

    @app.get("/")
    def dashboard():
        return HTMLResponse(dashboard_file.read_text(encoding="utf-8"))

    # 旧版 NiceGUI 聊天页兜底（/classic），同时抑制 NiceGUI auto-index 抢占 '/'
    from lebotclaw.web.pages import chat as chat_page
    chat_page.register(runtime)

    # /api/* 路由
    try:
        from lebotclaw.web.routes_api import register_api_routes
        register_api_routes(runtime)
    except Exception:
        pass

    started = {"done": False}

    @app.on_startup
    def _start_background():
        if started["done"]:
            return
        started["done"] = True
        import threading
        # 飞书 WS 长连接（config 启用时）
        if runtime.channels and runtime.channels.has("feishu"):
            threading.Thread(target=runtime.channels.get("feishu").run, daemon=True).start()
        # cron 调度器（config 启用时）
        if runtime.scheduler:
            try:
                runtime.scheduler.start()
            except Exception:
                pass

    @app.on_shutdown
    def _stop_background():
        if runtime.scheduler:
            try:
                runtime.scheduler.shutdown(wait=False)
            except Exception:
                pass

    return app


def main():
    cfg = load_merged_config()
    runtime = AppRuntime(cfg)

    from lebotclaw.web.session_manager import SessionManager
    runtime.sessions = SessionManager(runtime)
    runtime.channels = _build_channels(runtime)
    runtime.scheduler = _build_scheduler(runtime)

    create_app(runtime)

    from nicegui import ui
    web_cfg = cfg.get("web", {})
    ui.run(
        host=web_cfg.get("host", "127.0.0.1"),
        port=int(web_cfg.get("port", 8080)),
        title=web_cfg.get("title", "LebotClaw"),
        reload=False,
        show=False,
        storage_secret=web_cfg.get("storage_secret", "lebotclaw-web-session"),
    )


if __name__ == "__main__":
    main()
