# LebotClaw 通用超级智能体 — 设计文档 v2.1

> 状态：**v2.1 · 2026-07-22** · 决策冻结（A1+B1+C2 + D1/D2/D3 + D4/D5/D6 哥哥已拍）
> 上游：requirements.md v2.1
> v2.1 变更：外部 skill 源挂载（跑 Claude Code/OpenClaw 的 md skills）+ SOUL/MEMORY 文件机制
> （SOUL 强制只读可恢复）+ 陪伴叙事（天数+token，替代 Lv）+ 用户侧去除「升级」话术

---

## 1. 总体架构

```
┌─ 表现层 ─────────────────────────────────────────────────┐
│  chat 页：闯关卡（低年级）/ 步骤树（高年级）· 页头陪伴天数+token │
│  /skills 技能图鉴页（卡片+详情抽屉+成长日记+外部源标注）          │
│  星空页（flow/skill 知识点点亮）  家长周报（skill 动态+陪伴数据）  │
├─ 桥接层（io_bound 红线不变）──────────────────────────────┤
│  chat_bridge.blocking_stream_events（事件源已就位）        │
├─ core（新增通用底座，不 import K12）──────────────────────┤
│  FlowEngine          SkillEngine                         │
│  ├ FlowRun/FlowNode  ├ SkillStore（SKILL.md 文件包）      │
│  ├ 事件发射器        ├ Distiller（LLM 蒸馏+双闸）          │
│  └ 节点驱动循环      ├ Retriever（内生+外部源同池检索）     │
│   （包现有循环）     ├ EvolveEngine（版本/变体/回滚/合并） │
│                     └ ScriptSandbox（外部脚本，默认关）    │
│  WorkspaceFiles（SOUL.md 母版校验 + MEMORY.md 读写）       │
├─ core（现有，不动行为）───────────────────────────────────┤
│  Agent / Planner / IntentRouter / Memory / Wiki / 守护    │
├─ packs/（A1）────────────────────────────────────────────┤
│  k12/（heads + 路由规则 + planner 模板 + 教育工具）        │
└──────────────────────────────────────────────────────────┘
```

**思路依然是嫁接**：FlowEngine 包住现有 `stream_events` 循环；SkillEngine 挂 flow 完成回调与复用钩子；表现层只做事件渲染。

---

## 2. Skill 文件包格式（对齐 OpenClaw 物种）

### 2.1 目录结构

```
~/.lebotclaw/skills/
  index.json                    # 检索索引缓存：slug → {tokens, subject, grades, category, status, effectiveness, usage, lv}
  fen-pizza-fractions/
    SKILL.md                    # 当前版本（frontmatter + 正文）
    usage_log.jsonl             # 复用档案（FR-U1）
    versions/
      v1.0.0.md                 # 历史版本（FR-U2 归档）
```

### 2.2 SKILL.md 模板

```markdown
---
name: fen-pizza-fractions
title: 分披萨讲分数
category: teaching_tactic        # teaching_tactic | task_flow
version: 2.1.0                   # 内部工程字段，不外露
status: active                   # active | deprecated
source: internal                 # internal | external（外部源只读）
subject: math
grades: [三年级, 四年级]
knowledge_points: [分数的初步认识]  # 挂星空节点（FR-E4）
bloom: [理解, 应用]
trigger: 分数 入门 概念 切分 平均
effectiveness: 0.82
usage_count: 12
created_at: 2026-07-25T10:00:00
source_flow: flow_8f3a
---
# 适用场景
孩子第一次接触分数，对"1/2 是什么意思"没有直觉……

# 教学法
手法：实物类比 + 提问链。先生活物（披萨/蛋糕）切分，再抽象到符号。
禁忌：不要一上来写 1/2 的符号定义（三年级还没建立"平均分"概念）。

# 提问链示例
1. "一个披萨咱俩分，怎么分才公平？"（引出"平均分"）
2. "每人拿到的是整个披萨的多少？"（引出"一半"）
3. "如果 4 个人分呢？"（迁移）

# 分龄话术变体
- 低年级（1-4）：用披萨/蛋糕，多 emoji，一次只问一句
- 高年级（5-9）：可用"切蛋糕不公平怎么办"引出分数比较

# 易错点
- 孩子容易把"1/2 比 1/4 小"（分子分母直觉颠倒）→ 用图对比

# 演化日志
- v2.1.0 (2026-08-01): 吸收变体「披萨→蛋糕」（3 次复用验证有效）
- v2.0.0 (2026-07-28): 加入提问链第 3 问（迁移环节）
```

**为什么是这个格式**：模型读了就能照做（progressive disclosure），人类可读可改，未来可挂 scripts/ 与 clawhub 生态打通。frontmatter 承载机器检索字段，正文承载经验知识——检索看 frontmatter 索引，注入读正文。

### 2.3 兼容与迁移

- `SkillLibrary` 类名与方法签名（add/find/update/list）保持，后端从 skills.json 换为文件包
- 启动时检测：skills.json 存在且 skills/ 为空 → 自动逐条转 SKILL.md（steps_template 拼成正文）→ 原文件改名 skills.json.migrated
- index.json 每次增删改 skill 时重建（秒级，几十个 skill 无压力）；检索只读 index，命中后才读 SKILL.md 正文

### 2.4 外部 skill 源挂载（D4）

```
config.skills.external_dirs = ["~/.claude/skills", "~/.openclaw/workspace/skills"]
```

- **只读挂载**：启动时扫描外部目录的 `*/SKILL.md`，frontmatter 按最小兼容解析（name/description 必填，其余缺省）；条目进 index.json 时标 `source: external` + `origin: <目录名>`（图鉴显示「来自 OpenClaw」）
- **同池检索**：内外 skill 一起 ngram 打分排序；外部源 effectiveness 初始 0.7，被复用也记录档案、滚动评分——但**永不自动演化/合并/改写**（只读红线）
- **脚本沙盒**（ScriptSandbox）：SKILL.md 引用 scripts/ 时，注册一个 `run_skill_script` 工具 → 子进程执行（timeout 30s、输出截断 4KB、cwd 限定 skill 目录、继承最少环境变量）；`skills.script_exec` 默认 false——关闭时注入正文但剔除执行指令段，工具不注册
- **安全**：外部 SKILL.md 注入前同样过 moderation 闸；外部源加载失败（目录不存在/格式坏）静默跳过记日志，不影响启动
- **手改生效**：外部/内生 SKILL.md 都是 mtime 监听（启动时全量 + 每 5 min 增量扫），改了下次检索即用新内容

---

## 3. 内化流水线（Distiller）

```
flow_done ─→ 阈值判定（0.85/0.75/零负向 + 撤销黑名单）
         ─→ 判定类目（teaching_tactic / task_flow）
         ─→ LLM 蒸馏（1 次轻量调用，max_tokens≤1500）
            prompt 输入：flow 全程摘要（节点+工具+replan+关键对话片段≤800字）
            prompt 内置：SKILL.md 模板 + 教学法 checklist（红线：不给答案/启发式/分龄/无负面标签）
         ─→ 闸① moderation.check（现有关键词闸）
         ─→ 闸② 教学法校验（蒸馏输出自评 checklist 全过，任一不过丢弃+记日志）
         ─→ 写文件包 + 重建 index + skill_saved 事件
```

- 蒸馏是**后台任务**（线程池），不阻塞聊天流；skill_saved 事件经下一次心跳/当前会话 SSE 补发
- 蒸馏失败（LLM 异常/输出不合法 YAML）→ 放弃，不落兜底 JSON——宁缺毋滥（C2 无人工闸，质量靠阈值+双闸守）

---

## 4. 演化引擎（EvolveEngine）

> 版本迭代是**内部机制**：frontmatter 的 version 字段与 versions/ 归档是工程追溯用，
> 用户侧（通知/图鉴/周报）一律用「打磨」话术，不出现「升级」「Lv」。

### 4.1 复用档案（usage_log.jsonl）

```json
{"ts": ..., "flow_id": "...", "outcome": {"completion": 0.9, "replan_negative": 0, "feedback": "positive"}, "variant": "把披萨换成了蛋糕", "effectiveness": 0.85}
```

变体来源：复用时 agent 在 system 注入末尾被要求「若你做了与原 skill 不同的调整，用一行简述」——stream_events 循环末尾解析该标记行记入档案（解析不到则空，不影响主流程）。

### 4.2 演化流程

```
触发（usage%5==0 或 |Δeffectiveness|>0.15，仅内生 skill）
 ─→ LLM 读当前 SKILL.md + 近 10 条档案 → 重写
 ─→ 双闸（同内化）─→ 版本号+1（内部字段）
 ─→ 旧版归档 versions/ ─→ 重建 index ─→ skill_evolved 事件
    （文案：「小博把本领 xxx 打磨得更顺手了✨」）
```

### 4.3 回滚

新版本 `rollback_watch = 3`：连续 3 次复用 effectiveness 低于旧版基线 → 自动换回旧版文件，本次标记 `failed_experiment`（归档保留），同方向 30 天冷却。成长日记记录完整因果链（图鉴页可读）。

### 4.4 合并

检索时顺带算两两 active 内生 skill 的 trigger ngram 重合度（index 里有 tokens，增量计算），>0.7 → 后台 LLM 合并（高分者正文为主干，吸收低分者独特步骤/变体）→ 双闸 → 被并者 deprecated（标注 merged_into）→ 通知。外部源不参与。

### 4.5 陪伴档案（D6，替代 Lv 体系）

`~/.lebotclaw/users/<uid>/companion.json`：

```json
{"first_seen": "2026-07-22", "total_tokens": 152340, "last_active": "2026-07-29"}
```

- **天数**：自然日差 + 1 → 「陪伴小主人第 N 天」
- **token**：adapter 每次 generate/stream 返回的 usage（prompt+completion）累加；adapter 不返回 usage 时按字符数/4 粗估（记 estimated 标记）
- 展示位：chat 页头部（「🐾 陪伴第 42 天 · 一起聊过 15.2 万 token」）、图鉴页头、家长周报
- 里程碑轻庆祝：第 7/30/100/365 天、破 1万/10万/100万 token → 聊天流一张小卡（小博口吻，模板文案不调 LLM）

---

## 5. K12 教育机制

| 机制 | 实现 |
|---|---|
| 教学策略类目（E1） | 蒸馏 prompt 按类目分流两套模板；teaching_tactic 模板强制 教学法/提问链/分龄变体/易错点 四节 |
| 分龄适配（E2） | 注入时按学生 profile 年级选取 SKILL.md「分龄话术变体」对应段拼入 system（而非全文） |
| 家长治理（E3） | skill 事件写 `~/.lebotclaw/skill_events.jsonl`；scheduler 周报 taskType 读它生成「本领成长」小节；图鉴卡片配一句家长可读说明（蒸馏时生成） |
| 星空联动（E4） | flow_done 与 skill.knowledge_points 都写 covered.json（复用现有埋点通道）；星空页无需改结构 |
| 周报素材（E4） | flow_runs.jsonl + skill_events.jsonl 即为周报取数源，job_runner 周报模板加两节 |
| 教学法红线（E5） | 双闸内置；teaching_tactic 入库后再加一条运行时校验：复用注入文本必含提问链段（防空策略注入） |

---

## 5.5 SOUL / MEMORY 文件机制（D5，古早 OpenClaw 机制）

### 文件布局（每用户一份，多用户隔离）

```
~/.lebotclaw/users/<uid>/
  SOUL.md        # 人格文件：强制保留、不可修改（内置母版）
  MEMORY.md      # 长期记忆文件：人类可读可改
  companion.json # 陪伴档案（§4.5）
```

### SOUL.md —— 只读红线

- 内容：小博核心人格与价值观（15 岁男孩/善良果敢/共情/绝不直接给答案/红线清单）——由 K12 pack 提供母版（`packs/k12/SOUL.master.md`），本质是把 heads.py 里最底层的「你是谁」抽成文件
- **不可修改机制**：① 文件 chmod 444；② 启动时 sha256 比对母版，缺失或被改 → 自动重写恢复并记日志；③ 所有 UI/API（settings、图鉴、文件接口）不提供 SOUL 编辑入口；④ agent 全部工具集中无写 SOUL 的路径
- 为什么强制：K12 产品的人格底座是安全承诺，不能被 prompt 注入、孩子误操作或外部 skill 改写

### MEMORY.md —— 文件即真相

- 写入：每次会话收尾（summarize_session 现有钩子）后，后台蒸馏追加重要条目——画像要点/约定/里程碑（「他怕狗」「约定每天背 5 个单词」「2026-08-01 第一次独立解出方程」），去重后追加，单条 ≤80 字
- 读取：system prompt 组合顺序 **SOUL.md（底座）→ pack 学科人设（heads）→ MEMORY.md → 检索记忆/wiki/skill 注入**；MEMORY.md 超过 200 行时只注入最近 100 行 + 早段摘要
- 手改生效：用户直接编辑文件，mtime 监听，下次会话即生效——家长想纠正一条记忆，改文件就行，不用学任何 UI

### 与现有 MemoryStore 的关系

memory.db 继续承载结构化数据（检索条目/画像字段/freeze 上下文）；MEMORY.md 是**面向人与注入的叙事层**，二者由蒸馏钩子单向同步（db → md），不反向解析。

---

## 6. 可视化设计

### 6.1 双模式渲染（同一事件流，两套渲染器）

```
SSE 事件（plan/step/tool_round/replanned/flow_done/skill_*）
   ├── 闯关渲染器（quest）：年龄档 1-4 默认
   └── 步骤树渲染器（tree）：年龄档 5+ 与家长默认
```

`ui.flow_mode = auto|quest|tree`（config + 页面切换按钮，切换只改渲染层，事件流不变、状态不丢）。

**闯关渲染器**：
- 计划卡片 → 「冒险地图」卡：关卡格子（🗺️ 第1关 知识点回顾 → 第2关 例题挑战…）
- step 事件 → 当前关卡格子点亮 + 进度星 ⭐⭐☆☆ + 小博鼓励语（文案随事件类型模板化，不额外调 LLM）
- tool_round → 「小博正在用计算器帮忙🔧」气泡
- replanned → 「没事，我们回头补一下基础💪」+ 回退格插入
- flow_done → 🎉 完成动效（CSS confetti）+ 徽章卡（「分数小达人」）
- skill_saved/evolved → 「学会新本领 / 本领更顺手了」收集卡动效（翻卡入图鉴）

**步骤树渲染器**：v1.1 设计（节点状态/工具徽标/回退分支/复用标注）。

### 6.2 技能图鉴页（/skills）

- 页头：陪伴档案条（「🐾 陪伴小主人第 N 天 · 一起聊过 X 万 token」）
- 顶部 tab：全部 / 教学策略 / 任务流程 / 外部来源 / 已退休
- 卡片：emoji 图标（蒸馏时生成）+ 名称 + 复用次数 + 效果条 + 来源标注（外部源显示「来自 OpenClaw」）+ 一句话家长说明
- 详情抽屉：SKILL.md 渲染（markdown）、成长日记（版本史 + 演化日志 + 回滚记录）、操作（编辑/停用/删除——外部源仅「停用挂载」，只读）
- 手动新建：markdown 表单（frontmatter 字段表单化，正文 textarea）

### 6.3 SSE 事件协议（向后兼容，新增）

| 事件 | 载荷 | 时机 |
|---|---|---|
| `plan` / `step` / `tool_round` / `replanned` / `flow_done` | 同 v1.1 | flow 全周期 |
| `skill_saved` | {slug, title, category, 家长说明} | 入库 |
| `skill_evolved` | {slug, title, from_v, to_v, 变更摘要（「打磨」文案）} | 演化/合并 |
| `skill_used` | {slug, title, source} | 复用注入（flow 卡片标注） |
| `companion_milestone` | {kind: days\|tokens, value} | 陪伴里程碑轻庆祝 |

旧前端对未知事件静默忽略。

---

## 7. Flow 引擎（沿用 v1.1 B1 设计，不重复）

FlowNode/FlowRun 数据模型、节点驱动执行、replan 嫁接、收口归档均按 v1.1 §2。v2.0 仅新增：`flow_done` 时把知识点标签写 covered.json（E4）、flow_runs.jsonl 增加 `knowledge_points` 字段。

---

## 8. 分层（沿用 v1.1 A1，不重复）

CapabilityPack 四要素协议 + packs/k12 re-export + dummy 冒烟。v2.0 补充：teaching_tactic 蒸馏模板、闯关文案模板属于 K12 pack 资产，pack 协议增加可选 `skill_templates()` 与 `quest_copy()` 两要素（默认空实现 → 底层不依赖 K12）。

---

## 9. 存储与配置汇总

| 项 | 位置 | 说明 |
|---|---|---|
| skills/ | `~/.lebotclaw/` | 内生 SKILL.md 文件包 + index.json + versions/ |
| skills.json | 旧 | 自动迁移后改名 .migrated |
| 外部 skill 源 | config 指定（如 `~/.openclaw/workspace/skills`） | 只读挂载，mtime 增量扫描 |
| users/\<uid\>/SOUL.md | `~/.lebotclaw/` | 人格文件，chmod 444 + 母版 hash 校验可恢复 |
| users/\<uid\>/MEMORY.md | `~/.lebotclaw/` | 长期记忆叙事层，人类可改 |
| users/\<uid\>/companion.json | `~/.lebotclaw/` | 陪伴档案（首日/token 累计） |
| skill_undos.json | `~/.lebotclaw/` | 撤销黑名单 30 天 |
| skill_events.jsonl | `~/.lebotclaw/` | 入库/演化/回滚/合并/deprecated（周报取数） |
| flow_runs.jsonl | `~/.lebotclaw/` | flow 归档（+knowledge_points） |
| config | config.json | flow.enabled / skills.auto_internalize / skills.auto_evolve / skills.external_dirs / skills.script_exec（默认关）/ ui.flow_mode |

---

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| C2 全自动误沉淀 | 三重兜底（从严阈值+撤销黑名单+低分淘汰）+ 双闸 |
| LLM 蒸馏/演化质量不稳 | 模板强约束 + YAML 校验 + 失败宁缺毋滥；演化为后台任务 |
| 演化越磨越差 | 回滚机制（FR-U3）+ failed_experiment 30 天冷却 |
| 蒸馏/演化 LLM 成本 | 各 1 次轻调用且仅达标触发；月度量极小 |
| 外部 skill 带来不可控行为 | 只读挂载 + 注入过 moderation + 脚本执行默认关 + 不参与演化 |
| SOUL.md 被篡改/注入 | chmod 444 + 母版 hash 校验自动恢复 + 全链路无写入口 |
| 文件包并发写 | 蒸馏/演化都走 io_bound 线程池 + 单写者锁（复用现有 RLock 模式） |
| 67 测试回归 | SkillLibrary API 签名不变，测试改 fixture；flow/skill 全新增模块 |
| 闯关文案低幼化惹高年级反感 | auto 按年级分档 + 手动切换 + 高年级默认步骤树 |
