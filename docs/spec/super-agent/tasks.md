# LebotClaw 通用超级智能体 — 任务拆解 v2.1

> 状态：**v2.1 · 2026-07-23 P1-P3 主体落地，199 tests 全绿，待哥哥验收**（A1+B1+C2 冻结；D1-D6 哥哥已反馈拍定，含 SOUL 强制只读）
> 上游：requirements.md v2.1 / design.md v2.1

---

## P0 · 决策与对齐

| # | 任务 | 状态 |
|---|---|---|
| 0.1 | 拍板 A/B/C → A1+B1+C2 | ✅ 2026-07-22 |
| 0.2 | 对齐 OpenClaw 现状（SKILL.md 实物已核实：文件包+frontmatter+scripts） | ✅ 2026-07-22 |
| 0.3 | v2.0/v2.1 补充决策 D1-D6（SKILL.md 载体/演化为内部机制/分龄渲染/外部源挂载/SOUL+MEMORY/陪伴叙事） | ✅ 2026-07-22 哥哥反馈拍定（SOUL 强制只读） |

## P1 · Flow 可见 + 分龄可视化（估 5-6 天）

> **✅ 1.1-1.9 全部完成（2026-07-23）**：core/flow.py（23 测试）+ agent.py 事件接入 + dashboard 闯关/步骤树双模式 + 飞书 collapse_events_to_text 透传（CLI 保持原样）。真模型 E2E 验证 plan→step→tool_round→flow_done 全链路。

| # | 任务 | 验收 | 依赖 |
|---|---|---|---|
| 1.1 | `core/flow.py`：FlowNode/FlowRun + 事件发射器 | 单测：序列化 round-trip | 0.3 |
| 1.2 | 规划触发判定 + `plan` 事件接入 stream_events | 多步任务出 plan 事件，普通问答零事件 | 1.1 |
| 1.3 | 节点驱动执行 + `step` 状态事件 | 逐步亮灯 | 1.2 |
| 1.4 | `tool_round` 事件（轮次+原因） | 连环调用可见 | 1.3 |
| 1.5 | replan 嫁接 + `replanned` 事件 | 「太难了」出回退节点 | 1.3 |
| 1.6 | 收口 `flow_done` + flow_runs.jsonl（含 knowledge_points）+ 星空 covered.json 联动 | 分数复习后星空点亮 | 1.5 |
| 1.7 | 步骤树渲染器（高年级/家长） | 演示路径步骤树全通过 | 1.4-1.6 |
| 1.8 | 闯关渲染器（低年级）：冒险地图卡/进度星/拟人化工具气泡/CSS confetti+徽章 | 三年级 profile 全程闯关呈现 | 1.7 |
| 1.9 | 模式切换（auto/quest/tree）+ 飞书/CLI 文本透传 + config 开关 + 回归测试 | 切换不丢状态；pytest 全绿 | 1.7,1.8 |

## P2 · Skill 内化 2.0 + workspace 文件（估 6-7 天）

> **✅ 2.1-2.12 全部完成（2026-07-23）**：skillstore.py（28 测试）+ workspace.py（12 测试）+ distiller.py（21 测试，真模型蒸出首个 skill）+ 图鉴 API/页面 + 撤销黑名单 + 外部源挂载（OpenClaw 39 个 skill 实测）+ ScriptSandbox 默认关。2.10 按哥哥口径收敛为「沙箱默认关、外部 skill 仅作 md 指引」。

| # | 任务 | 验收 | 依赖 |
|---|---|---|---|
| 2.1 | SkillStore 文件包后端（SKILL.md 读写 + index.json 缓存 + skills.json 自动迁移），API 签名不变 | 迁移测试过；67 测试 fixture 改后全绿 | 0.3 |
| 2.2 | 复用档案 usage_log.jsonl + effectiveness 滚动评分 | 复用记录正确落盘 | 2.1 |
| 2.3 | 蒸馏器：类目判定 + 双模板 prompt（teaching_tactic 四节强制）+ LLM 调用 + YAML 校验 | 产出合法 SKILL.md；teaching_tactic 四节完整 | 2.1 |
| 2.4 | 双闸：moderation + 教学法 checklist；失败宁缺毋滥 | 给答案式套路被拦截 | 2.3 |
| 2.5 | 自动入库 + `skill_saved` 事件 + 撤销 + 30 天黑名单 | 演示路径二入库+撤销生效 | 2.4 |
| 2.6 | 检索复用：index ngram + 过滤 + 命中注入 SKILL.md 正文（分龄变体选段）+ `skill_used` 标注 | 复用后讲法体现策略+标注可见 | 2.5 |
| 2.7 | 技能图鉴页 v1：卡片（复用次数/效果条/来源标注）/类目 tab/详情抽屉/CRUD/手动新建 | FR-S6 全项 | 2.5 |
| 2.8 | 家长说明字段（蒸馏生成）+ 开关 + 测试 | 卡片说明无黑话 | 2.7 |
| 2.9 | 外部 skill 源挂载：目录扫描+最小兼容解析+index 标 external+mtime 增量扫+只读红线 | 挂载 OpenClaw skills 后图鉴出现带来源标注卡片且可命中注入 | 2.6 |
| 2.10 | ScriptSandbox：run_skill_script 工具（超时/截断/cwd 限定）+ skills.script_exec 默认关 | 开关关闭时不注册工具不执行；开启后 data-analyst 类脚本可跑 | 2.9 |
| 2.11 | SOUL/MEMORY 文件机制：母版+chmod 444+hash 校验恢复+MEMORY.md 蒸馏写入钩子+prompt 组合顺序 | 删 SOUL 重启恢复；手改 MEMORY 下次会话生效 | 2.1 |
| 2.12 | 陪伴档案 companion.json：天数+token 累计（usage 统计+粗估兜底）+ chat/图鉴页头展示+里程碑卡片 | 天数/token 随使用增长；页面无 Lv/升级字样 | 2.11 |

## P3 · 自我演化引擎（估 3-4 天）

> **✅ 3.1-3.6 全部完成（2026-07-23）**：core/evolve.py（19 测试）——%5/Δ0.15 触发、LLM 打磨版本+1、versions/ 归档、3 低分回滚+30 天冷却、ngram>0.7 合并（内部限定）。全量 199 passed。

| # | 任务 | 验收 | 依赖 |
|---|---|---|---|
| 3.1 | 演化触发（usage%5 / Δeffectiveness>0.15，仅内生）+ LLM 重写 + 双闸 + 版本归档 + 「打磨」文案通知 | 5 次复用后自动演化，v1 可追溯，无「升级」字样 | 2.2,2.4 |
| 3.2 | 变体标注解析（system 注入要求+标记行解析） | 变体入档案，演化时吸收 | 3.1 |
| 3.3 | 失败回滚（连续 3 次低分→回旧版+failed_experiment 冷却） | 演示路径三回滚成功 | 3.1 |
| 3.4 | 近重合并（ngram>0.7 → LLM 合并 → 被并者 deprecated） | 不产生实质重复 active skill | 3.1 |
| 3.5 | 成长日记（版本史+演化日志+回滚记录）进图鉴详情抽屉 + 「打磨」聊天通知 | 图鉴可读完整成长链 | 3.1-3.4 |
| 3.6 | skill_events.jsonl + 家长周报「本领成长」小节 + 回归测试 | 周报含 skill 动态；pytest 全绿 | 3.5 |

## P4 · 分层接口抽离（估 2-3 天，可与 P2/P3 并行）

| # | 任务 | 验收 | 依赖 |
|---|---|---|---|
| 4.1 | CapabilityPack 协议（四要素 + skill_templates/quest_copy 可选要素）+ K12Pack re-export | 行为与现状一致 | 0.3 |
| 4.2 | core/flow + core/skill 去 K12 import | grep 无 heads/router 引用 | 4.1 |
| 4.3 | packs/dummy 冒烟 + 测试 + docs/packs.md | dummy pack 跑通 | 4.2 |

## 里程碑

- **M1（P1 完）**：Flow 双模式可演示（闯关+步骤树+星空联动）
- **M2（P2 完）**：Skill 内化闭环（SKILL.md 沉淀+图鉴+分龄复用+外部源挂载+SOUL/MEMORY+陪伴叙事）
- **M3（P3 完）**：自我演化可演示（演化+变体吸收+回滚+成长日记+家长周报）
- **M4（P4 完）**：通用底座叙事成立，可写 Arxiv 系统设计章

## 关键依赖链

```
0.3 ─→ P1（1.1→…→1.9）
0.3 ─→ P2（2.1→…→2.12）─→ P3（3.1→…→3.6）
0.3 ─→ P4（可全程并行）
P1 与 P2 仅在 1.6（flow_done 回调）与 2.5（入库挂点）弱耦合，可并行开发、联调合并
```
