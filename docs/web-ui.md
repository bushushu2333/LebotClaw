# LebotClaw Web UI / 飞书通道 / cron 推送

LebotClaw 在终端 CLI 之外，新增了 **Web UI（NiceGUI）**、**飞书原生通道**、**cron 主动推送**。
三者复用同一套同步运行时（`Agent.chat`，保留工具调用 + 记忆），微信通道留 bridge 接口（本期不接 sidecar）。

## 安装

```bash
pip install -e ".[all]"     # nicegui + APScheduler + lark-oapi
# 或分组安装：pip install -e ".[web]"  /  pip install -e ".[feishu]"
```

## Web UI

```bash
lebotclaw web               # → http://127.0.0.1:8080
```

| 页面 | 说明 |
|---|---|
| `/` | 聊天：左侧学科切换、对话流、`/帮助 /切换 /重置` 等命令 |
| `/memory` | 4 类学习记忆（学生画像 / 学习进度 / 已讲知识点 / 会话摘要） |
| `/plan` | 学习计划（接入现有 `Planner`，按目标分解步骤） |
| `/settings` | 学生信息（写回 config + memory）、模型状态、对话风格 |
| `/jobs` | 定时任务管理 |
| `/wiki` | 知识库（添加/浏览 L2 知识页，agent 回答时自动检索引用） |

## 流式输出

Web 聊天与 `/api/chat/stream` 都是**流式输出**（逐句显示），且不丢工具调用：
`Agent.chat_stream_with_tools` 先非流式探测 `tool_calls`——有工具就执行后第二轮真流式，
无工具则把完整回复按句切分模拟流式。所以数学题（触发 calculator）不会因流式而算错。

## 知识库（L2 wiki）

区别于 memory（对话自动抽取的碎片记忆），wiki 是你**主动沉淀的结构化知识页**：
在 `/wiki` 添加（标题 / 内容 / 来源 / 标签），agent 每轮按当前问题用 2/3-gram 检索相关页
注入 system prompt，让回答有据可依。适合存：课文知识点、错题套路、学校规定、辅导口径等。

CLI（`lebotclaw`）同样享受 wiki 注入——`create_default_registry` 已共享同一个 `WikiStore`。

## 飞书通道（原生 WebSocket，无需公网回调）

1. 在[飞书开放平台](https://open.feishu.cn)建**自建应用**，开启「机器人」能力，
   并订阅事件 `im.message.receive_v1`（接收消息）；
2. 在 `~/.lebotclaw/config.json` 配置：

```jsonc
"channels": {
  "feishu": {
    "enabled": true,
    "app_id": "cli_xxx",
    "app_secret": "xxx",
    "default_chat_id": "oc_xxx"     // cron 推送的回落目标
  }
}
```

3. `lebotclaw web` 启动后自动以 WebSocket 长连接接入飞书；**私聊机器人**即可收发消息。

> Web 当前会话与飞书会话**相互隔离**（各自独立 SessionContext），不会串话。

## cron 主动推送

`config.scheduler.enabled = true` 并配好飞书 `default_chat_id`，在 `/jobs` 页或 `/api/jobs` 建任务：

| task_type | 行为 |
|---|---|
| `push_reminder` | 纯文本提醒，**不跑 LLM**，直接推送 |
| `daily_review` | 每日复习清单（跑 agent 生成） |
| `weekly_summary` | 每周学情周报（跑 agent 生成） |
| `spaced_review` | 针对薄弱点的间隔复习（跑 agent 生成） |
| `custom_prompt` | 自定义提示（跑 agent 生成） |

支持 `one_shot`（一次性任务，触发后自动禁用）；推送目标按
`job.channel/chat_id` → 通道默认目标 → `scheduler.default_*` 三级回落。

## HTTP API（`/api/*`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康/模型/会话数 |
| POST | `/api/chat` | `{session_id, message}` → `{reply}` |
| GET | `/api/chat/stream` | SSE 占位（MVP 内部仍走 `chat()`） |
| POST | `/api/bridge/messages` | 微信 sidecar 占位（返回 501） |
| GET/POST/DELETE | `/api/jobs` | cron 任务 CRUD |
| POST | `/api/jobs/{id}/run` | 立即触发一个任务 |

> `/api/*` 默认不校验（仅绑 `127.0.0.1`）；设 `web.api_token` 后需 `Authorization: Bearer <token>`。

## 微信通道（留接口，本期不启用）

微信没有官方个人号 API，非官方协议（wechaty / iPad 协议等）有**封号风险**，K12 场景不宜使用。
本仓库仅保留 bridge 契约（见 `channels/bridge.py`），未接 sidecar。如需合规触达微信，
建议对接**企业微信官方 API** 或**微信小程序**。

## 配置完整示例

见 `web/runtime.py` 的 `_default_config()`——缺字段会自动合并默认值，无需手写全部。
