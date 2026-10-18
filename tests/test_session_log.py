# -*- coding: utf-8 -*-
"""任务 5.3 单测：事件溯源会话日志（试点一个群）。

手册验收：一个群的对话历史能**从日志完整回放**，且回放结果与实际发给模型的一致。
再加 DSH 的铁律与追加语义：
  - 「模型可见即已记录」
  - 追加**先快照冻结 + 校验无损 JSON**，**被拒绝的追加不改变日志**
  - QQ message_id 不唯一 → 用自有的 seq

导入 bot_core 时不要污染真实数据：本测试只用临时群号 + 临时日志目录。
"""
import os
import sys
import json
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import session_log as slog  # noqa: E402

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)


# ── 用临时目录隔离，绝不写真实 data/session_log ─────────────────────
_tmp = tempfile.mkdtemp(prefix="slogtest_")
_orig_dir = slog.SESSION_LOG_DIR
slog.SESSION_LOG_DIR = os.path.join(_tmp, "session_log")
slog.reset_cache()

TEST_GID = "900000001"

try:
    # ── enabled_for 白名单 ─────────────────────────────────────────
    os.environ.pop("EGGBLOOM_SESSION_LOG_GROUPS", None)
    check("默认未设置 → 全关", slog.enabled_for(TEST_GID) is False)
    os.environ["EGGBLOOM_SESSION_LOG_GROUPS"] = TEST_GID
    check("白名单命中 → 开", slog.enabled_for(TEST_GID) is True)
    check("白名单未命中 → 关", slog.enabled_for("123") is False)
    os.environ["EGGBLOOM_SESSION_LOG_GROUPS"] = f"{TEST_GID}, 888"
    check("多群+空格解析", slog.enabled_for("888") is True)

    # ── append + seq 单调（不依赖 QQ message_id）───────────────────
    e1 = slog.append_user_msg(TEST_GID, "你好")
    e2 = slog.append_bot_reply(TEST_GID, "嗨")
    e3 = slog.append_user_msg(TEST_GID, "在吗")
    check("seq 从 0 开始", e1["seq"] == 0)
    check("seq 单调递增", e2["seq"] == 1 and e3["seq"] == 2)
    check("事件带 type", e1["type"] == slog.EV_USER_MSG and e2["type"] == slog.EV_BOT_REPLY)
    check("事件带 ts", isinstance(e1["ts"], float))

    # ── 派生 messages（纯函数）──────────────────────────────────────
    msgs = slog.derive_messages("group", TEST_GID)
    check("派生条数=3", len(msgs) == 3)
    check("派生 role 顺序 user/assistant/user",
          [m["role"] for m in msgs] == ["user", "assistant", "user"])
    check("派生 content 一致", msgs[0]["content"] == "你好" and msgs[1]["content"] == "嗨")
    check("replay 等价 derive", slog.replay(TEST_GID) == msgs)
    check("limit 生效", len(slog.replay(TEST_GID, limit=2)) == 2)
    check("limit 取最近 N 条", slog.replay(TEST_GID, limit=1)[0]["content"] == "在吗")

    # ── ★可回放：清空内存索引，从磁盘重建 ──────────────────────────
    slog.reset_cache()
    msgs2 = slog.derive_messages("group", TEST_GID)
    check("★清缓存后从磁盘完整回放", msgs2 == msgs)

    # ── ★与"实际发给模型的一致"：模拟 bot_core 的 history 用法 ─────
    # bot_core: history = group_chat_history[gid][-10:]  然后 _build_cached_messages
    # 这里我们等价地记录同一字符串并派生，验证逐条一致
    slog.reset_cache()
    fake_history = []
    for i in range(12):
        u = f"[群友{i}(1000{i})] 消息{i}"[:300]
        r = f"回复{i}"[:300]
        fake_history.append({"role": "user", "content": u})
        fake_history.append({"role": "assistant", "content": r})
        slog.append_user_msg(TEST_GID, u)
        slog.append_bot_reply(TEST_GID, r)
    mem_window = fake_history  # 内存里本会是 [-16:]，这里 24 条仅示意
    derived_window = slog.replay(TEST_GID, limit=len(fake_history))
    check("★派生历史与实际内存 history 逐条一致", derived_window == mem_window)
    # chat 组装只取最近10条 → 派生同样支持
    check("★派生[-10:] 与内存[-10:] 一致",
          slog.replay(TEST_GID, limit=10) == fake_history[-10:])

    # ── ★追加校验：非无损 JSON 被拒绝且不改变日志 ──────────────────
    n_before = len(slog.events_of("group", TEST_GID))
    rejected = False
    try:
        slog.append("group", TEST_GID, "user_msg", {"content": float("nan")})
    except ValueError:
        rejected = True
    check("★NaN 载荷被拒绝（allow_nan=False）", rejected)
    check("★被拒绝的追加不改变日志", len(slog.events_of("group", TEST_GID)) == n_before)

    rejected2 = False
    try:
        slog.append("group", TEST_GID, "user_msg", {"content": {1, 2, 3}})  # set 不可 JSON
    except ValueError:
        rejected2 = True
    check("★set 载荷被拒绝", rejected2)
    check("★二次拒绝后日志仍不变", len(slog.events_of("group", TEST_GID)) == n_before)

    rejected3 = False
    try:
        slog.append("group", TEST_GID, "x", "not-a-dict")
    except TypeError:
        rejected3 = True
    check("★非 dict 载荷被拒绝", rejected3)

    # ── ★载荷被冻结：外部改原 dict 不影响日志 ──────────────────────
    payload = {"content": "原始"}
    ev = slog.append("group", TEST_GID, "user_msg", payload)
    payload["content"] = "被篡改"
    re_read = slog.derive_messages("group", TEST_GID)[-1]["content"]
    check("★载荷冻结（外部修改不影响日志）", re_read == "原始")

    # ── jsonl 落盘格式 ─────────────────────────────────────────────
    path = slog._ns_path("group", TEST_GID)
    check("日志文件存在", os.path.isfile(path))
    with open(path, "r", encoding="utf-8") as f:
        lines = [l for l in f if l.strip()]
    check("每行一条 JSON 事件", all(json.loads(l).get("seq") is not None for l in lines))
    check("行数=事件数", len(lines) == len(slog.events_of("group", TEST_GID)))

    # ── stats 诊断 ─────────────────────────────────────────────────
    st = slog.stats(TEST_GID)
    check("stats.total 正确", st["total"] == len(lines))
    check("stats 有群号", st["group_id"] == TEST_GID)

    # ── 命名空间隔离：不同群互不干扰 ───────────────────────────────
    slog.append_user_msg("900000002", "别的群")
    check("★不同群命名空间隔离",
          slog.events_of("group", "900000002")[0]["payload"]["content"] == "别的群"
          and slog.events_of("group", TEST_GID)[-1]["payload"]["content"] != "别的群")

    # ── 追加失败 → 不改变日志/派生状态（用只读路径模拟）────────────
    os.environ.pop("EGGBLOOM_SESSION_LOG_GROUPS", None)
    n_before2 = len(slog.events_of("group", "900000003"))
    orig = slog.SESSION_LOG_DIR
    try:
        slog.SESSION_LOG_DIR = os.path.join("\x00bad", "x")
        failed = False
        try:
            slog.append_user_msg("900000003", "写不进去")
        except IOError:
            failed = True
        check("★追加失败抛 IOError", failed)
    finally:
        slog.SESSION_LOG_DIR = orig
    check("★追加失败后日志未变", len(slog.events_of("group", "900000003")) == n_before2)

finally:
    slog.SESSION_LOG_DIR = _orig_dir
    slog.reset_cache()
    os.environ.pop("EGGBLOOM_SESSION_LOG_GROUPS", None)
    shutil.rmtree(_tmp, ignore_errors=True)

# ── 输出 ────────────────────────────────────────────────────────────
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in PASS:
    print(f"  PASS  {n}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
