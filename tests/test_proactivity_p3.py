# -*- coding: utf-8 -*-
"""tests/test_proactivity_p3.py —— ⑤⑥⑦⑧⑨⑫ 六条行为 + 动作限制

前五条（①②③④⑪）在 `test_proactivity_ledger/arbitrator/wiring` 里，
这个文件只钉 2026-11-07 P3 补的六条，避免把老用例改乱。

判据：
  [A] ⑤ 私聊连发（3 条、bot 沉默）→ user_burst
  [B] ⑩ 群聊连发，且期间没人插话 → user_burst；有人插话 → 不触发
  [C] ⑥ 只发图没配字 → image_only；配了字 → 不触发
  [D] ⑦ 叫名字但没 @ → name_dropped；真 @ → 不触发
  [E] ⑧ 断 7 小时后回来 → resume_topic；断 1 小时 → 不触发
  [F] ⑨ job.done 即时候选；⑫ 四种 notice 即时候选
  [G] ⑪ 群活跃度缩放阈值
  [H] 动作限制：群聊不给 poke；任务汇报/状态变更不给 poke；久等类给
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

from harness.proactivity import arbitrator as A  # noqa: E402
from harness.proactivity import ledger as L  # noqa: E402

PASS, FAIL = 0, 0
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


ARGS = {"no_reply_s": 480.0, "at_dropped_s": 180.0, "promise_s": 1200.0,
        "idle_s": 14400.0, "burst_n": 3, "burst_s": 120.0,
        "image_s": 120.0, "named_s": 120.0, "resume_s": 180.0,
        "resume_gap_h": 6.0}


def kinds(state, now, **kw):
    return {o["kind"] for o in L.scan(state, now, **{**ARGS, **kw})}


def main():
    t = 10_000.0
    print("=== A) ⑤ 私聊连发 ===")
    s = None
    for i in range(3):
        s = L.observe_user_message(s, {"sender": "10001", "text": "第%d条" % (i + 1),
                                       "ts": t + i * 10}, t + i * 10)
    check("连发 3 条 → streak=3", s["user_streak"] == 3, s["user_streak"])
    check("刚发完不触发（可能还在打）", "user_burst" not in kinds(s, t + 20), kinds(s, t + 20))
    check("等够 2 分钟 → user_burst", "user_burst" in kinds(s, t + 150), kinds(s, t + 150))
    s2 = L.observe_bot_reply(s, {"scene": "private", "user_id": "10001",
                                 "text": "在", "ts": t + 60, "trace_id": "x"}, t + 60)
    check("bot 回过后不再报连发", "user_burst" not in kinds(s2, t + 150), kinds(s2, t + 150))

    print("\n=== B) ⑩ 群聊连发（有人插话就不算）===")
    g = None
    for i in range(2):
        g = L.observe_user_message(g, {"group_id": "829", "sender": "10001",
                                       "text": "群友%d" % i, "ts": t + i * 5}, t + i * 5)
    check("群聊 2 条就够（need = n-1）", "user_burst" in kinds(g, t + 150), kinds(g, t + 150))
    gb = L.observe_user_message(g, {"group_id": "829", "sender": "10002",
                                    "text": "我插一句", "ts": t + 30}, t + 30)
    check("别人插话后 streak 归 1", gb["user_streak"] == 1, gb["user_streak"])
    check("插话后不报连发", "user_burst" not in kinds(gb, t + 150), kinds(gb, t + 150))

    print("\n=== C) ⑥ 只发图没配字 ===")
    img = L.observe_user_message(None, {"sender": "10001", "text": "",
                                        "has_image": True, "image_count": 2,
                                        "ts": t}, t)
    check("记下只发图", img["image_only_ts"] == t and img["image_only_n"] == 2, img)
    check("超时后 image_only", "image_only" in kinds(img, t + 150), kinds(img, t + 150))
    img2 = L.observe_user_message(None, {"sender": "10001", "text": "看这个",
                                         "has_image": True, "image_count": 1,
                                         "ts": t}, t)
    check("配了字就不算", "image_only" not in kinds(img2, t + 150), kinds(img2, t + 150))

    print("\n=== D) ⑦ 叫名字没 @ ===")
    nm = L.observe_user_message(None, {"sender": "10001", "text": "小a你觉得呢",
                                       "named_bot": True, "at_only": False, "ts": t}, t)
    check("记下叫名字", nm["named_ts"] == t, nm.get("named_ts"))
    check("超时后 name_dropped", "name_dropped" in kinds(nm, t + 150), kinds(nm, t + 150))
    at = L.observe_user_message(None, {"sender": "10001", "text": "小a你觉得呢",
                                       "named_bot": True, "at_only": True,
                                       "at_bot": True, "ts": t}, t)
    check("真 @ 不算 name_dropped", "name_dropped" not in kinds(at, t + 150), kinds(at, t + 150))
    check("真 @ 走 at_dropped", "at_dropped" in kinds(at, t + 300), kinds(at, t + 300))

    print("\n=== E) ⑧ 隔很久回来 ===")
    old = L.observe_user_message(None, {"sender": "10001", "text": "上次说到哪了",
                                        "ts": t}, t)
    back = L.observe_user_message(old, {"sender": "10001", "text": "我回来了",
                                        "ts": t + 7 * 3600}, t + 7 * 3600)
    check("记下断了 7 小时", back["resume_gap_h"] >= 7, back["resume_gap_h"])
    check("超时后 resume_topic", "resume_topic" in kinds(back, t + 7 * 3600 + 200),
          kinds(back, t + 7 * 3600 + 200))
    soon = L.observe_user_message(old, {"sender": "10001", "text": "我又来了",
                                        "ts": t + 3600}, t + 3600)
    check("只断 1 小时不算久别", "resume_topic" not in kinds(soon, t + 3600 + 200),
          kinds(soon, t + 3600 + 200))

    print("\n=== F) ⑨⑫ 即时候选 ===")
    job = L.job_observation({"job_id": "job-ab12", "label": "视频抽帧",
                             "ok": True, "returncode": 0,
                             "tail": "frames=48 duration=6.2s", "ts": t},
                            "10001", t)
    check("job → autonomy_done", job and job["kind"] == "autonomy_done", job)
    check("job 证据带 tail", "48" in str(job["evidence"].get("tail")), job["evidence"])
    check("job 优先级最高", job["priority"] == 65, job["priority"])
    check("没有 target 不产出", L.job_observation({"job_id": "x"}, "", t) is None)
    for ev, name, want in (
        ({"group_id": "829", "name": "新群名", "ts": t}, "notice.group_name", "group_name"),
        ({"group_id": "829", "user_id": "1", "ts": t}, "notice.member_in", "member_in"),
        ({"group_id": "829", "user_id": "1", "kick": True, "ts": t}, "notice.member_out", "member_out"),
        ({"user_id": "10001", "ts": t}, "notice.friend_add", "friend_add"),
    ):
        o = L.notice_observation(ev, name, t)
        check("%s → state_change" % name, o and o["kind"] == "state_change", o)
        check("%s 证据写对子类型" % name, o and o["evidence"]["event"] == want, o)

    print("\n=== G) ⑪ 群活跃度缩放 ===")
    quiet = L.observe_user_message(None, {"group_id": "829", "sender": "10001",
                                          "text": "有人吗", "ts": t}, t)
    check("冷群：4 小时阈值缩到 2.4 小时",
          "idle_contact" in kinds(quiet, t + 9000,
                                  idle_s=14400.0), kinds(quiet, t + 9000))
    hot = None
    for i in range(25):
        hot = L.observe_user_message(hot, {"group_id": "830", "sender": "s%d" % (i % 5),
                                           "text": "热闹", "ts": t + i}, t + i)
    hot_act = float(hot["last_activity_ts"])
    check("热闹群：冷场 4 小时还不够（阈值拉到 6.4 小时）",
          "idle_contact" not in kinds(hot, hot_act + 14400),
          kinds(hot, hot_act + 14400))
    check("热闹群：冷到 6.7 小时才开口",
          "idle_contact" in kinds(hot, hot_act + 24000),
          kinds(hot, hot_act + 24000))

    print("\n=== H) 动作限制 ===")
    check("群聊只给 say/skip", A.allowed_acts("idle_contact", "group") == ("say", "skip"))
    check("私聊久等类给 poke", "poke" in A.allowed_acts("no_reply", "private"))
    check("冷场给 poke", "poke" in A.allowed_acts("idle_contact", "private"))
    check("任务汇报不给 poke", "poke" not in A.allowed_acts("autonomy_done", "private"))
    check("状态变更不给 poke", "poke" not in A.allowed_acts("state_change", "private"))
    check("连发不给 poke", "poke" not in A.allowed_acts("user_burst", "private"))
    check("模型越权选 poke → 改 skip",
          A.parse_model_decision('{"act":"poke","text":""}', scene="private",
                                 allowed=("say", "skip"))["act"] == "skip")
    check("模型选 say 保留",
          A.parse_model_decision('{"act":"say","text":"嗯"}', scene="private",
                                 allowed=("say", "skip"))["act"] == "say")
    check("6 条都注册了",
          all(k in A.BEHAVIORS for k in ("user_burst", "image_only", "name_dropped",
                                         "resume_topic", "autonomy_done", "state_change")),
          sorted(A.BEHAVIORS))

    print("\n=== I) ⑬ 日程 + 产物直发 ===")
    sch = L.schedule_observation({"next_title": "上课", "next_start": "14:00",
                                  "remaining_min": 10, "place": "三教"},
                                 "10001", t)
    check("⑬ 日程 → schedule_near", sch and sch["kind"] == "schedule_near", sch)
    check("⑬ 证据带剩余分钟", sch and sch["evidence"]["in_minutes"] == 10, sch)
    check("⑬ 没有标题不产出",
          L.schedule_observation({"remaining_min": 5}, "1", t) is None)
    check("⑬ 没有收件人不产出",
          L.schedule_observation({"next_title": "上课", "remaining_min": 5}, "", t) is None)
    check("⑬ 优先级 58", sch["priority"] == 58, sch["priority"])

    check("产物 .mp4 给 video",
          A.allowed_acts("autonomy_done", "private", r"D://x//a.mp4")
          == ("say", "video", "skip"))
    check("产物 .png 给 image",
          A.allowed_acts("autonomy_done", "private", r"D://x//a.png")
          == ("say", "image", "skip"))
    check("没有产物只给 say/skip",
          A.allowed_acts("autonomy_done", "private", "") == ("say", "skip"))
    check("未知后缀不当作产物",
          A.allowed_acts("autonomy_done", "private", r"D://x//a.log")
          == ("say", "skip"))
    check("模型选 video 但没产物 → skip",
          A.parse_model_decision('{"act":"video","text":""}', scene="private",
                                 allowed=("say", "skip"))["act"] == "skip")

    import tempfile
    from harness import tool_proc as TP
    d = tempfile.mkdtemp(prefix="art_")
    mp4 = os.path.join(d, "out.mp4")
    io.open(mp4, "wb").write(b"00")
    check("从任务输出里捞到真实产物",
          TP._guess_artifact("done: " + mp4 + " (12.3MB)") == mp4,
          TP._guess_artifact("done: " + mp4))
    check("不存在的路径不当产物",
          TP._guess_artifact(r"D://no//such//x.mp4") == "")

    print("\n=== J) ⑭ 关心 + 视频解析语义 ===")
    care = L.care_observation({"vitals": {"loneliness": 0.72}}, "10001", t, "private")
    check("孤独 0.72 私聊 → care", care and care["kind"] == "care", care)
    check("证据带状态与强度",
          care and care["evidence"]["feel"] == "loneliness"
          and care["evidence"]["strength"] == 0.72, care)
    check("群聊不说孤独（不表演脆弱）",
          L.care_observation({"vitals": {"loneliness": 0.72}}, "829", t, "group") is None)
    check("群聊可以说累",
          L.care_observation({"vitals": {"burnout": 0.7}}, "829", t, "group") is not None)
    check("阈值以下不产出",
          L.care_observation({"vitals": {"loneliness": 0.3}}, "1", t) is None)
    check("没有收件人不产出",
          L.care_observation({"vitals": {"loneliness": 0.9}}, "", t) is None)
    care2 = L.care_observation({"vitals": {"loneliness": 0.72}}, "10001", t + 100, "private")
    check("同一状态 4 小时内去重一致", care["dedupe_key"] == care2["dedupe_key"])
    care3 = L.care_observation({"vitals": {"loneliness": 0.72}}, "10001",
                               t + 5 * 3600, "private")
    check("跨 4 小时换新 key", care["dedupe_key"] != care3["dedupe_key"])

    check("解析任务 → mode=analyze",
          L._job_mode({"label": "视频解析", "artifact": r"D://x//f.png"}) == "analyze")
    check("有产物 → mode=generate",
          L._job_mode({"label": "渲染", "artifact": r"D://x//a.mp4"}) == "generate")
    check("普通任务 → mode=job", L._job_mode({"label": "npm install"}) == "job")
    _jo = L.job_observation({"job_id": "j", "label": "视频解析", "ts": t}, "1", t)
    check("analyze 会写进证据", _jo["evidence"]["mode"] == "analyze", _jo)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
