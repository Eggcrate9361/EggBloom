# -*- coding: utf-8 -*-
"""决策器 `observe` 必须被每一个消费者认（2026-10-25 新增）。

线上事故（群 829854136，11:46）：

    [11:47:11] [调试] interventions=[('group_chat_decider',
                    {'action': 'observe', 'after': 25, 'reason': '[Decider v3] …'}),
                 ('ego', {'action': 'inject', …})]
    [11:47:11] 群[…] [插件注入]               ← ego 那份被照常执行
    [11:47:11] 群[…] [近况注入] 6条
    [11:47:26] 搜索「QQ群机器人 #sl 指令 什么意思」
    [11:47:29] 群[…] [插件覆盖] ego: #sl是啥指令

决策器明确说"25 秒后再看"，宿主却当它没说过，照常注入、搜索、回复。

根因不是逻辑写错，而是**同一个 action 有两处消费者**：
  · `process_buffered_message` 里"无触发预决策"那段（10037 附近）只认 observe
  · 同一个函数后半段的插件裁决循环（10293 附近）只认 skip/inject/override
消息走哪条路取决于 `in_session` / 是否 @ / 是否有引用 —— `#sl` 那条恰好
是 `in_session=True`，于是走了后半段，observe 被静默丢弃。

这个文件锁三件事：

  [1] 后半段的裁决循环里必须出现 `observe` 分支
  [2] 观察到点后重看时必须带 observe_retry（否则无限观察）
  [3] observe 的裁决次序：skip / override 优先，observe 优先于 inject

运行：python tests/test_decider_observe.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def main():
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()

    # 定位"插件裁决循环"。全文有两处 `for plugin_name, result in intervention:`：
    #   ① 裁决循环（读 action，决定 skip/inject/override/observe）
    #   ② 温度消费循环（只读 result["temperature"]，不参与裁决）
    # 判据只能是"读不读 action"，按循环出现次数写会误判成 2 处。
    _all = [m.start() for m in re.finditer(
        r"for plugin_name, result in intervention:", src)]
    _loops = []
    for _p in _all:
        _chunk = src[_p:_p + 400]
        if 'result.get("action")' in _chunk:
            _loops.append(_p)
    check("找到插件裁决循环（只应有一处）", len(_loops) == 1,
          f"共 {len(_all)} 处遍历，其中读 action 的 {len(_loops)} 处")
    if not _loops:
        return 1
    # 窗口到「会话内未指向 Bot」守卫为止 —— 它是裁决循环所在函数段的收尾。
    # 固定 3000 字窗口会被循环内新增的注释挤爆（2026-10-26 加去重分支时踩到）。
    _guard = src.find("\n        if not plugin_intervened", _loops[0])
    body = src[_loops[0]:_guard if _guard > 0 else _loops[0] + 4000]

    print("[1] 裁决循环认 observe")
    check('裁决循环里有 action == "observe" 分支',
          'action == "observe"' in body, "缺这条 → observe 被静默丢弃")
    check("observe 读取 after 秒数", 'result.get("after")' in body)
    check("observe 读取 reason（日志要能看出为什么等）",
          'result.get("reason")' in body)

    print("\n[2] observe 真的会中断本轮")
    # 关键：读到了却不用，等于没修。必须能看到 _schedule_observe + return。
    check("裁决循环里会排观察窗口", "_schedule_observe(" in body, "")
    seg = body[body.find("_obs_after > 0"):][:600] if "_obs_after > 0" in body else ""
    check("排完观察后 return（不继续注入/回复）", "return" in seg, seg[:160])
    check("已有观察窗口时不重复排", "in GROUP_OBSERVE" in seg, seg[:160])

    print("\n[3] 裁决次序正确")
    # skip/override 优先于 observe：决策器让等，但另一个插件已经给出成品回复，
    # 这时应该发那条回复而不是干等。
    check("observe 分支用 not plugin_skip 把关", "not plugin_skip" in seg, seg[:200])
    check("observe 分支用 not plugin_reply 把关", "not plugin_reply" in seg, seg[:200])

    print("\n[4] 观察重看不带 retry 会无限循环")
    # 决策插件 1799 行把 observe_retry 编进缓存键；宿主不漏传，第二次决策
    # 才会返回 skip。漏传 = 缓存命中第一次的 observe = 排一个又一个观察。
    ctx_start = src.find('"in_session": buffer.get("in_session", False),')
    ctx = src[ctx_start:ctx_start + 1200] if ctx_start > 0 else ""
    check("第二处 before_reply 的 ctx 传了 observe_retry",
          '"observe_retry"' in ctx, "漏传 → 观察窗口重看时又排观察")
    # 第一处（无触发预决策）本来就有，确认没被改掉
    check("两处 ctx 都传了 observe_retry",
          src.count('"observe_retry"') >= 2, src.count('"observe_retry"'))

    print("\n[5] 两处消费者都在（防止后人删掉一处）")
    check("预决策处消费 observe", "action\") == \"observe\"" in src
          or '== "observe"' in src, "")
    check("_schedule_observe 定义存在", "def _schedule_observe(" in src)
    check("_observe_second_look 定义存在", "def _observe_second_look(" in src)

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())