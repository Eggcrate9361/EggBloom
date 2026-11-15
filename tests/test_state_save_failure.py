# -*- coding: utf-8 -*-
"""P0-3 单测：save_state 不再吞掉写盘失败。

修复前（bot_core.py:2183 前后）：
    with _state_lock:
        try:
            data_store.write_json(STATE_FILE, {...})
        except: pass          ← 裸 except，任何失败都无声无息

后果：状态写盘失败时**没有任何痕迹**，重启后状态莫名其妙丢失，
排查时完全无从下手（用户侧只看到"记忆/历史没了"）。

修复后：记录连续失败次数 + 首次失败与恢复都打日志，
且**仍然不抛异常**（save_state 在主循环里被调用，抛出去会打断消息处理）。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  [OK]   {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


class _FakeStore:
    """替身 data_store：可控成功/失败，且不碰真实磁盘。"""

    def __init__(self):
        self.fail = False
        self.calls = 0

    def write_json(self, path, obj):
        self.calls += 1
        if self.fail:
            raise OSError("磁盘已满（模拟）")
        return True


_saved_store = bot_core.data_store
_fake = _FakeStore()

try:
    print("=== A. 结构性证据：裸 except 已消失 ===")
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    m = re.search(r"def save_state\(.*?\n(?=\ndef |\nclass )", src, re.S)
    check("能定位 save_state 函数体", m is not None)
    body = m.group(0) if m else ""
    # 只看**代码**行：注释里会引用被删掉的旧写法（`except: pass`），不该计入判定
    code_lines = [ln for ln in body.splitlines() if not ln.strip().startswith("#")]
    code_body = "\n".join(code_lines)
    check("★ save_state 内已无裸 `except:`",
          not re.search(r"except\s*:", code_body),
          re.findall(r"except\s*:.{0,20}", code_body))
    check("★ save_state 内有 except Exception as e", "except Exception as e" in code_body)
    check("★ 失败会记日志（含『写盘失败』）", "写盘失败" in body)
    check("★ 恢复时也有日志", "写盘已恢复" in body)
    check("存在观测位 _STATE_SAVE_FAILS", hasattr(bot_core, "_STATE_SAVE_FAILS"))

    print("\n=== B. 失败时：不抛异常 + 计数 + 记录原因 ===")
    bot_core.data_store = _fake
    bot_core._STATE_SAVE_FAILS["n"] = 0
    bot_core._STATE_SAVE_FAILS["last"] = ""
    _fake.fail = True
    _fake.calls = 0

    raised = None
    try:
        bot_core.save_state(force=True)
    except BaseException as e:                        # noqa: BLE001
        raised = e
    check("★ 写盘失败时**不向调用方抛异常**（主循环不受影响）", raised is None, raised)
    check("★ 确实尝试过写盘", _fake.calls == 1, _fake.calls)
    check("★ 失败计数 +1", bot_core._STATE_SAVE_FAILS["n"] == 1, bot_core._STATE_SAVE_FAILS)
    check("★ 记录了失败原因", "磁盘已满" in (bot_core._STATE_SAVE_FAILS.get("last") or ""),
          bot_core._STATE_SAVE_FAILS.get("last"))

    bot_core.save_state(force=True)
    check("连续失败时计数累加", bot_core._STATE_SAVE_FAILS["n"] == 2, bot_core._STATE_SAVE_FAILS["n"])

    print("\n=== C. 恢复时：计数清零 + 不抛异常 ===")
    _fake.fail = False
    _fake.calls = 0
    raised = None
    try:
        bot_core.save_state(force=True)
    except BaseException as e:                        # noqa: BLE001
        raised = e
    check("恢复后不抛异常", raised is None, raised)
    check("★ 恢复后失败计数清零", bot_core._STATE_SAVE_FAILS["n"] == 0,
          bot_core._STATE_SAVE_FAILS["n"])
    check("恢复的原因字段被清空", bot_core._STATE_SAVE_FAILS.get("last") == "")

    print("\n=== D. 原有节流行为未被破坏 ===")
    # 非 force 时：连续调用不足 20 次且距上次写盘 <60s → 应当跳过（不写盘）
    bot_core._state_write_count = 0
    bot_core._state_last_write = __import__("time").time()
    _fake.calls = 0
    for _ in range(5):
        bot_core.save_state()          # 默认非 force
    check("★ 非 force 且未达阈值 → 不写盘（节流保持）", _fake.calls == 0, _fake.calls)
    bot_core.save_state(force=True)
    check("★ force=True 立即写盘（退出/清理路径不受影响）", _fake.calls == 1, _fake.calls)
finally:
    bot_core.data_store = _saved_store
    bot_core._STATE_SAVE_FAILS["n"] = 0
    bot_core._STATE_SAVE_FAILS["last"] = ""

print("\n" + "=" * 62)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
