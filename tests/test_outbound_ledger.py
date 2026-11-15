# -*- coding: utf-8 -*-
"""出站动作账本（1.6.0 出站动作统一）。

## 契约

1. 账本挂在**已有唯一出口** `harness/outbound.py` 的 worker 上，不新建 send()；
2. 一个 job 只记一条，成功/失败都记，且**不改变** job 的 ok / 返回值；
3. `content_ref` 绝不写入 base64 图片；
4. by=plugin 的主动提问在「之后没有 by=model 出站」期间一直可投影进 prompt，
   不受 30 分钟硬窗影响；主链回复一发出即终止；
5. 落盘路径可隔离，不污染真机数据。

运行：python tests/test_outbound_ledger.py
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="eggledger_")
os.environ["EGGBLOOM_OUTBOUND_LEDGER"] = os.path.join(_TMP, "ledger.jsonl")
os.environ.setdefault("EGGBLOOM_OUTBOX_DB", os.path.join(_TMP, "outbox.sqlite3"))

from harness import outbound_ledger as ol  # noqa: E402
from harness import outbound as ob  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


def ev(**kw):
    e = {"ts": time.time(), "scene": "private", "target": "10001",
         "kind": "text", "action": "文本", "content_ref": "你好",
         "by": "model", "origin": "", "result": "ok", "question": False}
    e.update(kw)
    return e


# ── 1. build_event 纯函数 ────────────────────────────────────────────
job = {"kind": "text", "target": "10001", "is_group": False, "ts": 1000.0,
       "payload": {"text": "你那边现在几点了？", "message": "你那边现在几点了？"},
       "origin": "plugin:proactivity", "ok": True}
e1 = ol.build_event(job)
check("事件字段齐全", set(("ts", "scene", "target", "kind", "action",
                          "content_ref", "by", "result", "question")) <= set(e1), sorted(e1))
check("插件来源记为 by=plugin", e1["by"] == "plugin", e1["by"])
check("私聊场景", e1["scene"] == "private", e1["scene"])
check("提问被标记", e1["question"] is True, e1)
check("★ 无问号的主动提问也能识别",
      ol.build_event(dict(job, payload={
          "text": "刚刷到个东西 想起你了 你那边现在几点了",
          "message": "刚刷到个东西 想起你了 你那边现在几点了"}))
      ["question"] is True)
check("普通陈述不算提问",
      ol.build_event(dict(job, payload={
          "text": "行吧 看来这话题确实有点沉 当我没说",
          "message": "行吧 看来这话题确实有点沉 当我没说"}))
      ["question"] is False)
check("文本动作", e1["action"] == "文本", e1["action"])

job2 = {"kind": "image", "target": "888", "is_group": True, "ok": True,
        "origin": "rule action=表情",
        "payload": {"message": [{"type": "image",
                                 "data": {"file": "base64://iVBORw0KGgoAAAANSUhEUg",
                                          "sub_type": "1"}}]}}
e2 = ol.build_event(job2)
check("群聊场景", e2["scene"] == "group", e2["scene"])
check("规则来源记为 by=rule", e2["by"] == "rule", e2["by"])
check("sub_type=1 归为表情动作", e2["action"] == "表情", e2["action"])
check("★ base64 不进账本", "base64://" not in e2["content_ref"] and "iVBOR" not in e2["content_ref"],
      e2["content_ref"])
check("图片动作默认图片·展示",
      ol._action_of("image", {"message": [{"type": "image", "data": {"file": "x.png"}}]}, "") == "图片·展示")

e3 = ol.build_event({"kind": "text", "target": "1", "ok": False,
                     "payload": {"text": "发送失败的文本"}})
check("失败 job 记 result=failed", e3["result"] == "failed", e3["result"])
check("失败 job 不算提问", e3["question"] is False)


# ── 2. 落盘隔离 + 统一日志行 ─────────────────────────────────────────
ol.reset_memory()
p = ol.ledger_path()
check("账本路径可重定向", p == os.environ["EGGBLOOM_OUTBOUND_LEDGER"], p)
logs = []
r = ol.record_job(job, log_fn=logs.append)
check("record_job 返回事件", isinstance(r, dict) and r["by"] == "plugin")
check("落盘存在", os.path.isfile(p))
check("落盘可读回", any(x.get("by") == "plugin" for x in ol.load_recent()), p)
check("统一日志行格式", logs and logs[0].startswith("[动作:文本]")
      and "by=plugin" in logs[0] and "result=ok" in logs[0], logs)


# ── 3. origin 标记 ───────────────────────────────────────────────────
check("默认 origin 为空", ol.current_origin() == "")
tok = ol.mark("plugin:proactivity")
check("mark 生效", ol.current_origin() == "plugin:proactivity")
ol.reset(tok)
check("reset 生效", ol.current_origin() == "")


# ── 4. 真跑一次队列：一个 job 一条账本 ────────────────────────────────
ol.reset_memory()
ob.reset_stats()
ob.set_config(lambda: {})
ob.set_logger(lambda m: None)
ob.set_sender(lambda kind, target, payload, is_group: {"retcode": 0})
tok = ol.mark("plugin")
try:
    ok = ob.send_text("刚刷到个东西 想起你了 你那边现在几点了", "10001")
finally:
    ol.reset(tok)
check("队列发送成功", ok is True)
check("★ 一个 job 只记一条", len([x for x in ol.recent(target="10001")
                                 if x.get("content_ref", "").startswith("刚刷到")]) == 1,
      ol.recent(target="10001"))
check("★ 队列里来源没丢（跨线程）",
      any(x.get("by") == "plugin" for x in ol.recent(target="10001")),
      ol.recent(target="10001"))


# ── 5. pending_outreach：按「有没有主链回复」判定，不按时间硬窗 ──────
ol.reset_memory()
with ol._LOCK:
    ol._MEM.append(ev(ts=time.time() - 31 * 60, by="plugin", origin="plugin",
                      content_ref="刚刷到个东西 想起你了 你那边现在几点了",
                      question=True))
pend = ol.pending_outreach("private", "10001")
check("★ 超过 30 分钟仍算待接住", pend is not None and pend["by"] == "plugin")
blk = ol.context_block("p_10001")
check("★ 投影块含主动提问原文", "你那边现在几点了" in blk and "别当成新话题" in blk, blk)
check("群 cache_key 不串台", ol.context_block("g_10001") == "")
check("非法 cache_key 返回空", ol.context_block("x_10001") == "")

with ol._LOCK:
    ol._MEM.append(ev(ts=time.time(), by="model", content_ref="12点干嘛？"))
check("★ 主链回复后不再挂起", ol.pending_outreach("private", "10001") is None)
check("主链回复后投影块为空", ol.context_block("p_10001") == "")

ol.reset_memory()
with ol._LOCK:
    ol._MEM.append(ev(ts=time.time() - 20 * 3600, by="plugin", origin="plugin",
                      content_ref="陈年旧问？", question=True))
check("超长兜底（>12h）不再挂起",
      ol.pending_outreach("private", "10001", max_age_hours=12) is None)


# ── 6. 架构约束：不破坏已有唯一出口 ──────────────────────────────────
core = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
led = open(os.path.join(ROOT, "harness", "outbound_ledger.py"), encoding="utf-8").read()
obsrc = open(os.path.join(ROOT, "harness", "outbound.py"), encoding="utf-8").read()
check("账本模块不定义第二个发送出口",
      "def send(" not in led and "api_call(" not in led)
check("★ 账本挂在工作线程 finally 上", "record_job(job" in obsrc and "finally:" in obsrc)
check("★ submit 记录调用链来源", 'job["origin"]' in obsrc)
check("★ 插件出口打了来源标记", '_oled.mark("plugin")' in core)
check("★ prompt 注入了账本投影", "_oled.context_block(cache_key)" in core)
check("唯一 OneBot 出口仍在",
      "def _outbound_low_send(" in core and '"send_group_msg"' in core
      and '"send_private_msg"' in core)
check("主链发送函数计数未减少",
      core.count("_ob_send_text(") >= 3 and core.count("_ob_send_msg(") >= 5,
      (core.count("_ob_send_text("), core.count("_ob_send_msg(")))
# 与 tests/test_outbound.py 同款 AST 判据：docstring 里提到端点不算直连。
import ast  # noqa: E402
_tree = ast.parse(obsrc)
_direct = [n for n in ast.walk(_tree)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
           and n.func.id == "api_call" and n.args
           and isinstance(n.args[0], ast.Constant)
           and str(n.args[0].value) in ("send_group_msg", "send_private_msg")]
check("harness 层无裸 api_call(send_*_msg)", _direct == [], len(_direct))

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
