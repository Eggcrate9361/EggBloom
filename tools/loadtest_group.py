# -*- coding: utf-8 -*-
"""200 人大群 / 8 人并发高频压测（1.5.9 发布前）

## 为什么不直接对着群发消息

今晚（2026-10-28）探测群号时踩到一个真事：随手选的 `900000001`
在日志里被渲染成「宝仕达运动器材有限公司」——它撞上了一个**真实存在的群名**。
如果 Bot 恰好在那个群里，压测消息就会发到真人面前。

所以本脚本有**硬保护**：开跑前查 `get_group_list`，
只要目标群在真实群列表里，立刻退出，不发送任何东西。
**压测只能打不存在的群号。**

## 为什么走 3005 而不是 OneBot API

3005 是协议层的**事件入口**（`bot_core.EventHandler.do_POST`）：
未匹配路由的 POST body 会被原样塞进 `MSG_QUEUE`，由主循环处理。
这条路径与真实消息**完全同构**——同一套去重、缓冲、决策、生成、发送。
用 OneBot 的 `send_group_msg` 只是"发出去"，测不到处理链。

## 用法

    python tools/loadtest_group.py --group 777000111 --check-only
    python tools/loadtest_group.py --group 777000111 --duration 180
    python tools/loadtest_group.py --group 777000111 --talkers 8 --rate 1.0

参数
    --members    模拟成员数（默认 200，只用于 id/昵称分布）
    --talkers    并发发言人数（默认 8）
    --rate       每人每秒消息数（默认 1.0 → 8 msg/s）
    --duration   持续秒数（默认 180）
    --check-only 只做安全检查，不发消息
"""
import argparse
import json
import random
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request

ONEBOT = "http://127.0.0.1:3000"
EVENT = "http://127.0.0.1:3005/"

# 真实感语料：长短混合，含问句 / 陈述 / 表情 / @
LINES = [
    "今天几点集合", "有人打排位吗", "这图谁做的", "笑死我了",
    "明天早八我死了", "刚下课", "谁在图书馆", "食堂还有饭吗",
    "这个版本的平衡性真离谱", "我抽到限定了我超", "别说了，我保底了",
    "你们在聊啥", "刚刚那条消息没看懂", "有人会修电脑吗",
    "论文写不完了救命", "这周有谁回家", "地铁又晚点",
    "推荐个耳机呗", "预算五百以内", "我投了三个岗位都没回",
    "后天体测", "跑一千米要命", "我先去洗澡", "等会儿回来",
    "这游戏新手友好吗", "有人一起开黑吗",
]

# 长度分布：短句为主（贴近真实群聊），偶尔长句
LONG_LINES = [
    "我说真的，这个功能我昨天试了一晚上，怎么调都不对，最后发现是配置文件里多了一个空格，气死我了",
    "刚看完那部电影，节奏挺慢的但是结尾我还挺喜欢，就是中间有一段我差点睡着",
]


def _api(action, params=None, timeout=6):
    body = json.dumps(params or {}).encode()
    req = urllib.request.Request(f"{ONEBOT}/{action}", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"error": str(e)}


def real_group_ids():
    r = _api("get_group_list")
    data = r.get("data") or []
    return {str(g.get("group_id")) for g in data if isinstance(g, dict)}


def safety_check(group):
    """目标群必须是**不存在的**。返回 (ok, reason)。"""
    real = real_group_ids()
    if not real:
        return False, "拿不到真实群列表（协议层不可用？）→ 无法确认安全性，拒绝运行"
    if str(group) in real:
        return False, f"群 {group} 是**真实加入的群**，拒绝压测（会把消息发到真人面前）"
    return True, f"OK：{group} 不在 {len(real)} 个真实群中"


def make_member(i):
    return {"user_id": 310000000 + i, "nick": f"压测{i:03d}"}


def build_text():
    r = random.random()
    if r < 0.06:
        return random.choice(LONG_LINES)
    line = random.choice(LINES)
    if r < 0.10:
        line = "@AAA无理由退货 " + line      # 模拟 @（会走进"被点名"分支）
    return line


def send_event(sess, group, member, text, mid, kind="text"):
    """一次 POST。返回 (ok, 耗时秒, 错误串)。"""
    t0 = time.time()
    if kind == "text":
        msg = [{"type": "text", "data": {"text": text}}]
        post_type = "message"
    elif kind == "image":
        msg = [{"type": "image", "data": {"url": "C:/nonexistent/loadtest.png"}},
               {"type": "text", "data": {"text": text}}]
        post_type = "message"
    else:                                    # poke
        msg = []
        post_type = "notice"
    if post_type == "notice":
        ev = {"post_type": "notice", "notice_type": "notify", "sub_type": "poke",
              "group_id": str(group), "user_id": str(member["user_id"]),
              "time": int(time.time())}
    else:
        ev = {"post_type": "message", "message_type": "group",
              "group_id": str(group), "user_id": str(member["user_id"]),
              "time": int(time.time()), "message_id": mid,
              "sender": {"user_id": member["user_id"],
                         "nickname": member["nick"], "card": member["nick"]},
              "message": msg}
    try:
        req = urllib.request.Request(EVENT, data=json.dumps(ev).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            r.read()
        return True, time.time() - t0, ""
    except Exception as e:
        return False, time.time() - t0, f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="777000111")
    ap.add_argument("--members", type=int, default=200)
    ap.add_argument("--talkers", type=int, default=8)
    ap.add_argument("--rate", type=float, default=1.0, help="每人每秒条数")
    ap.add_argument("--duration", type=int, default=180)
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--out", default=r"D:\workbuddy_tmp\loadtest_result.json")
    args = ap.parse_args()

    ok, why = safety_check(args.group)
    print(f"[安全检查] {why}")
    if not ok:
        sys.exit(2)
    if args.check_only:
        return

    members = [make_member(i) for i in range(1, args.members + 1)]
    talkers = members[:args.talkers]
    lock = threading.Lock()
    stats = {"sent": 0, "fail": 0, "lat": [], "errors": []}
    stop = threading.Event()
    counter = {"mid": int(time.time()) * 1000}

    def worker(m):
        interval = 1.0 / max(args.rate, 0.01)
        next_at = time.time()
        while not stop.is_set():
            now = time.time()
            if now < next_at:
                time.sleep(min(0.05, next_at - now))
                continue
            next_at += interval * random.uniform(0.8, 1.2)
            with lock:
                counter["mid"] += 1
                mid = counter["mid"]
            r = random.random()
            kind = "poke" if r < 0.02 else ("image" if r < 0.08 else "text")
            good, cost, err = send_event(None, args.group, m, build_text(),
                                         mid, kind)
            with lock:
                if good:
                    stats["sent"] += 1
                    stats["lat"].append(cost)
                else:
                    stats["fail"] += 1
                    if len(stats["errors"]) < 10:
                        stats["errors"].append(err)

    threads = [threading.Thread(target=worker, args=(m,), daemon=True)
               for m in talkers]
    t0 = time.time()
    for t in threads:
        t.start()
    try:
        while time.time() - t0 < args.duration:
            time.sleep(1)
            if int(time.time() - t0) % 15 == 0:
                with lock:
                    print(f"  +{int(time.time()-t0):3d}s 已投递 {stats['sent']} "
                          f"失败 {stats['fail']}")
    except KeyboardInterrupt:
        print("中断")
    stop.set()
    for t in threads:
        t.join(timeout=3)

    lat = stats["lat"]
    res = {
        "group": args.group, "members": args.members, "talkers": args.talkers,
        "rate_per_talker": args.rate, "planned_sec": args.duration,
        "actual_sec": round(time.time() - t0, 1),
        "sent": stats["sent"], "fail": stats["fail"],
        "msg_per_sec": round(stats["sent"] / max(time.time() - t0, 0.1), 2),
        "latency_ms": {
            "p50": round(statistics.median(lat) * 1000, 1) if lat else None,
            "p95": round(sorted(lat)[int(len(lat) * 0.95)] * 1000, 1) if lat else None,
            "max": round(max(lat) * 1000, 1) if lat else None,
        },
        "errors": stats["errors"],
    }
    print(json.dumps(res, ensure_ascii=False, indent=2))
    try:
        open(args.out, "w", encoding="utf-8").write(
            json.dumps(res, ensure_ascii=False, indent=2))
    except Exception:
        pass


if __name__ == "__main__":
    main()