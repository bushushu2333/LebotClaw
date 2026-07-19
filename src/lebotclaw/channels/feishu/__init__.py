"""飞书原生通道（lark-oapi WebSocket 长连接）。

FeishuChannel 的 import 收敛在 app 层（try import），未安装 lark-oapi 时给友好提示，
避免 channels 包顶层 import 失败。
"""
