# LebotClaw 通用超级智能体 — 需求规格说明书 v2.1

> Spec 阶段：requirements（本文档）→ design.md → tasks.md
> 状态：**v2.1 · 2026-07-22** · 取代 v2.0（决策 A1+B1+C2 维持不变）
> v2.0 变更：skill 对齐 OpenClaw 物种（SKILL.md 文件包）；自演化机制；K12 教育机制；分龄可视化
> v2.1 变更（哥哥反馈）：① 必须能跑 Claude Code / OpenClaw 在用的 md skills（外部源挂载）；
> ② 保留古早 OpenClaw 的 SOUL 文件 + MEMORY 文件机制（文件即真相、人类可改）；
> ③ 用户侧弱化「升级」话术（与已有概念混淆），版本迭代仅为内部工程字段；
> ④ 陪伴叙事改用「陪伴小主人第 N 天 + 累计陪伴 token 数」

---

## 1. 背景与定位

LebotClaw = 通用超级智能体底座 + K12 能力包。三条护城河：

1. **Flow 可见** — 工作流看得见（规划→分步→工具→回退→收口）
2. **Skill 可内化、会成长** — 干得好的套路自动沉淀为 SKILL.md 文件包，复用中自动打磨变好（版本化为内部追溯）
3. **K12 教育属性** — skill 沉淀的不只是"做了什么"，更是"怎么教好的"；全流程分龄、家长可治理、与教育资产（星空/错题本/周报）联动

### 现状对账（代码核实 2026-07-22）

| 能力 | 现状 | 差距 |
|---|---|---|
| SSE 事件流 | `stream_events()` 产 route/wiki/tool/delta | 无 plan 结构，叙事性进度条 |
| Planner | 5 套模板 + replan 关键词回退 | 零进聊 |
| SkillLibrary | JSON 存储 + 阈值沉淀函数 | 零调用；且 JSON 模板不是 OpenClaw 物种（OpenClaw = SKILL.md + scripts 文件包，progressive disclosure 注入） |
| 星空联动 | stream_events 已有 covered.json 埋点 | flow/skill 未接入 |
| K12 元素 | 分龄人设、学科过滤、moderation 双闸 | 全是沿用，新机制教育中立 |

---

## 2. 范围

### In Scope
- **F 系列**：Flow 可见（规划进聊/状态实时/工具显式/回退可见/收口）
- **S 系列**：Skill 内化（SKILL.md 文件包 + LLM 蒸馏沉淀 + 教学法闸 + 检索复用 + 撤销黑名单）
- **U 系列**：Skill 自我演化（变体吸收、失败回滚、近重合并；版本化为内部字段）
- **E 系列**：K12 教育机制（教学策略类目、分龄适配、家长治理、教育资产联动、教学法安全闸）
- **V 系列**：可视化（闯关模式/步骤树模式/技能图鉴/星空联动）
- **L 系列**：CapabilityPack 接口抽离（A1）

### Out of Scope
- DAG 并行执行引擎、多 agent 编排、微信 sidecar
- 跨用户 skill 市场 / clawhub 分发（文件包格式为之留门，本期不做）
- Arxiv 报告、云端部署（旧待续）

---

## 3. 决策记录（2026-07-22 已拍板，维持）

- **A1 接口抽离**：CapabilityPack 协议四要素注册，K12 为 packs/k12 首个实现
- **B1 竖向步骤树**：线性主干 + 回退/跳过分支标记
- **C2 全自动入库**：从严阈值直接入库 + 通知可撤销 + 低分自动淘汰
- v2.0 补充决策（本轮设计自然推论，如有异议哥哥拍）：
  - **D1**：skill 载体 = SKILL.md 文件包（对齐 OpenClaw 物种），弃用 skills.json（自动迁移）
  - **D2**：版本化自我演化（重写 + 变体吸收 + 失败回滚）为**内部机制**，全自动；用户侧不出现「升级」话术，通知文案用「打磨」
  - **D3**：可视化默认分龄——低年级闯关模式，高年级步骤树模式，可手动切换
- v2.1 补充决策（哥哥 07-22 反馈拍定）：
  - **D4**：skill 必须 md 格式、人类可读可改，且能挂载执行 Claude Code / OpenClaw 在用的外部 skills（外部源只读挂载 + 脚本沙盒）
  - **D5**：保留古早 OpenClaw 机制——SOUL.md（人格文件）+ MEMORY.md（长期记忆文件），文件即真相、人类可改、改完即生效
  - **D6**：陪伴叙事 = 「陪伴小主人第 N 天」+「累计陪伴 token 数」，替代 Lv 等级体系

---

## 4. 功能需求（EARS）

### F 系列 · Flow 可见（沿用 v1.1，不变）

**FR-F1 规划进聊**：多步任务 → 先推结构化计划卡片再执行。
**FR-F2 步骤状态实时化**：pending→running→done/skipped/failed，SSE 驱动 ≤500ms。
**FR-F3 工具循环显式化**：tool_round 事件带轮次+原因，归入节点。
**FR-F4 回退/调整可见**：replan 触发 → 步骤树插入/跳过节点 + 原因标注。
**FR-F5 收口**：flow_done 含总结+完成率，归档 flow_runs.jsonl。

### S 系列 · Skill 内化（v2.0 重写）

**FR-S1 文件包载体**
系统 SHALL 以 `~/.lebotclaw/skills/<slug>/SKILL.md` 文件包存储 skill（YAML frontmatter 元数据 + Markdown 正文 procedural knowledge），兼容 OpenClaw / Claude Code skill 格式（最小兼容字段：name、description）。首次启动时若存在旧 skills.json SHALL 自动迁移为文件包。
- 验收：迁移后 SkillLibrary API 行为不变；SKILL.md 可被文本编辑器直接阅读修改，手改后下次会话生效。

**FR-S1b 外部 skill 源挂载（D4）**
系统 SHALL 支持在 config 配置外部 skill 目录（如 `~/.claude/skills`、`~/.openclaw/workspace/skills`），以**只读**方式挂载：参与检索与复用注入，与内生 skill 同机制；frontmatter 缺字段时按最小兼容（name/description）解析。
- SKILL.md 中引用的 scripts/ 脚本 SHALL 可通过内置 script 工具沙盒执行（子进程 + 超时 + 输出截断），脚本执行默认关闭，需 config 显式开启；外部源在图鉴页标注来源（「来自 OpenClaw」），不参与自动演化、不可被自动改写。
- 验收：挂载后 OpenClaw 的 data-analyst skill 可被检索命中并注入生效；未开启脚本开关时注入仅取正文、不执行脚本。

**FR-S2 LLM 蒸馏沉淀**
WHEN 一次 flow 完成且通过入库阈值（完成率>0.85 且有效性>0.75 且零负向反馈）THEN agent SHALL 用 LLM 把本次过程**蒸馏**为 SKILL.md：适用场景、教学/执行步骤、易错点、变体空间——是经验知识，不是步骤流水账。
- 验收：产出 SKILL.md 正文为可复用的方法描述；蒸馏失败（LLM 异常）则放弃本次沉淀，不落 JSON 兜底。

**FR-S3 教学法闸 + 内容闸（入库前）**
WHEN SKILL.md 蒸馏完成 THEN 系统 SHALL 先过两道闸：① moderation 关键词闸（现有）；② 教学法校验（蒸馏 prompt 内置 checklist：不直接给答案、启发式提问、分龄适配、无负面标签），不通过则丢弃并记录原因。
- 验收：蒸馏出"直接给答案式"套路被拦截，skills 目录无该文件。

**FR-S4 自动入库与通知（C2）**
过闸后 SHALL 直接入库，聊天流推送 `skill_saved` 事件：「💾 我学会了一个新本领：xxx」+ 查看/撤销。撤销 → 删除 + skill_undos.json 黑名单（同场景 30 天不再自动沉淀）。

**FR-S5 检索复用（progressive disclosure）**
WHEN 新任务到来 THEN 系统 SHALL 检索（场景 ngram + subject/grade 硬过滤 + effectiveness 排序，外部源同池参与），命中 top1 且过阈值 → **SKILL.md 正文注入 system prompt**（模型照着做，非仅替 Planner 模板），flow 卡片标注「复用了本领：xxx」。
- 验收：存过「分披萨讲分数」后，新会话问分数入门 → 回答体现该教学策略 + 标注可见。

**FR-S6 /skills 技能图鉴页**
用户 SHALL 能浏览全部 skill（图鉴卡片：图标/名称/类目/复用次数/效果条/来源标注），查看详情抽屉（SKILL.md 全文 + 成长日记），可删除/停用/手动新建/编辑（内生 skill 可编辑；外部源只读）。
- 验收：全部 CRUD 可用；deprecated 灰色「已退休」展示。

### U 系列 · Skill 自我演化（v2.0 新增；版本迭代为内部机制，用户侧无「升级」话术）

**FR-U1 复用档案**
每次复用 SHALL 记录档案：有效性信号（完成率/replan 负向/用户反馈关键词）+ agent 标注的「本次变体」（若有）。档案写入 skill 目录 `usage_log.jsonl`。

**FR-U2 演化触发**
WHEN 复用次数达到 5 的倍数，或 effectiveness 较当前版本基线波动 >0.15 THEN agent SHALL 自动演化：LLM 读取当前 SKILL.md + 复用档案 → 重写正文（吸收成功变体、剔除失效步骤）→ 过双闸 → 版本号+1（内部字段），旧版本归档 `versions/`。聊天通知文案用「打磨」：「小博把本领 xxx 打磨得更顺手了✨」。
- 验收：演化后 SKILL.md 反映变体经验；`versions/v1.0.0.md` 可追溯；全流程无「升级」字样外露。

**FR-U3 失败回滚**
WHEN 新版本连续 3 次复用有效性低于上一版本基线 THEN 系统 SHALL 自动回滚到上一版本并标记本次为"失败实验"（保留归档，30 天内不再自动重试同方向）。
- 验收：回滚后版本号回到旧版，成长日记记录完整因果链。

**FR-U4 近重合并**
WHEN 检索发现两个 active 内生 skill 触发场景 ngram 重合度 >0.7 THEN 系统 SHALL 后台合并（保留高分者正文、吸收低分者独特步骤）→ 版本号+1 → 通知。外部源 skill 不参与合并。
- 验收：不产生两个实质相同的 active skill。

### E 系列 · K12 教育机制（v2.0 新增）

**FR-E1 教学策略类目**
skill SHALL 分类目：`teaching_tactic`（怎么教：类比/提问链/scaffolding）| `task_flow`（任务流程）。蒸馏 prompt 按类目分流——teaching_tactic 必含：教学手法、提问链示例、分龄话术变体、对应知识点标签。
- 验收：教学场景沉淀的 skill 带完整教学法字段，非任务流水账。

**FR-E2 分龄适配**
teaching_tactic skill SHALL 记录适用年级段；复用注入时按当前学生年级选取对应话术变体（低年级具体形象/高年级抽象逻辑）。
- 验收：同一 skill 在三年级和初三会话中呈现不同讲法。

**FR-E3 家长治理**
skill 入库/演化/deprecated 事件 SHALL 汇入家长周报（scheduler 周报 taskType 取数）；/skills 页每张卡片附家长可读说明（"这是什么本领、怎么学会的、效果如何"）。
- 验收：周报含 skill 动态小节；图鉴页说明文字无技术黑话。

**FR-E4 教育资产联动**
flow_done SHALL 联动：① 星空 covered.json（接现有埋点）；② skill 知识点标签命中星空节点点亮；③ flow/skill 摘要入周报素材池。
- 验收：完成「分数复习」flow 后星空"分数"节点点亮；周报含本次 flow 摘要。

**FR-E5 教学法红线**
所有 skill 注入/演化内容 SHALL NOT 违反「不直接给答案」规矩；系统 SHALL 在蒸馏/演化 prompt 中内置该红线并在双闸校验。
- 验收：见 FR-S3。

**FR-E6 SOUL 文件 + MEMORY 文件机制（D5，古早 OpenClaw 机制）**
每用户 workspace（`~/.lebotclaw/users/<uid>/`）SHALL 包含两个 md 文件：
- **SOUL.md（人格文件）——强制性保留、不可修改**：承载小博的核心人格与价值观红线（我是谁、怎么对孩子说话、绝不做什么）。系统启动时 SHALL 校验其存在与完整（hash 比对内置母版），被删/被改 SHALL 自动从母版恢复；任何 UI/API/agent 流程 SHALL NOT 提供修改入口。
- **MEMORY.md（长期记忆文件）——人类可读可改**：agent 定期把 memory.db 中的重要条目（画像要点、约定、里程碑）蒸馏追加进来；用户可直接编辑，改完下次会话生效。
system prompt SHALL 按序组合：SOUL.md（底座人格）→ pack 学科人设（heads）→ MEMORY.md → 检索记忆/wiki/skill 注入。
- 验收：删除 SOUL.md 重启后自动恢复且内容同母版；手改 MEMORY.md 加一条「他怕狗」，下次对话小博知道；图鉴/设置页无 SOUL 编辑入口。

**FR-E7 陪伴叙事（D6）**
系统 SHALL 为每用户维护陪伴档案（`companion.json`）：首次会话日期 → 「陪伴小主人第 N 天」；adapter 每次调用的 usage token 累计 → 「一起聊过 X 万 token」。两者 SHALL 展示在 chat 页头部、/skills 图鉴页头、家长周报；聊天流在整数里程碑（第 7/30/100 天、破 10 万 token）轻庆祝。
- 验收：天数按自然日正确递增；token 计数与 adapter usage 一致（误差 <1%）；页面无 Lv/升级字样。

### V 系列 · 可视化（v2.0 新增）

**FR-V1 闯关模式（低年级默认）**
flow 执行时 chat 内 SHALL 呈现闯关卡：当前关卡名 + 进度星（⭐⭐☆☆）+ 小博人设鼓励语 + 工具调用拟人化（「小博正在用计算器帮忙🔧」）；flow_done 播放完成动效 + 徽章。
- 验收：1-4 年级学生 profile 下默认该模式。

**FR-V2 步骤树模式（高年级/家长默认）**
完整步骤树：节点状态、工具徽标、回退分支、复用标注。
- 验收：5 年级以上默认；两模式可手动切换，切换不丢状态。

**FR-V3 技能图鉴**
/skills 页游戏化图鉴：卡片（图标/类目/复用次数/效果条/来源标注）、详情抽屉（含成长日记=版本史+演化日志）、页头展示陪伴天数与累计 token。
- 验收：FR-S6 全项 + 成长日记可读 + 外部源卡片有来源标注且只读。

**FR-V4 星空联动**
flow/skill 知识点命中 SHALL 实时点亮星空页对应节点。
- 验收：FR-E4 验收①②。

### L 系列 · 分层（沿用 v1.1）

**FR-L1 能力包协议**：CapabilityPack 四要素注册；底层不 import K12 具体模块；dummy pack 冒烟通过。

---

## 5. 非功能需求

| 项 | 要求 |
|---|---|
| 兼容红线 | 同步核心不改行为；io_bound 桥接；禁用 cpu_bound |
| 回归 | 现有 67 测试全绿；SkillLibrary API 签名保持（存储后端换文件包，测试改 fixture 不改断言意图） |
| 性能 | 规划判定 ≤100ms；skill 检索 ≤50ms（index.json 缓存，文件不每次全读）；蒸馏/演化为后台任务不阻塞聊天流 |
| LLM 成本 | 蒸馏/演化各 1 次轻量调用（max_tokens ≤1500）；不达标不调用 |
| 可开关 | config：`flow.enabled` / `skills.auto_internalize` / `skills.auto_evolve` / `skills.external_dirs` / `skills.script_exec`（默认关）/ `ui.flow_mode`（auto/quest/tree，默认 auto） |
| 安全 | skill 全生命周期过 moderation + 教学法闸；外部源只读且脚本执行默认关；SOUL.md 内置母版可恢复、无修改入口 |
| 多端 | 飞书透传 plan/flow_done/skill_saved/skill_upgraded 文本摘要 |

---

## 6. 验收指标（演示路径）

1. **Flow 闯关**：三年级 profile 说「帮我复习分数」→ 闯关卡逐步点亮 → 「太难了」→ 回退关卡 → 完成动效+徽章 → 星空"分数"点亮。
2. **Skill 内化闭环**：高质量 flow → 自动沉淀 SKILL.md（教学法字段完整）→ 通知+撤销可用 → 图鉴可见 → 新会话复用（正文注入，讲法体现策略）→ 标注可见。
3. **自我演化**：同一 skill 复用 5 次（含 2 次变体标注）→ 自动演化为 v2（通知文案「打磨得更顺手了」）→ 成长日记可见 → 人为制造 3 次低分 → 自动回滚 v1。
4. **外部 skill**：挂载 `~/.openclaw/workspace/skills` → 图鉴出现带「来自 OpenClaw」标注的卡片 → 提问命中 → 正文注入生效。
5. **SOUL/MEMORY**：删 SOUL.md 重启自动恢复；手改 MEMORY.md 下次对话生效。
6. **陪伴叙事**：页头「陪伴小主人第 N 天 · 一起聊过 X 万 token」随使用增长。
7. **家长治理**：周报含 skill 动态小节；图鉴卡片家长说明无黑话。
8. **分层**：dummy pack 冒烟通过。
