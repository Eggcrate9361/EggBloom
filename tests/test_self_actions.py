# -*- coding: utf-8 -*-
"""行为账本（self_actions.py，2026-10-30）。

用户要的"行为知晓"：它知道自己干过什么（发图/戳人/采集），下一轮能看到。

钉死：
  1. 记账按场景/对象/时间过滤
  2. 渲染成【你刚做过的事】，没有动作时返回空串（零 token）
  3. 落盘后重载还在
  4. 只记事实，不评价

运行：python tests/test_self_actions.py
"""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="acts_test_")
import self_actions as sa  # noqa: E402

sa.DATA_DIR = TMP
sa.LOG_FILE = os.path.join(TMP, "self_actions.jsonl")

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


try:
    now = time.time()
    sa.record("meme", scene="group", target="777", detail="得意", ts=now - 60)
    sa.record("collect", scene="group", target="777", detail="尴尬", ts=now - 30)
    sa.record("poke", scene="private", target="10001", ts=now - 10)
    sa.record("meme", scene="group", target="777", detail="开心", ts=now - 40 * 60)  # 超时

    r = sa.recent(minutes=15, now=now)
    kinds = [x["kind"] for x in r]
    check("取到最近动作", "meme" in kinds and "collect" in kinds and "poke" in kinds, kinds)
    check("超时的不取", len(r) == 3, len(r))

    rg = sa.recent(scene="group", minutes=15, now=now)
    check("按场景过滤", all(x["scene"] == "group" for x in rg), rg)
    rp = sa.recent(scene="private", minutes=15, now=now)
    check("私聊只看到私聊", [x["kind"] for x in rp] == ["poke"], rp)

    rt = sa.recent(target="10001", minutes=15, now=now)
    check("按对象过滤", [x["kind"] for x in rt] == ["poke"], rt)

    txt = sa.render(scene="group", target="777", minutes=15, now=now)
    check("渲染有标题", "【你刚做过的事】" in txt, txt)
    check("渲染带动作", "表情包" in txt and "表情库" in txt, txt)
    check("不评价（没有'偷'字）", "偷" not in txt, txt)

    check("没有动作 → 空串",
          sa.render(scene="group", target="999", minutes=5, now=now) == "")

    # 落盘重载
    sa._MEM = None
    r2 = sa.recent(minutes=15, now=now + 1)
    check("重载后还在", len(r2) == 3, len(r2))

    # 只保留事实字段
    rec = sa.record("image", scene="private", target="10001", detail="发了一张图", ts=now)
    check("记录字段齐全", set(rec) >= {"ts", "kind", "scene", "target", "detail"}, rec)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
