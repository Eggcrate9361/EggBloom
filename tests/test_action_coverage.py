# -*- coding: utf-8 -*-
"""tests/test_action_coverage.py —— 「模型看不见的自动调用」全覆盖回归（2026-11-15）

## 为什么有这个文件

2026-11-15 全仓盘了一遍：**系统侧自动执行、但模型零途径知晓** 的动作有 31 类。
它们只有 `log()` 写 stdout / `qq_bot.log` —— 用户看得见，模型看不见。
于是出现这些真机症状：

  · 入群欢迎 / 撤回反馈 / 慢回填充说过了，下一轮它否认；
  · 截图、传文件、改名片、踢人做过了，问起来答不上；
  · 群里画图的"画图中，请稍等…"、子 agent 的回报正文，全都不在它的记忆里。

用户拍板「我的范围就是全补」，于是拆六批补齐。这个文件钉住补齐后的契约，
防止以后有人图省事绕过统一入账口。

## 判据

  [A] 词表自洽：每个会落账的 kind 都有中文动词，`remark` 等新词不遗漏
  [B] `QUIET_KINDS` 只挡 `render()`、不挡检索（群事件别挤占"你刚做过的事"）
  [C] 来源归类：`agent` 单列一类，绝不冒充 `model`（否则会误清挂起的提问）
  [D] 源码级：唯一出口 + 统一入账口 + 每类缺口都打了标记
  [E] `set_turn` 是**线程本地**的（子 agent 不会串到主链那一轮）
  [F] 插件 `api_call`：读接口零记录、写接口必记

运行：python tests/test_action_coverage.py
"""
import inspect
import io
import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import self_actions as sa  # noqa: E402
from harness import outbound_ledger as ol  # noqa: E402

# 账本落盘隔离：`self_actions.DATA_DIR` 写死在仓库里，不换会污染生产账本。
TMP = tempfile.mkdtemp(prefix="acts_cov_")
sa.DATA_DIR = TMP
sa.LOG_FILE = os.path.join(TMP, "self_actions.jsonl")

PASS, FAIL, FAILURES = 0, 0, []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


SRC = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()


def main():
    # ── [A] 词表自洽 ───────────────────────────────────────────────
    print("[A] 词表与持久化分级")
    check("PERSIST_KINDS 都配了中文动词",
          [k for k in sa.PERSIST_KINDS if k not in sa._KIND_WORD] == [],
          [k for k in sa.PERSIST_KINDS if k not in sa._KIND_WORD])
    for k in ("welcome", "recall", "notice", "restart", "slow_ack", "late",
              "upload", "video", "qzone", "profile", "request", "config",
              "plugin", "group_event", "remark", "tool_call"):
        check(f"  词表有 {k}", k in sa._KIND_WORD)
    check("record() 支持 persist 开关",
          "persist" in inspect.signature(sa.record).parameters)

    # ── [B] QUIET_KINDS 的边界 ─────────────────────────────────────
    print("\n[B] 群事件只进检索、不进 render")
    sa.record("say", scene="group", target="900", group_id="900",
              detail="我先开口了")
    sa.record("group_event", scene="group", target="900", group_id="900",
              detail="某个群友进群了", persist=False)
    blk = sa.render(scene="group", target="900")
    check("render 带上了我说的话", "我先开口了" in blk, blk)
    check("render 不含群事件", "某个群友进群了" not in blk, blk)
    check("检索照样查得到群事件",
          bool(sa.search(kind="group_event", target="900")),
          sa.search(kind="group_event", target="900"))

    # 只有群事件时不能返回"空标题"（零 token 边界）
    sa2_dir = tempfile.mkdtemp(prefix="acts_cov2_")
    old_dir, old_file, old_mem = sa.DATA_DIR, sa.LOG_FILE, sa._MEM
    sa.DATA_DIR, sa.LOG_FILE, sa._MEM = sa2_dir, os.path.join(sa2_dir, "a.jsonl"), None
    sa.record("group_event", scene="group", target="901", group_id="901",
              detail="只有事件", persist=False)
    empty = sa.render(scene="group", target="901")
    sa.DATA_DIR, sa.LOG_FILE, sa._MEM = old_dir, old_file, old_mem
    check("★ 只有群事件时 render 返回空串", empty == "", repr(empty))

    # ── [C] 来源归类 ───────────────────────────────────────────────
    print("\n[C] by= 归类（agent 不冒充 model）")
    check("model 前缀 → model", ol._by_of("model") == "model")
    check("agent 前缀 → agent", ol._by_of("agent action=agent答复") == "agent")
    check("rule 前缀 → rule", ol._by_of("rule action=画图") == "rule")
    check("plugin 前缀 → plugin", ol._by_of("plugin:proactivity") == "plugin")
    check("★ 没打标 → unknown（不许默认成 model）",
          ol._by_of("") == "unknown", ol._by_of(""))
    check("★ agent 不会被当成主链回复",
          ol._by_of("agent action=x") != "model")

    # ── [D] 源码级契约 ─────────────────────────────────────────────
    print("\n[D] 源码级：唯一出口 + 统一入账口")
    check("有统一入账口 _note_self_action", "def _note_self_action(" in SRC)
    check("有来源标记 _with_outbound_origin", "def _with_outbound_origin(" in SRC)
    check("★ 插件 api_call 走记账出口",
          "api_call=_plugin_api_call," in SRC)
    check("★ bot_core 无裸 api_call(send_group_msg)",
          'api_call("send_group_msg"' not in SRC
          and "api_call('send_group_msg'" not in SRC)
    check("★ bot_core 无裸 api_call(send_private_msg)",
          'api_call("send_private_msg"' not in SRC
          and "api_call('send_private_msg'" not in SRC)

    # 每个补过的出口都要留下可检索的字面标记（改坏一处这里就红）
    marks = [
        ("群撤回反馈", '_with_outbound_origin("rule", smart_send_group'),
        ("私聊撤回反馈", '_with_outbound_origin("rule", send_message, recall_msg'),
        ("画图（群）", 'action=画图'),
        ("慢回填充", 'action=慢回填充'),
        ("迟到投递", 'action=迟到投递'),
        ("子 agent 回报", 'action=子agent回报'),
        ("agent 占位", 'action=agent占位'),
        ("群 Agent 答复", 'action=agent答复'),
        ("插件 on_message 回复", 'action=插件回复'),
        ("管理员通知", 'action=通知'),
        ("重启通报", 'action=重启通报'),
        ("指令回执", 'action=指令'),
        ("画像报告", 'action=画像报告'),
        ("空闲退场", 'action=退场'),
        ("反戳兜底", 'action=反戳'),
        ("插件发图", 'action=插件发图'),
        ("插件发视频", 'action=插件发视频'),
        ("网页截图", 'action=网页截图'),
    ]
    for name, pat in marks:
        check(f"  {name} 打了来源标记", pat in SRC, pat)

    kinds = [
        ("群管理动作", '_note_self_action("admin", scene="group"'),
        ("新成员名片", '_note_self_action("admin", scene="group"'),
        ("好友备注", '_note_self_action("remark"'),
        ("入群欢迎", '_note_self_action("welcome"'),
        ("同意申请", '_note_self_action("request"'),
        ("传文件", '_note_self_action("file"'),
        ("群上传", '_note_self_action("upload"'),
        ("配置热重载", '_note_self_action("config"'),
        ("群事件", '_note_self_action("group_event"'),
        ("退场/主链发言", '_sa.record("say"'),
    ]
    for name, pat in kinds:
        check(f"  {name} 记了 kind", pat in SRC, pat)

    check("★ 子 agent 路径也标了 turn", "turn_target=" in SRC)
    check("★ 插件重载记进账本",
          "重载' if ok else '重载失败" in io.open(
              os.path.join(ROOT, "plugins", "hotrestart", "__init__.py"),
              encoding="utf-8").read())

    # ── [E] set_turn 线程本地 ──────────────────────────────────────
    print("\n[E] set_turn 是线程本地的")
    sa.set_turn(scene="group", target="900000005", group_id="900000005")
    seen = {}

    def worker():
        seen["turn"] = dict(sa._turn())
        sa.record_tool("run", "子线程干的活")

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    check("主线程的 turn 还在", sa._turn()["scene"] == "group")
    check("★ 子线程看不到主链那一轮", seen["turn"]["scene"] == "",
          seen.get("turn"))
    last = sa.search(kind="run", limit=1)
    check("子线程的动作没被记成主链的场景",
          bool(last) and last[0]["scene"] == "", last)

    # ── [F] 插件 api_call 记账 ─────────────────────────────────────
    print("\n[F] 插件 api_call：读不记、写必记")
    import bot_core as bc  # noqa: E402  （重，放最后）

    check("get_* 不算写", bc._api_is_write("get_group_list") is False)
    check("can_* 不算写", bc._api_is_write("can_send_image") is False)
    check("send_poke 算写", bc._api_is_write("send_poke") is True)
    check("set_group_card 算写", bc._api_is_write("set_group_card") is True)
    check("upload_group_file 算写", bc._api_is_write("upload_group_file") is True)
    check("端点→kind：戳", bc._api_action_kind("send_poke") == "poke")
    check("端点→kind：说话", bc._api_action_kind("send_group_msg") == "say")
    check("端点→kind：传文件", bc._api_action_kind("upload_group_file") == "upload")
    check("端点→kind：群管理", bc._api_action_kind("set_group_ban") == "admin")

    calls = []

    def fake_api(action, params=None):
        calls.append((action, dict(params or {})))
        return {"retcode": 0}

    real = bc.api_call
    bc.api_call = fake_api
    try:
        bc._plugin_api_call("get_group_list", {})
        bc._plugin_api_call("send_poke", {"user_id": "10001"})
        bc._plugin_api_call("set_group_card",
                            {"group_id": 900000005, "user_id": 10001,
                             "card": "新名片"})
        bc._plugin_api_call("send_group_msg",
                            {"group_id": 900000005, "message": "插件替我说的"})
    finally:
        bc.api_call = real

    check("读接口照常转发", any(c[0] == "get_group_list" for c in calls))
    pokes = [r for r in sa.search(kind="poke", limit=5) if r["target"] == "10001"]
    check("★ 戳一戳进了账本", bool(pokes), pokes)
    adm = [r for r in sa.search(kind="admin", limit=5)
           if r["target"] == "900000005"]
    check("★ 改名片进了账本", bool(adm), adm)
    said = [r for r in sa.search(kind="say", limit=5)
            if "插件替我说的" in str(r.get("detail") or "")]
    check("★ 插件直发的群消息进了账本", bool(said), said)
    check("★ 读接口没有进账本",
          not [r for r in sa.search(query="get_group_list", limit=5)])

    # 失败的回包不记（账本里不能有没发生的事）
    bc.api_call = lambda action, params=None: {"retcode": 100}
    try:
        bc._plugin_api_call("send_poke", {"user_id": "20002"})
    finally:
        bc.api_call = real
    check("★ retcode!=0 不记账",
          not [r for r in sa.search(kind="poke", limit=5)
               if r["target"] == "20002"])

    # ── [G] _note_self_action 的 history 边界 ──────────────────────
    print("\n[G] 统一入账口的 history 边界")
    bc.chat_history["777001"] = []
    bc._note_self_action("welcome", text="你好呀", scene="private",
                         target="777001", to_history=True)
    check("私聊：对方看得见的话写进 history",
          [m for m in bc.chat_history["777001"] if m["content"] == "你好呀"])
    bc.group_chat_history["900000009"] = []
    bc._note_self_action("welcome", text="进群了？坐！", scene="group",
                         target="900000009", group_id="900000009",
                         to_history=True)
    check("群聊：写进 group_chat_history",
          [m for m in bc.group_chat_history["900000009"]
           if m["content"] == "进群了？坐！"])
    bc.chat_history["777002"] = []
    bc._note_self_action("admin", scene="private", target="777002",
                         detail="踢了个人", to_history=False)
    check("★ 纯动作不写 history（免得它以为自己说过）",
          bc.chat_history["777002"] == [])
    check("纯动作仍进 self_actions",
          bool([r for r in sa.search(kind="admin", limit=5)
                if r["target"] == "777002"]))

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
