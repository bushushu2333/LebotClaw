"""飞书原生通道：lark-oapi WebSocket 长连接收消息 + IM API 文本回复。

无需公网回调 URL，仅需 app_id/app_secret（在 config.channels.feishu 配置）。
WS client 在后台 daemon 线程 ``run()`` 中阻塞启动。

SDK 调用全部收敛在此文件，便于固定 lark-oapi 版本（>=1.4.0,<1.5）。
收到的消息经 Dispatcher → 统一会话 → blocking_chat → reply 回飞书。
"""
import json
import logging

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

from lebotclaw.channels.base import ChatChannel, IncomingMessage, PushTarget

logger = logging.getLogger("lebotclaw.feishu")


class FeishuChannel(ChatChannel):
    name = "feishu"

    def __init__(self, cfg: dict, runtime):
        self.cfg = cfg or {}
        self.rt = runtime
        self.app_id = self.cfg.get("app_id", "")
        self.app_secret = self.cfg.get("app_secret", "")
        self.default_chat_id = self.cfg.get("default_chat_id", "")

        self._client = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .build()
        )
        self._ws = None
        from lebotclaw.channels.dispatcher import Dispatcher
        self._dispatcher = Dispatcher(runtime)

    # ── 入站：WS 事件 → IncomingMessage → Dispatcher ──

    def _to_incoming(self, data) -> IncomingMessage:
        event = data.event
        msg = event.message
        text = ""
        try:
            content = json.loads(msg.content)
            text = content.get("text", "") or content.get("content", "")
        except Exception:  # noqa: BLE001
            text = getattr(msg, "content", "") or ""

        user_id = ""
        try:
            user_id = event.sender.sender_id.open_id
        except Exception:  # noqa: BLE001
            pass

        return IncomingMessage(
            channel="feishu",
            chat_id=getattr(msg, "chat_id", "") or "",
            user_id=user_id,
            text=text,
            message_id=getattr(msg, "message_id", "") or "",
            raw={},
        )

    def _on_receive(self, data):
        try:
            incoming = self._to_incoming(data)
            if not incoming.text.strip():
                return
            self._dispatcher.handle(self, incoming)
        except Exception as e:  # noqa: BLE001
            logger.exception("feishu handle error: %s", e)

    # ── 长连接 ──

    def run(self):
        dispatcher = (
            lark.EventDispatcher.builder("")
            .register_p2_im_message_receive_v1(self._on_receive)
            .build()
        )
        self._ws = lark.ws.Client(
            app_id=self.app_id,
            app_secret=self.app_secret,
            event_handler=dispatcher,
            log_level=lark.LogLevel.INFO,
        )
        logger.info("feishu ws client starting (app_id=%s)...", self.app_id[:6])
        self._ws.start()  # 阻塞，由 daemon 线程承载

    # ── 出站：回复 / 主动推送 ──

    def reply(self, target: PushTarget, text: str) -> None:
        self._send_text(target.chat_id, text)

    def push(self, target: PushTarget, text: str) -> None:
        self._send_text(target.chat_id, text)

    def _send_text(self, chat_id: str, text: str) -> None:
        if not chat_id or not text:
            return
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        resp = self._client.im.v1.message.create(req)
        if not resp.success():
            logger.warning("feishu send failed: code=%s msg=%s", resp.code, resp.msg)

    def stop(self):
        # lark.ws.Client 无显式 stop 接口；daemon 线程随进程退出即可
        pass
