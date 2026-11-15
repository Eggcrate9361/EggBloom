# -*- coding: utf-8 -*-
"""drive.tick 的 token 记账自检。

为什么写它：tick 原来只读 res["usage"]，而 agent_service.run_chat 返回的是
AgentResult.to_dict()，用量挂在 usage_total={input,output,total} —— 没有 usage 键。
于是每轮 journal 都是 "tokens": 0、state.total_tokens 永远 0，醒来那句
「你这条线已经花了 N tokens」再也没出现过（静默失败，不报错）。

做法：数据目录指到临时目录，harness.agent_service 换成假模块，
分别喂 usage_total / 缺 total / 老式 usage 字典 / 完全没用量，断言记账。
不跑真模型、不碰真 data/drive。

用法：python tests/test_drive_tokens.py     （0 通过，1 失败）
"""
import importlib, json, os, sys, tempfile, types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # tests/ 上跳一层就是仓库根
FAILED = []


def check(cond, msg):
    if cond:
        print("  ok   " + msg)
    else:
        print("  FAIL " + msg)
        FAILED.append(msg)


def load_drive(tmp):
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    sys.modules.pop("drive", None)
    drive = importlib.import_module("drive")
    drive._DATA = tmp
    drive._GOALS = os.path.join(tmp, "goals.md")
    drive._JOURNAL = os.path.join(tmp, "journal.jsonl")
    drive._STATE = os.path.join(tmp, "state.json")
    drive._ensure()
    return drive


def fake_service(ret, capture=None):
    mod = types.ModuleType("harness.agent_service")
    mod.is_ready = lambda: True

    def _run(**kw):
        if capture is not None:
            capture.update(kw)
        return ret

    mod.run_chat = _run
    sys.modules["harness.agent_service"] = mod
    try:
        importlib.import_module("harness").agent_service = mod
    except Exception:
        pass
    return mod


def read_journal(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def case_usage_total():
    print("[1] run_chat 返回 usage_total（真接口的形状）")
    drive = load_drive(tempfile.mkdtemp(prefix="drv-"))
    fake_service({"ok": True, "answer": "干完了", "steps": 3,
                  "usage_total": {"input": 1200, "output": 340, "total": 1540}})
    r = drive.tick()
    check(r.get("ok") is True, "tick 正常返回")
    check(r.get("tokens") == 1540,
          "tick 回传 tokens=1540（实际 %r）" % (r.get("tokens"),))
    st = drive.state()
    check(int(st.get("total_tokens") or 0) == 1540,
          "state.total_tokens 累加为 1540（实际 %r）" % (st.get("total_tokens"),))
    check(int(st.get("last_tokens") or 0) == 1540,
          "state.last_tokens 记为 1540（实际 %r）" % (st.get("last_tokens"),))
    ticks = [e for e in read_journal(drive._JOURNAL) if e.get("kind") == "tick"]
    check(bool(ticks) and int(ticks[-1].get("tokens") or 0) == 1540,
          "journal 里 tick 的 tokens 不再是 0")
    check(bool(ticks) and int(ticks[-1].get("tokens_in") or 0) == 1200
          and int(ticks[-1].get("tokens_out") or 0) == 340,
          "journal 里记下了 input/output 分项")
    fake_service({"ok": True, "answer": "NOOP", "steps": 0,
                  "usage_total": {"input": 100, "output": 20, "total": 120}})
    drive.tick()
    st = drive.state()
    check(int(st.get("total_tokens") or 0) == 1660,
          "第二轮后总量累计为 1660（实际 %r）" % (st.get("total_tokens"),))
    check("1,660" in drive.build_prompt(),
          "醒来时的提示里带上了累计用量（原来永远是空）")


def case_total_missing():
    print("[2] usage_total 里只有 input/output，total 缺省")
    drive = load_drive(tempfile.mkdtemp(prefix="drv-"))
    fake_service({"ok": True, "answer": "x",
                  "usage_total": {"input": 30, "output": 12}})
    r = drive.tick()
    check(r.get("tokens") == 42,
          "回退为 input+output=42（实际 %r）" % (r.get("tokens"),))


def case_usage_dict():
    print("[3] 老式顶层 usage 字典能算，完全没有用量不抛异常")
    drive = load_drive(tempfile.mkdtemp(prefix="drv-"))
    fake_service({"ok": True, "answer": "x",
                  "usage": {"prompt_tokens": 7, "completion_tokens": 5}})
    r = drive.tick()
    check(r.get("tokens") == 12,
          "usage 字典也算得出 12（实际 %r）" % (r.get("tokens"),))

    drive = load_drive(tempfile.mkdtemp(prefix="drv-"))
    fake_service({"ok": True, "answer": "x"})
    r = drive.tick()
    check(r.get("ok") is True and r.get("tokens") == 0,
          "没有用量信息时记 0 且不抛异常")


def case_heavy_turn():
    print("[5] 单轮超警戒线时打 heavy 标记、下一轮提示里点名")
    drive = load_drive(tempfile.mkdtemp(prefix="drv-"))
    fake_service({"ok": True, "answer": "干完了",
                  "usage_total": {"input": 380000, "output": 8000, "total": 388000}})
    r = drive.tick()
    check(r.get("heavy") is True, "回传 heavy=True（实际 %r）" % (r.get("heavy"),))
    ticks = [e for e in read_journal(drive._JOURNAL) if e.get("kind") == "tick"]
    check(bool(ticks) and ticks[-1].get("heavy") is True,
          "journal 里记下了 heavy=True（不只活在返回值里）")
    p = drive.build_prompt()
    check("警戒线" in p, "下一轮醒来的提示里点名超支")

    drive = load_drive(tempfile.mkdtemp(prefix="drv-"))
    fake_service({"ok": True, "answer": "x",
                  "usage_total": {"input": 900, "output": 100, "total": 1000}})
    r = drive.tick()
    check(r.get("heavy") is False, "小轮次 heavy=False（不误报）")
    check("警戒线" not in drive.build_prompt(), "小轮次不会吓唬自己")


def case_heavy_streak():
    print("[6] 超支不许静默：每轮过线落 note，连着超线升级叫停")
    drive = load_drive(tempfile.mkdtemp(prefix="drv-"))
    fake_service({"ok": True, "answer": "x",
                  "usage_total": {"input": 200000, "output": 5000, "total": 205000}})
    drive.tick()
    notes = [e for e in read_journal(drive._JOURNAL) if e.get("kind") == "note"]
    check(len(notes) == 1, "第一轮过线就落了一条 note（实际 %d 条）" % len(notes))
    check(drive.state().get("heavy_streak") == 1,
          "state.heavy_streak 记 1（实际 %r）" % (drive.state().get("heavy_streak"),))
    check("连续" not in drive.build_prompt(),
          "只超一轮时还不喊「连续超线」")

    drive.tick()
    check(drive.state().get("heavy_streak") == 2,
          "连着超两轮 streak=2（实际 %r）" % (drive.state().get("heavy_streak"),))
    notes = [e for e in read_journal(drive._JOURNAL) if e.get("kind") == "note"]
    check(len(notes) == 2 and "连第 2 轮" in notes[-1].get("text", ""),
          "第二条 note 点明「连第 2 轮」")
    p = drive.build_prompt()
    check("连续 2 轮超线" in p, "提示里升级成叫停")

    fake_service({"ok": True, "answer": "x",
                  "usage_total": {"input": 900, "output": 100, "total": 1000}})
    drive.tick()
    check(drive.state().get("heavy_streak") == 0,
          "小轮次把 streak 归零（实际 %r）" % (drive.state().get("heavy_streak"),))
    check("连续" not in drive.build_prompt(), "归零后不再喊连续超线")


def case_pending_goals():
    print("[4] 清单计数只数未做完的")
    drive = load_drive(tempfile.mkdtemp(prefix="drv-"))
    with open(drive._GOALS, "w", encoding="utf-8") as f:
        f.write("# 我想做的\n\n> 引言不算\n\n"
                "- [x] 做完了的\n- [ ] 还没做 A\n- [ ] 还没做 B\n- 普通一行\n")
    check(drive.pending_goals() == 2,
          "只数出 2 件未做（实际 %r）" % (drive.pending_goals(),))
    check(drive.pending_goals() != len(drive.goals().splitlines()),
          "不再拿总行数冒充剩余条数")


def case_step_cap():
    print("[7] 主动时间自带步数闸，不再用 240 次工具调用的无限预算")
    drive = load_drive(tempfile.mkdtemp(prefix="drv-"))
    cap = {}
    fake_service({"ok": True, "answer": "x",
                  "usage_total": {"input": 10, "output": 2, "total": 12}}, capture=cap)
    drive.tick()
    check(cap.get("max_steps") == drive.DRIVE_MAX_STEPS,
          "tick 传了 max_steps=%r（实际 %r）"
          % (drive.DRIVE_MAX_STEPS, cap.get("max_steps")))
    check(0 < drive.DRIVE_MAX_STEPS <= 12,
          "步数闸是个矮天花板（%r），不是 0/不限" % (drive.DRIVE_MAX_STEPS,))
    check(cap.get("publish") is False, "仍然不往群里播")


def main():
    for fn in (case_usage_total, case_total_missing, case_usage_dict,
               case_heavy_turn, case_heavy_streak, case_pending_goals,
               case_step_cap):
        try:
            fn()
        except Exception as e:
            check(False, "%s 抛异常：%s: %s" % (fn.__name__, type(e).__name__, e))
    print()
    if FAILED:
        print("失败 %d 项：%s" % (len(FAILED), "；".join(FAILED)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
