"""超级小博主动来信：晨间问候 / 错题间隔重复复习提醒 / 生日祝福。

通用聊天机器人从不主动开口——这是"伙伴"和"工具"的分水岭。
状态存 ~/.lebotclaw/proactive_state.json（每天同类消息最多发一次）。
飞书等外部推送复用 pending_messages() 即可（凭证待填，先网页内来信）。
"""
import json
import re
import time
from datetime import datetime
from pathlib import Path

from lebotclaw.tools.builtin.store import JsonListStore

_STATE_FILE = Path.home() / ".lebotclaw" / "proactive_state.json"

# 错题记录后第 N 天提醒复习（间隔重复）
REVIEW_WINDOWS = (1, 3, 7, 15)


def _load_state() -> dict:
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: dict):
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _greeting(now: datetime, name: str) -> str:
    h = now.hour
    who = f"{name}，" if name else ""
    if 5 <= h < 12:
        return f"早安{who}☀️ 新的一天！大脑先热个身不？你之前聊到哪儿我可都记着呢～"
    if 12 <= h < 18:
        return f"下午好呀{who}🌤 放学了没？今天学校有啥好玩的事，跟我唠唠？"
    if 18 <= h < 23:
        return f"晚上好{who}🌙 今天的学习任务搞定没？需要我搭把手随时说～"
    return f"这么晚还来找我呀{who}🌟 是有心事，还是作业卡住了？"


def _is_birthday(birthday: str, now: datetime) -> bool:
    m = re.search(r"(\d{1,2})\s*[月/-]\s*(\d{1,2})", birthday or "")
    return bool(m) and (now.month, now.day) == (int(m.group(1)), int(m.group(2)))


def pending_messages(memory, consume: bool = False) -> list:
    """待推送的主动消息。consume=True 时落状态（同类消息当天不再重复）。"""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    state = _load_state()
    msgs = []

    profile = memory.get_student_profile()
    name = profile.get("名字", "")

    # 1) 生日祝福（一年一次）
    if _is_birthday(profile.get("生日", ""), now) and state.get("birthday_year") != now.year:
        msgs.append({
            "kind": "birthday",
            "text": f"🎂 {name + '，' if name else ''}生日快乐！！今天可是你的大日子，我早就在日历上圈好了～"
                    "愿望想好了没？学习上今年也有我陪着，咱们一起变得更厉害！💪",
        })
        state["birthday_year"] = now.year

    # 2) 每日首次问候
    if state.get("last_greet") != today:
        msgs.append({"kind": "greet", "text": _greeting(now, name)})
        state["last_greet"] = today

    # 3) 错题间隔重复提醒
    reminded = state.setdefault("reminded", {})
    due = []
    for it in JsonListStore("~/.lebotclaw/mistakes.json").all():
        if it.get("mastered"):
            continue
        created = datetime.fromtimestamp(it.get("created_at", time.time()))
        days = (now.date() - created.date()).days
        if days in REVIEW_WINDOWS and reminded.get(str(it.get("id"))) != today:
            due.append(it)
            reminded[str(it["id"])] = today
    if due:
        q = due[0].get("question", "")[:20]
        msgs.append({
            "kind": "review",
            "text": f"复习闹钟⏰ {name + '，' if name else ''}之前错的那道「{q}」，"
                    "脑子里的印象开始变淡啦——趁现在复习最划算！"
                    "要不要我给你出几道长得像的题，测测是不是真会了？",
            "mistake_ids": [i["id"] for i in due],
        })

    if consume:
        _save_state(state)
    return msgs
