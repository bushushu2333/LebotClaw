"""Flow 引擎（spec: docs/spec/super-agent/design.md §2/§7，需求 FR-F1~F5）。

让多步任务的「规划 → 分步执行 → 工具显式 → replan 回退 → 收口归档」全程以
dict 事件外露（plan/step/tool_round/replanned/flow_done），供 SSE 与前端渲染。

设计要点：
- 纯同步、零 LLM 调用（summary 用模板拼接），嫁接现有 Planner（5 套模板 +
  replan 关键词逻辑），不改 Planner 任何行为。
- router 以鸭子类型注入（只需 classify(text).intent.value），本模块不 import
  K12 具体实现（heads/education），为 P4 分层（A1）留好接口。
- 归档：{user_dir}/flow_runs.jsonl，每行一个 JSON（含 knowledge_points）；
  user_dir 可注入，测试用 tmp_path。
"""

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from lebotclaw.core.planner import Plan, Planner, Step, StepStatus


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"
    INSERTED = "inserted"  # replan 嫁接进来的新节点（待执行）


_STEP_TO_NODE = {
    StepStatus.PENDING: NodeStatus.PENDING,
    StepStatus.IN_PROGRESS: NodeStatus.RUNNING,
    StepStatus.COMPLETED: NodeStatus.DONE,
    StepStatus.SKIPPED: NodeStatus.SKIPPED,
}

_NODE_TO_STEP = {
    NodeStatus.PENDING: StepStatus.PENDING,
    NodeStatus.INSERTED: StepStatus.PENDING,
    NodeStatus.RUNNING: StepStatus.IN_PROGRESS,
    NodeStatus.DONE: StepStatus.COMPLETED,
    NodeStatus.SKIPPED: StepStatus.SKIPPED,
    NodeStatus.FAILED: StepStatus.PENDING,
}


@dataclass
class FlowNode:
    id: int
    title: str
    description: str = ""
    tools_needed: List[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.PENDING
    note: str = ""  # 节点产出摘要 / 跳过原因

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "tools_needed": list(self.tools_needed),
            "status": self.status.value,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FlowNode":
        return cls(
            id=int(data.get("id", 0)),
            title=data.get("title", ""),
            description=data.get("description", ""),
            tools_needed=list(data.get("tools_needed", [])),
            status=NodeStatus(data.get("status", "pending")),
            note=data.get("note", ""),
        )


@dataclass
class FlowRun:
    id: str
    goal: str
    nodes: List[FlowNode] = field(default_factory=list)
    subject: str = "general"
    grade: str = ""
    created_at: float = field(default_factory=time.time)
    replan_log: List[dict] = field(default_factory=list)
    summary: str = ""
    completion_rate: float = 0.0
    skill_used: str = ""  # P2 复用注入时回填（FR-S5 标注）
    knowledge_points: List[str] = field(default_factory=list)
    # 运行时引用（不参与序列化）：驱动 planner.replan 的原始 Plan 对象
    plan: Optional[Plan] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal": self.goal,
            "subject": self.subject,
            "grade": self.grade,
            "created_at": self.created_at,
            "nodes": [n.to_dict() for n in self.nodes],
            "replan_log": list(self.replan_log),
            "summary": self.summary,
            "completion_rate": self.completion_rate,
            "skill_used": self.skill_used,
            "knowledge_points": list(self.knowledge_points),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FlowRun":
        return cls(
            id=data.get("id", ""),
            goal=data.get("goal", ""),
            nodes=[FlowNode.from_dict(n) for n in data.get("nodes", [])],
            subject=data.get("subject", "general"),
            grade=data.get("grade", ""),
            created_at=float(data.get("created_at", time.time())),
            replan_log=list(data.get("replan_log", [])),
            summary=data.get("summary", ""),
            completion_rate=float(data.get("completion_rate", 0.0)),
            skill_used=data.get("skill_used", ""),
            knowledge_points=list(data.get("knowledge_points", [])),
        )


# 计划类关键词：命中即触发规划（spec 1.2：「帮我安排/复习计划/步骤/怎么学」等）
PLAN_KEYWORDS = (
    "帮我安排", "复习计划", "学习计划", "制定计划", "怎么学", "步骤", "备考",
)

# 意图白名单（按 IntentCategory.value 字符串比较，避免 import router）
INTENT_WHITELIST = frozenset({
    "learning_plan", "text_creation", "math_calculation",
})

# replan 反馈词（与 Planner.replan 的负向/受挫关键词对齐，剔除过宽的「烦」）
_REPLAN_FEEDBACK_WORDS = (
    "太难了", "不想学", "放弃", "枯燥", "没意思",
    "错了", "不会", "不懂", "不明白", "太难", "不理解", "听不懂",
)

# 纯算式（如 "3+4=?"）是单步计算，不该触发多步 flow
_BARE_CALC_RE = re.compile(r"^[\d\s+\-*/×÷.^()%（）=？?]+$")

# 知识点抽取用的学科关键词表（星空 covered.json 点亮用；本地最小表，不依赖 router）
_KP_KEYWORDS = (
    "数学", "分数", "小数", "百分比", "几何", "代数", "方程", "函数", "三角",
    "概率", "统计", "数列", "面积", "周长", "体积",
    "语文", "作文", "写作", "阅读", "古诗", "文言文", "汉字", "拼音", "修辞",
    "英语", "英文", "单词", "词汇", "语法", "时态", "翻译", "听力", "口语", "音标",
    "科学", "物理", "化学", "生物", "实验",
)


def extract_knowledge_points(text: str, limit: int = 5) -> List[str]:
    """从目标文本抽取知识点标签（去重、保序、上限 5 个），供星空点亮与归档。"""
    points: List[str] = []
    for kw in _KP_KEYWORDS:
        if kw in (text or "") and kw not in points:
            points.append(kw)
            if len(points) >= limit:
                break
    return points


class FlowEngine:
    """规划触发判定 + FlowRun 生命周期 + replan 嫁接 + 收口归档。

    用法（agent.stream_events 内）：
      engine.should_trigger(text) → engine.create_run(text) → 逐节点
      start_node/complete_node → finalize → archive；
      下一轮对话若 is_replan_feedback(text) → apply_replan(active_run, text)。
    """

    PLAN_KEYWORDS = PLAN_KEYWORDS
    INTENT_WHITELIST = INTENT_WHITELIST

    def __init__(
        self,
        planner: Planner = None,
        router=None,
        user_dir: str = None,
        enabled: bool = True,
    ):
        self.planner = planner or Planner()
        self.router = router  # 鸭子类型：classify(text).intent.value
        self.user_dir = str(user_dir or "~/.lebotclaw")
        self.enabled = enabled  # config: flow.enabled
        self.active_run: Optional[FlowRun] = None  # 最近一次 run，供 replan 嫁接

    # ---- 触发判定（spec 1.2）----

    def should_trigger(self, user_input: str) -> bool:
        """多步任务判定：计划类关键词直接命中；否则看意图白名单。

        普通问答（GENERAL/KNOWLEDGE_QA/EMOTIONAL 等）一律不触发，保证零额外事件。
        """
        if not self.enabled:
            return False
        text = (user_input or "").strip()
        if not text:
            return False
        if any(kw in text for kw in self.PLAN_KEYWORDS):
            return True
        if _BARE_CALC_RE.match(text):
            return False  # 单步算式不出计划卡片
        if self.router is not None:
            try:
                intent = self.router.classify(text).intent
                value = getattr(intent, "value", str(intent))
                return value in self.INTENT_WHITELIST
            except Exception:  # noqa: BLE001
                return False
        return False

    def is_replan_feedback(self, user_input: str) -> bool:
        """学生反馈是否触发 replan（复用 Planner.replan 的负向/受挫关键词口径）。"""
        return any(w in (user_input or "") for w in _REPLAN_FEEDBACK_WORDS)

    # ---- FlowRun 生命周期 ----

    def create_run(self, goal: str, subject: str = "", grade: str = "") -> FlowRun:
        """调 Planner.decompose 生成 Plan，包装成 FlowRun（FR-F1 计划骨架）。"""
        plan = self.planner.decompose(goal=goal, subject=subject, grade=grade)
        nodes = [
            FlowNode(
                id=s.id,
                title=s.title,
                description=s.description,
                tools_needed=list(s.tools_needed),
                status=_STEP_TO_NODE[s.status],
            )
            for s in plan.steps
        ]
        run = FlowRun(
            id="flow_" + uuid.uuid4().hex[:8],
            goal=goal,
            nodes=nodes,
            subject=subject or plan.subject,
            grade=grade or plan.grade,
            knowledge_points=extract_knowledge_points(goal),
        )
        run.plan = plan
        self.active_run = run
        return run

    def start_node(self, run: FlowRun, idx: int) -> FlowNode:
        node = run.nodes[idx]
        if node.status in (NodeStatus.PENDING, NodeStatus.INSERTED):
            node.status = NodeStatus.RUNNING
        self._sync_plan_step(run, idx)
        return node

    def complete_node(self, run: FlowRun, idx: int, note: str = "") -> FlowNode:
        node = run.nodes[idx]
        node.status = NodeStatus.DONE
        if note:
            node.note = note
        self._sync_plan_step(run, idx, advance=True)
        return node

    def skip_node(self, run: FlowRun, idx: int, note: str = "") -> FlowNode:
        node = run.nodes[idx]
        node.status = NodeStatus.SKIPPED
        if note:
            node.note = note
        self._sync_plan_step(run, idx, advance=True)
        return node

    def fail_node(self, run: FlowRun, idx: int, note: str = "") -> FlowNode:
        node = run.nodes[idx]
        node.status = NodeStatus.FAILED
        if note:
            node.note = note
        self._sync_plan_step(run, idx, advance=True)
        return node

    def _sync_plan_step(self, run: FlowRun, idx: int, advance: bool = False):
        """把节点状态同步回内部 Plan（replan 的插入位置依赖 current_step_index）。"""
        if run.plan is None or not (0 <= idx < len(run.plan.steps)):
            return
        step = run.plan.steps[idx]
        step.status = _NODE_TO_STEP[run.nodes[idx].status]
        if run.nodes[idx].status == NodeStatus.DONE:
            step.result = run.nodes[idx].note
        if advance:
            run.plan.current_step_index = idx + 1

    # ---- replan 嫁接（spec 1.5 / FR-F4）----

    def apply_replan(self, run: FlowRun, feedback: str) -> List[dict]:
        """复用 Planner.replan 关键词判定，把变更嫁接到 FlowRun.nodes。

        返回动作列表 [{"action": "insert"|"skip", "node": FlowNode}]，
        每个动作记一条 replan_log（带原因）。
        """
        if run.plan is None:
            run.plan = self._rebuild_plan(run)
        old_nodes = run.nodes
        self.planner.replan(run.plan, feedback)

        actions: List[dict] = []
        new_nodes: List[FlowNode] = []
        oi = 0
        for i, step in enumerate(run.plan.steps):
            if oi < len(old_nodes) and old_nodes[oi].title == step.title:
                node = old_nodes[oi]
                oi += 1
                node.id = i
                mapped = _STEP_TO_NODE[step.status]
                if mapped == NodeStatus.SKIPPED and node.status != NodeStatus.SKIPPED:
                    node.status = NodeStatus.SKIPPED
                    node.note = step.result or "学生掌握良好，跳过此步骤"
                    actions.append({"action": "skip", "node": node})
                new_nodes.append(node)
            else:
                node = FlowNode(
                    id=i,
                    title=step.title,
                    description=step.description,
                    tools_needed=list(step.tools_needed),
                    status=NodeStatus.INSERTED,
                )
                new_nodes.append(node)
                actions.append({"action": "insert", "node": node})
        run.nodes = new_nodes

        for a in actions:
            run.replan_log.append({
                "ts": time.time(),
                "trigger": feedback,
                "action": a["action"],
                "node": a["node"].to_dict(),
            })
        return actions

    def _rebuild_plan(self, run: FlowRun) -> Plan:
        """从归档反序列化的 run（无 plan 引用）重建等价 Plan，供 replan 使用。"""
        steps = [
            Step(
                id=n.id,
                title=n.title,
                description=n.description,
                tools_needed=list(n.tools_needed),
                status=_NODE_TO_STEP[n.status],
                result=n.note,
            )
            for n in run.nodes
        ]
        return Plan(
            goal=run.goal,
            steps=steps,
            current_step_index=len(steps),
            subject=run.subject,
            grade=run.grade,
        )

    # ---- 收口（spec 1.6 / FR-F5）----

    def finalize(self, run: FlowRun) -> FlowRun:
        """计算完成率并用模板拼 summary（不调 LLM）。"""
        total = len(run.nodes)
        done = sum(1 for n in run.nodes if n.status == NodeStatus.DONE)
        skipped = sum(1 for n in run.nodes if n.status == NodeStatus.SKIPPED)
        failed = sum(1 for n in run.nodes if n.status == NodeStatus.FAILED)
        finished = done + skipped
        run.completion_rate = round(finished / total, 2) if total > 0 else 0.0
        parts = ["本次「%s」共 %d 步：完成 %d 步" % (run.goal, total, done)]
        if skipped:
            parts.append("跳过 %d 步" % skipped)
        if failed:
            parts.append("%d 步未攻克" % failed)
        run.summary = "，".join(parts) + "。"
        return run

    def archive(self, run: FlowRun) -> Path:
        """追加写 {user_dir}/flow_runs.jsonl，每行一个 JSON（含 knowledge_points）。"""
        path = Path(self.user_dir).expanduser() / "flow_runs.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = run.to_dict()
        record["archived_at"] = time.time()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path
