"""WorkspaceFiles（SOUL/MEMORY/companion）测试 — spec FR-E6 / FR-E7。"""
import json
import os
import stat

from lebotclaw.core.workspace import WorkspaceFiles, SOUL_MASTER


def _ws(tmp_path, uid="kid1"):
    return WorkspaceFiles(base_dir=str(tmp_path / "users"), uid=uid)


def test_soul_created_from_master(tmp_path):
    ws = _ws(tmp_path)
    assert ws.soul_path.exists()
    assert ws.soul_path.read_text(encoding="utf-8") == SOUL_MASTER


def test_soul_restored_when_deleted(tmp_path):
    ws = _ws(tmp_path)
    os.chmod(ws.soul_path, stat.S_IRUSR | stat.S_IWUSR)  # 解除只读再删
    ws.soul_path.unlink()
    assert ws.ensure_soul() is True
    assert ws.soul_path.read_text(encoding="utf-8") == SOUL_MASTER


def test_soul_restored_when_tampered(tmp_path):
    ws = _ws(tmp_path)
    os.chmod(ws.soul_path, stat.S_IRUSR | stat.S_IWUSR)
    ws.soul_path.write_text("被篡改的人格", encoding="utf-8")
    assert ws.ensure_soul() is True
    assert ws.soul_path.read_text(encoding="utf-8") == SOUL_MASTER


def test_soul_readonly_permissions(tmp_path):
    ws = _ws(tmp_path)
    mode = stat.S_IMODE(os.stat(ws.soul_path).st_mode)
    assert not (mode & stat.S_IWUSR)


def test_memory_append_and_read(tmp_path):
    ws = _ws(tmp_path)
    n = ws.append_memory(["他怕狗", "约定每天背 5 个单词"])
    assert n == 2
    content = ws.read_memory()
    assert "他怕狗" in content and "每天背 5 个单词" in content


def test_memory_append_dedup_and_len_cap(tmp_path):
    ws = _ws(tmp_path)
    ws.append_memory(["他怕狗"])
    assert ws.append_memory(["他怕狗"]) == 0  # 去重
    long_entry = "很长" * 100
    ws.append_memory([long_entry])
    for line in ws.memory_path.read_text(encoding="utf-8").splitlines():
        if "很长" in line:
            # "- [日期] " 前缀 + ≤80 字
            assert len(line) <= 80 + 15


def test_memory_human_edit_visible(tmp_path):
    ws = _ws(tmp_path)
    ws.memory_path.write_text("# MEMORY\n\n- 家长手写：他对青霉素过敏\n", encoding="utf-8")
    assert "青霉素过敏" in ws.read_memory()


def test_memory_truncates_long_file(tmp_path):
    ws = _ws(tmp_path)
    lines = [f"- 第{i}条记忆" for i in range(300)]
    ws.memory_path.write_text("\n".join(lines), encoding="utf-8")
    content = ws.read_memory()
    assert "中段省略" in content
    assert "第299条记忆" in content
    assert "第0条记忆" in content  # 头部保留


def test_companion_first_touch(tmp_path):
    ws = _ws(tmp_path)
    stats = ws.touch_companion()
    assert stats["days"] == 1
    assert stats["total_tokens"] == 0


def test_companion_add_tokens_and_milestone(tmp_path):
    ws = _ws(tmp_path)
    ws.touch_companion()
    assert ws.add_tokens(500) is None
    ev = ws.add_tokens(9600)  # 跨过 1 万
    assert ev == {"kind": "tokens", "value": 10_000}
    assert ws.companion_stats()["total_tokens"] == 10100
    # 不重复触发
    assert ws.add_tokens(10) is None


def test_companion_day_milestone(tmp_path):
    ws = _ws(tmp_path)
    ws.touch_companion()
    assert ws.check_day_milestone() is None  # 第 1 天不是里程碑
    # 伪造 first_seen 为 30 天前
    import time as _t
    past = _t.strftime("%Y-%m-%d", _t.localtime(_t.time() - 29 * 86400))
    data = json.loads(ws.companion_path.read_text(encoding="utf-8"))
    data["first_seen"] = past
    ws.companion_path.write_text(json.dumps(data), encoding="utf-8")
    ev = ws.check_day_milestone()
    assert ev == {"kind": "days", "value": 30}


def test_multi_user_isolation(tmp_path):
    a = _ws(tmp_path, uid="kid_a")
    b = _ws(tmp_path, uid="kid_b")
    a.append_memory(["a 的秘密"])
    assert "a 的秘密" in a.read_memory()
    assert b.read_memory() == ""
