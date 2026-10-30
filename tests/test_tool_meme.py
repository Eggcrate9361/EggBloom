# -*- coding: utf-8 -*-
"""表情包找回/发送（harness/tool_meme.py，2026-10-30）。

用户要的：「把你偷的那张图再发一遍」不能又随机配一张。

钉死：
  1. meme_find 按 tag/描述/文件名都能找到，返回 id
  2. meme_send 只发图库内的文件（目录穿越被拒）
  3. 没有对话上下文 / 没接线 → 如实失败
  4. 发送成功回 ok + sent

运行：python tests/test_tool_meme.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="toolmeme_test_")
from harness import tool_meme as tm  # noqa: E402
from harness import tool_task as tt  # noqa: E402

tm.MEME_ROOT = TMP
tm.INDEX_FILE = os.path.join(TMP, "collected", "index.json")
tm.DESC_FILE = os.path.join(TMP, "descriptions.json")

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


try:
    os.makedirs(os.path.join(TMP, "collected"), exist_ok=True)
    img1 = os.path.join(TMP, "collected", "aaa111.gif")
    img2 = os.path.join(TMP, "collected", "bbb222.jpg")
    open(img1, "wb").write(b"GIF89a-1")
    open(img2, "wb").write(b"\xff\xd8-2")
    json.dump({
        "sha1": {"path": "collected/aaa111.gif", "tags": ["得意"], "ts": 100},
        "sha2": {"path": "collected/bbb222.jpg", "tags": ["尴尬"], "ts": 200},
    }, open(tm.INDEX_FILE, "w", encoding="utf-8"))
    json.dump({"collected/aaa111.gif": {"desc": "蓝发少女竖大拇指", "ts": 1}},
              open(tm.DESC_FILE, "w", encoding="utf-8"))

    r = tm.meme_find("得意")
    check("按标签找到", r["count"] == 1 and r["items"][0]["id"] == "collected/aaa111.gif", r)
    r2 = tm.meme_find("大拇指")
    check("按画面描述找到", r2["count"] == 1, r2)
    r3 = tm.meme_find("")
    check("空查询按时间倒序", r3["items"][0]["id"] == "collected/bbb222.jpg", r3)
    check("找不到返回空", tm.meme_find("不存在的东西")["count"] == 0)

    # 发送：没接线
    tm.set_sender(None)
    tt.set_context(sender="10001", is_group=False, group_id="")
    check("没接线如实失败", tm.meme_send("collected/aaa111.gif")["ok"] is False)

    sent = []
    tm.set_sender(lambda p, c: sent.append((os.path.basename(p), c.get("sender"))) or True)
    ok = tm.meme_send("collected/aaa111.gif")
    check("发送成功", ok.get("ok") is True, ok)
    check("发的是同一张", sent and sent[0][0] == "aaa111.gif", sent)
    check("带上了对话上下文", sent and sent[0][1] == "10001", sent)

    # 目录穿越
    bad = tm.meme_send("../../../bot_config.json")
    check("目录穿越被拒", bad["ok"] is False, bad)
    check("不存在的 id 被拒", tm.meme_send("collected/nope.gif")["ok"] is False)

    # 没有上下文
    tt.set_context(sender="", is_group=False, group_id="")
    check("没上下文拒发", tm.meme_send("collected/aaa111.gif")["ok"] is False)

    # 注册
    fns = tm.register_all()
    check("注册返回三个函数", set(fns) == {"meme_find", "meme_send", "meme_save"}, fns)
    from harness import tool_registry as tr
    check("meme_find 归 work 组", "meme_find" in tr.names_in(tr.GROUP_WORK))
    check("meme_send 归 work 组", "meme_send" in tr.names_in(tr.GROUP_WORK))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
