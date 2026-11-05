# -*- coding: utf-8 -*-
"""tests/test_bubble_cap.py —— 私聊气泡条数闸（2026-11-05）

真机事故：19:42–19:44 用户发了 4 条消息，bot 实际发出 10 条。
逐条核对日志后定位到两条各自独立的「拆条发送」路径，谁都没有条数闸：

  ① `smart_send()`（私聊 AI 回复）
     按句切完就 for 循环逐条 `_ob_send_text`，一轮 3 句 = 连弹 3 条。
  ② `proactive_talk._send_bubbles()`（主动开口）
     `max_bubbles` 默认 4，每句之间还等 0.8–4 秒打字延时。
     19:40:03/07/13 三条「老大 / 礼物这事… / 要不你许个愿…」就是它发的，
     而 `[Plugin:proactive_talk] 主动开口` 那行日志要等**全部发完**才打印，
     所以日志上看像"先发送、后记日志"。

修法是只并条、不删字：超出上限的句子按原顺序并进末条，标点一个不动。
总闸 `private_bubble_max` 落在主配置，主动开口取它与 `max_bubbles` 的较小值。

判据：
  [A] 3 句 + cap=2 → 2 条，且第 2 条把后两句原样并起来
  [B] cap=0 → 回到旧行为，3 句 3 条
  [C] cap=1 → 1 条，全文一字不丢
  [D] 2 句 + cap=2 → 不并条（不误伤正常两句）
  [E] 单句 → 原样一条，不进并条逻辑
  [F] 主动开口取小值：主配置 2 / 插件 4 → 2；主配置 0 → 用插件值
  [G] 两处接线都在（源码级），别只改了一边
"""
import os
import re
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core as bc  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def _install(monkeypatch_sent):
    """把两条发送路径都接到 list 上，并关掉打字延迟。"""
    def _fake_ob(text, target, **kw):
        monkeypatch_sent.append(str(text))
        return True

    def _fake_send(text, target=None):
        monkeypatch_sent.append(str(text))
        return True

    bc._ob_send_text = _fake_ob
    bc.send_message = _fake_send


def main():
    sent = []
    old = {k: bc._config.get(k) for k in ("private_bubble_max", "reply_typing_speed")}
    orig_ob, orig_send = bc._ob_send_text, bc.send_message
    try:
        _install(sent)
        bc._config["reply_typing_speed"] = 0      # 不睡「开始敲字」那段
        TEXT3 = "第一句话。第二句话。第三句话。"

        # ── [A] cap=2 ─────────────────────────────────────────
        bc._config["private_bubble_max"] = 2
        sent.clear()
        with mock.patch("time.sleep"):             # 句间 0.3~1.0s 的节奏也跳过
            ok = bc.smart_send(TEXT3, "123456")
        check("[A] 3 句 + cap=2 → 2 条", ok is True and len(sent) == 2, sent)
        check("[A] 第 1 条只含第 1 句", sent and sent[0] == "第一句话。", sent)
        check("[A] 第 2 条是后两句原样并起来",
              len(sent) > 1 and sent[1] == "第二句话。第三句话。", sent)

        # ── [B] cap=0 → 旧行为 ────────────────────────────────
        bc._config["private_bubble_max"] = 0
        sent.clear()
        with mock.patch("time.sleep"):
            bc.smart_send(TEXT3, "123456")
        check("[B] cap=0 → 3 句 3 条（旧行为）", len(sent) == 3, sent)
        check("[B] 句子顺序没变",
              sent == ["第一句话。", "第二句话。", "第三句话。"], sent)

        # ── [C] cap=1 → 全文一条 ──────────────────────────────
        bc._config["private_bubble_max"] = 1
        sent.clear()
        with mock.patch("time.sleep"):
            bc.smart_send(TEXT3, "123456")
        check("[C] cap=1 → 只发 1 条", len(sent) == 1, sent)
        check("[C] 并条后一个字都没丢", sent and sent[0] == TEXT3, sent)

        # ── [D] 2 句不误伤 ────────────────────────────────────
        bc._config["private_bubble_max"] = 2
        sent.clear()
        with mock.patch("time.sleep"):
            bc.smart_send("吃了。面条。", "123456")
        check("[D] 2 句 + cap=2 → 仍是 2 条",
              sent == ["吃了。", "面条。"], sent)

        # ── [E] 单句 ──────────────────────────────────────────
        sent.clear()
        with mock.patch("time.sleep"):
            bc.smart_send("嗯。", "123456")
        check("[E] 单句 → 1 条且是原文", sent == ["嗯。"], sent)

        # ── [F] 主动开口取小值 ────────────────────────────────
        sys.path.insert(0, ROOT)
        import plugins.proactive_talk as pt  # noqa: E402

        bc._config["private_bubble_max"] = 2
        check("[F] 主配置 2 / 插件 4 → 2",
              pt.effective_bubble_cap({"max_bubbles": 4}) == 2,
              pt.effective_bubble_cap({"max_bubbles": 4}))
        check("[F] 插件更小时听插件的（1）",
              pt.effective_bubble_cap({"max_bubbles": 1}) == 1,
              pt.effective_bubble_cap({"max_bubbles": 1}))
        bc._config["private_bubble_max"] = 0
        check("[F] 主配置 0（不限）→ 用插件值 4",
              pt.effective_bubble_cap({"max_bubbles": 4}) == 4,
              pt.effective_bubble_cap({"max_bubbles": 4}))
        check("[F] 脏值不崩（字符串）",
              pt.effective_bubble_cap({"max_bubbles": "两"}) >= 1,
              pt.effective_bubble_cap({"max_bubbles": "两"}))
        check("[F] 默认配置本身已降到 2", pt.DEFAULT_CONFIG.get("max_bubbles") == 2,
              pt.DEFAULT_CONFIG.get("max_bubbles"))

        # ── [G] 接线：两处都要用 effective_bubble_cap ─────────
        with open(os.path.join(ROOT, "plugins", "proactive_talk", "__init__.py"),
                  "r", encoding="utf-8") as f:
            pt_src = f.read()
        check("[G] prompt 的行数上限走总闸",
              "max_bubbles = effective_bubble_cap(cfg)" in pt_src)
        check("[G] 切气泡处走总闸",
              "_clean_bubbles(text, effective_bubble_cap(cfg))" in pt_src)
        with open(os.path.join(ROOT, "bot_core.py"), "r", encoding="utf-8") as f:
            core = f.read()
        body = core.split("def smart_send(")[1].split("\ndef ")[0]
        check("[G] smart_send 读 private_bubble_max",
              'private_bubble_max' in body, body[:200])
        check("[G] 并条不删字（用 join 拼回）",
              re.search(r'"".join\(sentences\[_bubble_cap - 1:\]\)', body) is not None)
        check("[G] 单句分支发的是并条后的句子（不是原文 text）",
              "return send_message(sentences[0], target_qq)" in body)
    finally:
        bc._ob_send_text, bc.send_message = orig_ob, orig_send
        for k, v in old.items():
            if v is None:
                bc._config.pop(k, None)
            else:
                bc._config[k] = v
        # import 这个插件会拉起心跳线程（60~180s 后可能真的去开口）。
        # 测试进程退出时 daemon 会跟着死，但别赌那个时间窗 —— 显式停掉。
        try:
            pt._stop.set()
        except Exception:
            pass

    print(f"\n{'=' * 56}\n  PASS={PASS}  FAIL={FAIL}")
    if FAILURES:
        for f in FAILURES:
            print(f"  ✗ {f}")
    print("=" * 56)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
