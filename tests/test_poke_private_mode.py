# -*- coding: utf-8 -*-
"""戳一戳「回复路径可选项」回归（1.6.0 正式版，2026-11-10）。

两个场景各自独立三态。收到戳后不再自动反戳，而是按下面的模式进入生成；
模型只有主动输出 [POKE] 才执行反戳，否则可以回话、只发表情或沉默：

    群聊  poke_group_mode   = pipeline（视同点名，forced=True）
                            | gated（同一条主链，forced=False，过决策器/发言闸）
                            | off（静默）
    私聊  poke_private_mode = standalone（独立小剧场，1.6.0 之前的行为）
                            | mainline（塞回 MSG_QUEUE 走主链，接得上上下文）
                            | off（静默）

钉住的几件事：
  · 群聊文本降级**全部落在现有 pipeline 上**，不新建 1.5.x 已废弃的独立小剧场
    （真机事故：戳+消息并行生成两条、主循环被占 9 秒）。offs 不 upsert。
  · 私聊 mainline 的注入文本**不带「第 N 次」**（数字是模板锚点，模型会顺着
    数数复读；R2 第一批 57aa2a4 已把次数从注入文本里拿掉）。
  · mainline 必须带 _REQUEUE_FLAG：既跳过合并缓冲（否则戳完要等 8 秒、
    甚至被自己拦成死循环），又让 main() 跳过消息转存与 memory_feed
    （「被戳」不是对方说的话，不该进 chat_log / 记忆流）。
  · 配置真相源全链路：DEFAULT_CONFIG / example / _fieldmeta / v3 控件 /
    v4 CFG_OPTIONS / 导出文档，缺任一环配置页就会只剩「-- 未设置 --」。

运行方式（项目根目录）：
    python tests/test_poke_private_mode.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import types
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot_core  # noqa: E402
import config as _cfgmod  # noqa: E402

# 停掉 bot_core 带起来的守护线程：否则测试全过、退出码却是 0xC0000409。
sys.path.insert(0, HERE)
from _quiet_exit import quiet_exit  # noqa: E402
quiet_exit()

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} {extra}")
        print(f"  [FAIL] {name} {extra}")


def read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ── 事件构造 ────────────────────────────────────────────────────────────
def group_poke(**kw):
    ev = {"post_type": "notice", "notice_type": "notify", "sub_type": "poke",
          "group_id": 10001, "user_id": "10086001",
          "target_id": str(bot_core.BOT_QQ)}
    ev.update(kw)
    return ev


def private_poke(**kw):
    ev = {"post_type": "notice", "notice_type": "notify", "sub_type": "poke",
          "user_id": "10086001", "target_id": ""}
    ev.update(kw)
    return ev


@contextlib.contextmanager
def poke_env(cfg=None):
    """把所有外部副作用换成 Mock，返回带句柄的命名空间。

    受控 _config 只含本次要用的键 —— 顺便验证「配置缺省时回落默认值」。
    """
    conf = {"poke_timeout": 600,
            "poke_group_mode": "pipeline",
            "poke_private_mode": "standalone"}
    if cfg is not None:
        conf = dict(cfg)

    snap = (dict(bot_core.POKE_COUNTS), dict(bot_core.PRIVATE_SESSION),
            dict(bot_core.chat_history))
    bot_core.POKE_COUNTS.clear()
    bot_core.PRIVATE_SESSION.clear()

    env = types.SimpleNamespace()
    env.conf = conf
    env.log = mock.Mock()
    env.emit = mock.Mock()
    env.save_event = mock.Mock()
    env.nick = mock.Mock(side_effect=lambda u: f"昵称{u}" if u else "")
    env.poke_back = mock.Mock(return_value=False)
    env.upsert = mock.Mock(return_value="created")
    env.queue = mock.Mock()
    env.deepseek = mock.Mock(return_value="别戳啦～")
    env.send = mock.Mock(return_value=True)
    env.send_image = mock.Mock(return_value=True)
    env.match_meme = mock.Mock(return_value=None)
    env.nicknames = mock.Mock(return_value=["测试"])
    env.decide = mock.Mock(return_value={
        "action": "model", "emotion": "", "confidence": 0.2,
        "reason": "test_model", "scores": {},
    })
    tempdir = tempfile.TemporaryDirectory(prefix="poke-mode-")

    fake_meme = types.ModuleType("meme_engine")
    fake_meme.parse_meme_intent = lambda s: (s, None)
    fake_meme.match_meme = lambda s: None

    patches = [
        mock.patch.dict(os.environ, {
            "EGGBLOOM_POKE_STATE": os.path.join(tempdir.name, "state.json")
        }),
        mock.patch.object(bot_core.poke_policy, "decide", env.decide),
        mock.patch.object(bot_core, "_config", conf),
        mock.patch.object(bot_core, "log", env.log),
        mock.patch.object(bot_core, "_emit_plugin_event", env.emit),
        mock.patch.object(bot_core, "save_event_log", env.save_event),
        mock.patch.object(bot_core, "resolve_qq_nick", env.nick),
        mock.patch.object(bot_core, "_send_poke_back", env.poke_back),
        mock.patch.object(bot_core, "_group_allowed", mock.Mock(return_value=True)),
        mock.patch.object(bot_core, "_pipeline_upsert", env.upsert),
        mock.patch.object(bot_core, "MSG_QUEUE", env.queue),
        mock.patch.object(bot_core, "TARGET_QQS", ["10086001"]),
        mock.patch.object(bot_core, "call_deepseek", env.deepseek),
        mock.patch.object(bot_core, "send_message", env.send),
        mock.patch.object(bot_core, "send_image", env.send_image),
        mock.patch.object(bot_core, "match_meme", env.match_meme),
        mock.patch.object(bot_core, "_bot_nicknames", env.nicknames),
        mock.patch.object(bot_core, "MEME_OK", False),
        mock.patch.dict(sys.modules, {"meme_engine": fake_meme}),
    ]
    for p in patches:
        p.start()
    try:
        yield env
    finally:
        for p in reversed(patches):
            p.stop()
        bot_core.POKE_COUNTS.clear(); bot_core.POKE_COUNTS.update(snap[0])
        bot_core.PRIVATE_SESSION.clear(); bot_core.PRIVATE_SESSION.update(snap[1])
        bot_core.chat_history.clear(); bot_core.chat_history.update(snap[2])
        tempdir.cleanup()


def queued_texts(env):
    """从 MSG_QUEUE.put 的调用里取出注入文本。"""
    out = []
    for call in env.queue.put.call_args_list:
        msg = call.args[0] if call.args else call.kwargs.get("item", {})
        for seg in (msg.get("message") or []):
            if seg.get("type") == "text":
                out.append(seg.get("data", {}).get("text", ""))
    return out


def queued_msgs(env):
    return [c.args[0] if c.args else c.kwargs.get("item", {})
            for c in env.queue.put.call_args_list]


# ══════════════════════════════════════════════════════════════════════
# 1. 配置真相源全链路
# ══════════════════════════════════════════════════════════════════════
def test_config_chain():
    d = _cfgmod.DEFAULT_CONFIG
    check("DEFAULT_CONFIG 群聊默认 pipeline", d.get("poke_group_mode") == "pipeline",
          repr(d.get("poke_group_mode")))
    check("DEFAULT_CONFIG 私聊默认 standalone", d.get("poke_private_mode") == "standalone",
          repr(d.get("poke_private_mode")))

    ex = json.loads(read("bot_config.json.example"))
    check("example 两键与默认值一致",
          ex.get("poke_group_mode") == "pipeline"
          and ex.get("poke_private_mode") == "standalone",
          f"{ex.get('poke_group_mode')} / {ex.get('poke_private_mode')}")

    fm = json.loads(read("scripts/_fieldmeta.json")).get("fields", {})
    for key, vals in (("poke_group_mode", {"pipeline", "gated", "off"}),
                      ("poke_private_mode", {"standalone", "mainline", "off"})):
        f = fm.get(key) or {}
        got = {o[0] for o in (f.get("options") or [])}
        check(f"fieldmeta {key} options 三值齐全", got == vals, repr(got))
        check(f"fieldmeta {key} 是 select 且 default 对齐",
              f.get("ctl") == "select" and f.get("default") == d.get(key),
              f"{f.get('ctl')} / {f.get('default')}")

    v3 = read("webui/webui_artistic_v3.html")
    v4 = read("webui/webui_artistic_v4.html")
    check("v3 两个 select 控件存在",
          'id="sPokeGroupMode"' in v3 and 'id="sPokePrivateMode"' in v3)
    check("v3 两个键都被 collectSettings 收集",
          "poke_group_mode: val('sPokeGroupMode')" in v3
          and "poke_private_mode: val('sPokePrivateMode')" in v3)
    # v4 的下拉实际读 CFG_OPTIONS[k]，FIELD_META.options 只是冗余说明；
    # 少了这一项页面就只剩「-- 未设置 --」，配置改不动。
    check("v4 CFG_OPTIONS 两个键都不能缺",
          "poke_group_mode: [[" in v4 and "poke_private_mode: [[" in v4)

    docs = read("docs/配置项说明.md")
    check("导出文档两键都在", "poke_group_mode" in docs and "poke_private_mode" in docs)

    src = read("bot_core.py")
    check("生产代码两个键都有读取点",
          '_config.get("poke_group_mode")' in src
          and '_config.get("poke_private_mode")' in src)


# ══════════════════════════════════════════════════════════════════════
# 2. 群聊三态：同一条 pipeline，不新建小剧场
# ══════════════════════════════════════════════════════════════════════
def test_group_modes():
    cases = (("pipeline", True, 1), ("gated", False, 1), ("off", None, 0))
    for mode, forced, n_upsert in cases:
        with poke_env({"poke_timeout": 600, "poke_group_mode": mode}) as env:
            bot_core._handle_poke_event(group_poke())
            check(f"群聊 {mode}: upsert 调用 {n_upsert} 次",
                  env.upsert.call_count == n_upsert,
                  f"实际 {env.upsert.call_count}")
            if forced is not None:
                kw = env.upsert.call_args.kwargs
                check(f"群聊 {mode}: forced={forced}", kw.get("forced") is forced,
                      repr(kw.get("forced")))
                check(f"群聊 {mode}: source=poke / poke_uid 透传",
                      kw.get("source") == "poke" and kw.get("poke_uid") == "10086001")
                check(f"群聊 {mode}: 低把握模型允许动作标记",
                      kw.get("poke_allow_action") is True)
                check(f"群聊 {mode}: 注入文本不带次数",
                      "（戳了戳你）" in (kw.get("text") or "")
                      and "第 " not in (kw.get("text") or ""),
                      repr(kw.get("text")))
            check(f"群聊 {mode}: 带模型动作选项",
                  "[POKE]" in (kw.get("text") or "")
                  and "不要默认反戳" in (kw.get("text") or ""),
                  repr(kw.get("text")))
            check(f"群聊 {mode}: 不自动反戳", env.poke_back.call_count == 0)
            check(f"群聊 {mode}: 不旁生独立小剧场",
                  env.deepseek.call_count == 0 and env.send.call_count == 0
                  and env.queue.put.call_count == 0)

    # 非法值 / 配置缺省都回落到 pipeline，不能因为一个坏配置把回复路径改掉
    for label, cfg in (("非法值回落", {"poke_timeout": 600, "poke_group_mode": "wtf"}),
                       ("配置缺省回落", {"poke_timeout": 600})):
        with poke_env(cfg) as env:
            bot_core._handle_poke_event(group_poke())
            check(f"群聊 {label}: forced 仍为 True",
                  env.upsert.call_count == 1
                  and env.upsert.call_args.kwargs.get("forced") is True)


def test_model_poke_action():
    """模型输出动作标记才反戳，而且动作回合不再附带正文。"""
    clean, wanted = bot_core.parse_poke_intent("[POKE]")
    check("动作标记可解析", wanted is True and clean == "", repr((clean, wanted)))
    clean, wanted = bot_core.parse_poke_intent("懒得动 [戳一戳]")
    check("中文动作标记也可解析", wanted is True and clean == "懒得动", repr((clean, wanted)))

    with poke_env() as env:
        env.deepseek.return_value = "[POKE]"
        env.poke_back.return_value = True
        bot_core._handle_poke_event(private_poke())
        check("私聊：模型选择后只反戳一次", env.poke_back.call_count == 1)
        check("私聊：动作回合不发正文",
              env.send.call_count == 0 and env.queue.put.call_count == 0)
        check("私聊：动作回合不写 history",
              "10086001" not in bot_core.chat_history)


def test_policy_direct_actions():
    """反射层高把握时直接执行，不进入模型。"""
    with poke_env() as env:
        env.decide.return_value = {
            "action": "poke", "emotion": "", "confidence": 0.9,
            "reason": "test_poke", "scores": {},
        }
        env.poke_back.return_value = True
        bot_core._handle_poke_event(private_poke())
        check("直接 poke 调用反戳一次", env.poke_back.call_count == 1)
        check("直接 poke 不调模型", env.deepseek.call_count == 0)
        check("直接 poke 不发正文", env.send.call_count == 0)
        bot_core._handle_poke_event(private_poke())
        check("一分钟内第二次 poke 被硬闸拦下",
              env.poke_back.call_count == 1, env.poke_back.call_count)

    with poke_env() as env:
        env.decide.return_value = {
            "action": "meme", "emotion": "嫌弃", "confidence": 0.8,
            "reason": "test_meme", "scores": {},
        }
        env.match_meme.return_value = "/fake/meme.png"
        bot_core._handle_poke_event(private_poke())
        check("直接 meme 调匹配", env.match_meme.call_args.args[0] == "嫌弃")
        check("直接 meme 发图", env.send_image.call_count == 1)
        check("直接 meme 不调模型", env.deepseek.call_count == 0)

    with poke_env() as env:
        env.decide.return_value = {
            "action": "skip", "emotion": "", "confidence": 0.95,
            "reason": "test_skip", "scores": {},
        }
        bot_core._handle_poke_event(private_poke())
        check("直接 skip 不反戳 / 不调模型 / 不发送",
              env.poke_back.call_count == 0 and env.deepseek.call_count == 0
              and env.send.call_count == 0 and env.send_image.call_count == 0)


# ══════════════════════════════════════════════════════════════════════
# 3. 私聊三态：standalone / mainline / off
# ══════════════════════════════════════════════════════════════════════
def test_private_modes():
    # standalone：独立小剧场原样保留，不进主链
    with poke_env() as env:
        bot_core._handle_poke_event(private_poke())
        check("私聊 standalone: 单独调模型", env.deepseek.call_count == 1)
        check("私聊 standalone: 直接发送", env.send.call_count == 1)
        check("私聊 standalone: 不进 MSG_QUEUE", env.queue.put.call_count == 0)
        check("私聊 standalone: chat_history 补写一对",
              len(bot_core.chat_history.get("10086001", [])) == 2,
              repr(bot_core.chat_history.get("10086001")))
        check("私聊 standalone: 不自动反戳", env.poke_back.call_count == 0)

    # mainline：塞回主链，带 _REQUEUE_FLAG，不带次数，不自己生成
    with poke_env({"poke_timeout": 600, "poke_private_mode": "mainline"}) as env:
        bot_core._handle_poke_event(private_poke())
        msgs = queued_msgs(env)
        check("私聊 mainline: 恰好入队 1 条", len(msgs) == 1, repr(len(msgs)))
        if msgs:
            m = msgs[0]
            check("私聊 mainline: private / 名单内 QQ / 唯一 message_id",
                  m.get("message_type") == "private"
                  and m.get("user_id") == "10086001"
                  and str(m.get("message_id", "")).startswith("_poke_"),
                  repr(m.get("message_id")))
            check("私聊 mainline: 带 _REQUEUE_FLAG",
                  m.get(bot_core._REQUEUE_FLAG) is True)
            txt = queued_texts(env)[0] if queued_texts(env) else ""
            check("私聊 mainline: 注入文本不带次数",
                  txt == "（戳了戳你）" and "第 " not in txt, repr(txt))
            check("私聊 mainline: 合并缓冲放行（戳完立刻回）",
                  bot_core._should_buffer_private(txt, [], m) is False)
        check("私聊 mainline: 不直接调模型、不直接发送",
              env.deepseek.call_count == 0 and env.send.call_count == 0)
        check("私聊 mainline: 不写 chat_history（由主链统一负责）",
              "10086001" not in bot_core.chat_history)
        check("私聊 mainline: 不自动反戳", env.poke_back.call_count == 0)

    # mainline 第二次戳：文案换形态但仍无次数
    with poke_env({"poke_timeout": 600, "poke_private_mode": "mainline"}) as env:
        bot_core._handle_poke_event(private_poke())
        bot_core._handle_poke_event(private_poke())
        texts = queued_texts(env)
        check("私聊 mainline: 连戳两次两种文案",
              texts == ["（戳了戳你）", "（又戳了你一下）"], repr(texts))
        check("私聊 mainline: 两次都没有次数",
              all("第 " not in t for t in texts))

    # off：静默
    with poke_env({"poke_timeout": 600, "poke_private_mode": "off"}) as env:
        bot_core._handle_poke_event(private_poke())
        check("私聊 off: 不自动反戳", env.poke_back.call_count == 0)
        check("私聊 off: 不入队 / 不调模型 / 不发送 / 不写 history",
              env.queue.put.call_count == 0 and env.deepseek.call_count == 0
              and env.send.call_count == 0
              and "10086001" not in bot_core.chat_history)

    # 非法值 / 缺省值回落到 standalone
    for label, cfg in (("非法值回落", {"poke_timeout": 600, "poke_private_mode": "wtf"}),
                       ("配置缺省回落", {"poke_timeout": 600})):
        with poke_env(cfg) as env:
            bot_core._handle_poke_event(private_poke())
            check(f"私聊 {label}: 仍走独立小剧场",
                  env.deepseek.call_count == 1 and env.queue.put.call_count == 0)

    # 名单外：任何模式都不回应（与私聊消息同一道门槛）
    for mode in ("standalone", "mainline", "off"):
        with poke_env({"poke_timeout": 600, "poke_private_mode": mode}) as env:
            bot_core._handle_poke_event(private_poke(user_id="10086099"))
            check(f"私聊 {mode}: 名单外不反戳 / 不入队 / 不调模型",
                  env.poke_back.call_count == 0 and env.queue.put.call_count == 0
                  and env.deepseek.call_count == 0)


# ══════════════════════════════════════════════════════════════════════
# 4. _REQUEUE_FLAG 在主链的下游语义（静态闸）
# ══════════════════════════════════════════════════════════════════════
def test_requeue_downstream():
    src = read("bot_core.py")
    check("main() 仍按 _REQUEUE_FLAG 跳过消息转存 / memory_feed",
          "if not msg.get(_REQUEUE_FLAG):" in src)
    check("_should_buffer_private 仍对重入队消息放行",
          "if msg.get(_REQUEUE_FLAG):" in src
          and bot_core._should_buffer_private("随便一句", [],
                                              {bot_core._REQUEUE_FLAG: True}) is False)
    # 反例：普通消息照旧进缓冲，别把放行写成了「无条件不缓冲」
    check("普通私聊消息仍会进合并缓冲",
          bot_core._should_buffer_private("在吗", [], {"message_id": "x"}) is True)
    check("私聊戳重入队带动作事件标记",
          "_POKE_EVENT_FLAG = " in src and "_POKE_EVENT_FLAG: True" in src)
    check("主链只对戳事件接受模型动作",
          "if msg.get(_POKE_EVENT_FLAG):" in src
          and "parse_poke_intent(reply)" in src)
    check("动作说明只进模型输入，不污染 history",
          "_pm_text_for_ai += _POKE_ACTION_HINT" in src)
    check("gated 不再被 source=poke 强制放行",
          'str(_config.get("poke_group_mode") or "pipeline") == "pipeline"' in src)


def main():
    test_config_chain()
    test_group_modes()
    test_model_poke_action()
    test_policy_direct_actions()
    test_private_modes()
    test_requeue_downstream()
    print("\n" + "=" * 58)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
