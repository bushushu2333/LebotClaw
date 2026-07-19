"""飞书互动卡片流式回复（增强项，本期未启用）。

实现要点（供后续接入）：
1. 先用 ``im.v1.message.create`` 发一条 interactive 卡片，拿到 message_id；
2. agent 流式输出时，节流（>= stream_throttle_ms 或 >= stream_min_delta_chars 增量）
   调 ``im.v1.message.patch`` 原地更新同一张卡；
3. 前置依赖：``Agent.chat_stream_with_tools``（流式 + 工具）。
   本期 ``chat_stream`` 不处理 tool_calls（会丢工具），故流式卡片暂不做，
   MVP 飞书走文本整段回复（client._send_text）。
"""
