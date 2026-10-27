# -*- coding: utf-8 -*-
"""私聊消息合并（2026-10-25 新增）。

用户报：「私聊两条相近时间发送的消息没有被统一处理，而是一句一句分开回复了」。

日志实证（13:12）：
    13:12:30 收到: 没有养哦     → 当场进 AI 流程
    13:12:34 收到(第二条)      ← 第一条还在跑，只能在 MSG_QUEUE 里排队
    13:12:51 AI 回复（21 秒）
    13:12:57 才开始发

群聊一直有 `GROUP_MSG_BUFFER` + Timer 合并，私聊从来没有 —— 收到一条就立刻
进 AI。人不这么说话：想到一句发一句，中间隔几秒是常态。

修法：入口先攒进 `PRIVATE_MSG_BUFFER`，Timer 到点把整批**重新入队**，
到点后走的是原来那条完整链路（记忆/画像/画图/ego/表情包全不动）。

为什么不抽函数：私聊处理段内联在 main() 里 450 行、依赖 5 个循环内局部变量，
抽出去要动 5 处状态交接。重新入队只用改入口一处。

这个文件锁四件事：

  [1] 合并延迟**复用** `_batch_delay`（不另写一套规则，否则必然与群聊分叉）
  [2] 多条真的合并成一条（含跨条数与顺序）
  [3] 重新入队的那条不会被入口去重/缓冲再吞一次（否则表现为"合并了但不回"）
  [4] 机制自己坏掉时退化成旧行为（不能因为合并让私聊整个不回）

运行：python tests/test_private_merge.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402

SID = "_test_priv_merge_"
_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def _clean():
    with bot_core._session_lock:
        rec = bot_core.PRIVATE_MSG_BUFFER.pop(SID, None)
    if rec and rec.get("timer"):
        try:
            rec["timer"].cancel()
        except Exception:
            pass


def main():
    orig_cfg = dict(bot_core._config)
    bot_core._config["group_msg_buffer_window_session"] = 4
    bot_core._config["group_msg_buffer_window"] = 3
    bot_core._config["group_msg_buffer_window_trigger"] = 8

    print("[1] 合并延迟复用 _batch_delay，不另立规则")
    d1 = bot_core._private_batch_delay(first_time=time.time())
    check("返回正数秒", isinstance(d1, (int, float)) and d1 > 0, d1)
    # 与群聊"同一个人接着说话"的档位一致
    dg = bot_core._batch_delay(same_sender=True, in_session=True, triggered=False,
                               has_context=False, has_bare_image=False,
                               first_time=time.time())
    check("★ 与群聊同人档位算出来一致", abs(d1 - dg) < 0.01, f"私聊 {d1} vs 群聊 {dg}")
    # 硬顶：first_time 很久以前 → 剩余被压到 1 秒
    d_cap = bot_core._private_batch_delay(first_time=time.time() - 100)
    check("★ 整批有硬顶（不会无限等）", d_cap <= 1.5, d_cap)
    # 带图至少等 4 秒
    d_img = bot_core._private_batch_delay(first_time=time.time(), has_image=True)
    check("带图时至少等 4 秒（下载+理解要时间）", d_img >= 4, d_img)

    print("\n[2] 多条真的合并成一条")
    _clean()
    bot_core.MSG_QUEUE.queue.clear() if hasattr(bot_core.MSG_QUEUE, "queue") else None
    bot_core._buffer_private_message(SID, "第一句", [], {})
    check("第一条进了缓冲", SID in bot_core.PRIVATE_MSG_BUFFER)
    bot_core._buffer_private_message(SID, "第二句", [], {})
    bot_core._buffer_private_message(SID, "第三句", [], {})
    rec = bot_core.PRIVATE_MSG_BUFFER.get(SID) or {}
    parts = rec.get("parts") or []
    check("★ 三条都攒在同一批里", len(parts) == 3, parts)
    check("顺序保持", parts == ["第一句", "第二句", "第三句"], parts)

    print("\n[3] 到点重新入队：合成一条、带标记、有唯一 id")
    # 只取本次测试造的那条（队列里可能还有别的东西）
    bot_core._private_flush(SID)
    check("缓冲已清空", SID not in bot_core.PRIVATE_MSG_BUFFER)
    queued = []
    while not bot_core.MSG_QUEUE.empty():
        try:
            queued.append(bot_core.MSG_QUEUE.get_nowait())
        except Exception:
            break
    mine = [q for q in queued
            if isinstance(q, dict) and str(q.get("sender", {}).get("user_id", "")) == SID]
    check("★ 入队了一条", len(mine) == 1, len(mine))
    if mine:
        q = mine[0]
        txt = "".join(seg["data"]["text"] for seg in q.get("message", [])
                      if seg.get("type") == "text")
        check("★ 三句合成一条消息（不是三条）", "第一句" in txt and "第三句" in txt, txt)
        check("行内换行分隔（顺序可读）", txt.count("\n") == 2, repr(txt))
        check("带重新入队标记（否则会被再缓冲一次 → 死循环）",
              q.get(bot_core._REQUEUE_FLAG) is True, q.get(bot_core._REQUEUE_FLAG))
        # 无 id 的消息会被时间戳水位线判重吞掉
        check("★ 有唯一 message_id（否则被去重闸吃掉，表现为不回复）",
              bool(q.get("message_id")), q.get("message_id"))
        check("message_id 不是 0", q.get("message_id") != 0, q.get("message_id"))

    print("\n[4] 图片跟进批次（不丢图）")
    _clean()
    bot_core._buffer_private_message(SID, "配图", [os.path.join(ROOT, "a.gif")], {})
    rec = bot_core.PRIVATE_MSG_BUFFER.get(SID) or {}
    check("图片进了缓冲", rec.get("images") == [os.path.join(ROOT, "a.gif")], rec.get("images"))
    bot_core._private_flush(SID)
    queued = []
    while not bot_core.MSG_QUEUE.empty():
        try:
            queued.append(bot_core.MSG_QUEUE.get_nowait())
        except Exception:
            break
    mine = [q for q in queued
            if isinstance(q, dict) and str(q.get("sender", {}).get("user_id", "")) == SID]
    if mine:
        imgs = [s for s in mine[0].get("message", []) if s.get("type") == "image"]
        check("★ 重新入队时图还在", len(imgs) == 1, imgs)
        # extract_images 见 file 字段会先调 onebot_fresh_image 问协议层要新地址，
        # 对本地路径是白跑一次 HTTP；用 url 才直接命中"本地文件"分支
        check("★ 用 url 字段回灌（走本地文件分支，不打网络）",
              bool(imgs and imgs[0]["data"].get("url")), imgs)
        check("没用 file 字段（会触发一次多余的网络查询）",
              not (imgs and imgs[0]["data"].get("file")), imgs)

    print("\n[5] 空批次不产生空消息")
    _clean()
    bot_core._buffer_private_message(SID, "", [], {})
    bot_core._private_flush(SID)
    queued = []
    while not bot_core.MSG_QUEUE.empty():
        try:
            queued.append(bot_core.MSG_QUEUE.get_nowait())
        except Exception:
            break
    mine = [q for q in queued
            if isinstance(q, dict) and str(q.get("sender", {}).get("user_id", "")) == SID]
    check("空批次不入队", not mine, mine)

    print("\n[6] 入口闸的判定（纯函数，直接打）")
    # 这一组是判别力的来源。上一版只 grep 源码里有没有 continue ——
    # 实测把判断改成 `if False and ...`（等价于彻底关掉合并）后，grep 类断言
    # 照样全绿，因为 continue 那行代码还在、只是够不到。判定必须能被直接调用。
    S = bot_core._should_buffer_private
    check("普通文字消息 → 该缓冲", S("没有养哦", [], {}) is True)
    check("★ 带重新入队标记的 → 放行（否则自己拦自己，永远不回）",
          S("第一句\n第二句", [], {bot_core._REQUEUE_FLAG: True}) is False)
    check("空文字无图 → 不占窗口", S("", [], {}) is False)
    check("纯空白 → 不占窗口", S("   ", [], {}) is False)
    check("只有图 → 该缓冲（图和下一条文字可能是一句话）",
          S("", ["/tmp/a.jpg"], {}) is True)
    check("msg 不是 dict → 放行（不能因异常形状卡死）", S("hi", [], None) is False)

    print("\n[7] 主链接线（位置 + 用返回值判定）")
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    i_call = src.find("if _buffer_private_message(sender, text, images, msg):")
    i_save = src.rfind('save_chat_log(sender, "对方", text)', 0, i_call)
    i_ai = src.find("进入AI聊天流程", i_call)
    check("入口用返回值决定 continue（不是无条件拦）", i_call > 0)
    check("★ 在消息转存之后（每条都落日志/落记忆，合并不吞记忆）",
          0 < i_save < i_call, f"save={i_save} call={i_call}")
    check("★ 在 AI 流程之前（真正的省，不是生成完再丢）",
          0 < i_call < i_ai, f"call={i_call} ai={i_ai}")
    check("拦下后用 continue", "continue" in src[i_call:i_call + 400])
    check("机制异常时退化成旧行为（不 try 就会整个不回）",
          "合并不可用" in src[i_call:i_call + 600])

    print("\n[8] 撤回要能取消未处理的私聊批次（否则会回复已撤回的内容）")
    # 新增缓冲区就多了一个"可被取消的对象"。群聊撤回分支一直在 pop GROUP_MSG_BUFFER，
    # 私聊撤回分支原来只回一句"撤回了？"。不补这一步，用户在 5 秒窗口内撤回时：
    # 缓冲区照旧到点合并照旧回复（回的是已撤回的内容），并且再补一句"我还没看到"。
    i_recall = src.find("elif not is_grp_recall:")
    check("找到私聊撤回分支", i_recall > 0)
    seg = src[i_recall:i_recall + 3000]
    check("★ 撤回分支调用了取消函数", "_cancel_private_buffer" in seg, seg[:200])
    # 真正的实现放在函数里，单独断言函数本身的两步（先取 timer，再 pop）
    i_fn = src.find("def _cancel_private_buffer")
    fn = src[i_fn:i_fn + 1200] if i_fn > 0 else ""
    check("找到取消函数实现", i_fn > 0)
    check("★ 函数里会清掉待处理批次", "PRIVATE_MSG_BUFFER.pop" in fn, fn[:200])
    check("★ 函数里取消定时器（不清 timer 会到点照样合并）", "cancel()" in fn, "")
    # key 必须与缓冲区一致：缓冲区按真实 QQ 号做 key，NT 推的是 u_xxx
    check("★ u_xxx 归一化成 QQ（否则永远 pop 不到，这一步等于空转）",
          'startswith("u_")' in seg, seg[:300])
    # 判定本身要能被直接打。上面几条只是 grep，真正的判别力在这一段：
    # 走真正的「缓冲 → 撤回 → flush」顺序，看会不会冒出一条回复。
    C = bot_core._cancel_private_buffer
    _clean()
    bot_core._buffer_private_message(SID, "第一句", [], {})
    bot_core._buffer_private_message(SID, "第二句", [], {})
    check("★ 撤回时有东西可取消（返回条数 2）", C(SID) == 2, C(SID))
    check("★ 撤回后缓冲里没有这条", SID not in bot_core.PRIVATE_MSG_BUFFER)
    bot_core._private_flush(SID)
    queued = []
    while not bot_core.MSG_QUEUE.empty():
        try:
            queued.append(bot_core.MSG_QUEUE.get_nowait())
        except Exception:
            break
    mine = [q for q in queued
            if isinstance(q, dict) and str(q.get("sender", {}).get("user_id", "")) == SID]
    check("★ 撤回后不会凭空冒出一条回复", not mine, mine)
    # 已经 pop 掉的那个 timer 不能再把消息补进来（cancel 真的生效）
    time.sleep(0.3)
    queued = []
    while not bot_core.MSG_QUEUE.empty():
        try:
            queued.append(bot_core.MSG_QUEUE.get_nowait())
        except Exception:
            break
    mine = [q for q in queued
            if isinstance(q, dict) and str(q.get("sender", {}).get("user_id", "")) == SID]
    check("★ 被取消的定时器不会事后补发", not mine, mine)
    check("没有待处理批次时重复撤回不报错（返回 0）", C(SID) == 0, C(SID))

    print("\n[9] 重新入队的那条不重复转存（否则同一句话进两次记忆）")
    # 原文每条在入缓冲前已经各自 save_chat_log + memory_feed 过一次；
    # 合并后整批再走一遍转存，就等于记忆里既有原句、又有一条拼接版。
    i_save = src.rfind('save_chat_log(sender, "对方", text)', 0, i_call)
    guard = src[max(0, i_save - 900):i_save + 200]
    check("★ 转存被重新入队标记挡住", "_REQUEUE_FLAG" in guard, guard[-300:])
    check("转存仍在合并点之前（先存再缓冲，撤回不丢记录）", 0 < i_save < i_call, "")

    print("\n[10] 重新入队标记必须穿过主循环的重新打包（2026-10-27 真机）")
    # 主循环把队列事件重新打包成 event_msgs 时只挑固定字段。这里漏了标记，
    # 重新入队的那条就又变成"新消息"，被 13419 再拦一次 → 永远等不到回复。
    # 现象：私聊每条都「入缓冲 等 5.0s（本批 1 条）」，日志里连「收到:」都没有。
    check("★ event_msgs 保留 _REQUEUE_FLAG",
          "_REQUEUE_FLAG: bool(ev.get(_REQUEUE_FLAG))" in src,
          "主循环只挑固定字段 → 标记丢失 → 又被拦进缓冲 → 永不出队")
    # 第 9 节的「转存被挡住」也依赖同一个标记，两处必须都带着它
    check("入队与重新打包两处都用同一个常量", src.count("_REQUEUE_FLAG") >= 4,
          src.count("_REQUEUE_FLAG"))

    _clean()
    bot_core._config.clear()
    bot_core._config.update(orig_cfg)

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())