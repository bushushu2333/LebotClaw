"""统一消息分发：收消息 → 去重 → 会话绑定 → 对话 → 回复。

飞书等 IM 通道在后台线程收到消息后调 ``Dispatcher.handle``。
collapse_events_to_text 已含命令分流 + 路由 + 事件流折叠（Flow 痕迹透传），
此处只做通道层编排。
"""
from lebotclaw.channels.base import ChatChannel, IncomingMessage, PushTarget
from lebotclaw.web.chat_bridge import collapse_events_to_text


class Dispatcher:
    def __init__(self, runtime):
        self.rt = runtime
        self._seen: set[str] = set()      # 去重：(channel, message_id)

    def handle(self, channel: ChatChannel, msg: IncomingMessage) -> None:
        # 1. 去重（IM 平台可能重投）
        dedupe_key = f"{msg.channel}:{msg.message_id}" if msg.message_id else ""
        if dedupe_key:
            if dedupe_key in self._seen:
                return
            self._seen.add(dedupe_key)
            if len(self._seen) > 10000:
                self._seen = set(sorted(self._seen)[-5000:])

        target = PushTarget(channel=msg.channel, chat_id=msg.chat_id)

        # 2. 会话绑定：channel:chatId → 独立 SessionContext（与 Web 隔离）
        ctx = self.rt.sessions.by_channel_key(msg.channel, msg.chat_id)

        # 3. 记默认推送目标（之后 cron/agent 可主动推到这个会话）
        if self.rt.channels:
            self.rt.channels.set_default_target(msg.channel, target)

        # 4. 跑对话（事件流折叠成文本：Flow/Skill/陪伴痕迹透传到纯文本通道，spec 1.9）
        try:
            reply = collapse_events_to_text(ctx, msg.text)
        except Exception as e:  # noqa: BLE001
            reply = f"⚠ 出了点问题：{e}"

        # 5. 回复
        if reply:
            try:
                channel.reply(target, reply)
            except Exception:  # noqa: BLE001
                pass  # 回复失败不阻塞主流程
