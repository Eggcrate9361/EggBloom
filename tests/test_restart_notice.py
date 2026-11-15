# -*- coding: utf-8 -*-
"""restart_notice 的回归测试（2026-11-09）。

跑法：python tests/test_restart_notice.py

验的是四件事（都是真机上容易翻车的点）：
  1. build_text 按「离开多久 + 几点」确实换了话，不是一个模板套到底
  2. 半夜/早上有前缀，下午档允许没有前缀
  3. marker 写入的 at = 请求时间 + 延迟（不能是过去时刻）
  4. mark → read → clear 之后读不到（只通报一次）
"""
import os
import sys
import tempfile
import time
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/ 上跳一层

from harness import restart_notice as rn
import random


class FakeRng:
    """固定 pick 第一个，测试要确定性。"""
    def choice(self, seq):
        return seq[0]


def t_build_text_varies():
    r = random.Random(42)
    seen = set()
    for gap in (3, 60, 600, 7200, 200000):
        for hour in (3, 7, 10, 13, 16, 20, 23):
            seen.add(rn.build_text(gap, hour, r))
    assert len(seen) >= 12, f"话太单调，只有 {len(seen)} 种"
    print(f"  1. 15 组输入共 {len(seen)} 种说法 ✓")


def t_prefix():
    f = FakeRng()
    assert rn.build_text(5, 3, f).startswith("这个点还没睡？")
    assert rn.build_text(5, 7, f).startswith("这个点起来了。")
    assert rn.build_text(600, 16, f) == "出去 10 分钟，回来了。"
    print("  2. 半夜/早上有前缀，下午直接说话 ✓")


def t_gap_wording():
    f = FakeRng()
    assert "秒" in rn.build_text(20, 10, f)
    assert "分钟" in rn.build_text(300, 10, f)
    assert "小时" in rn.build_text(7200, 10, f)
    assert "天" in rn.build_text(200000, 10, f)
    # 负数/垃圾输入不许炸
    assert rn.build_text(-5, 10, f)
    assert rn.build_text("abc", 10, f)
    print("  3. 秒/分钟/小时/天 分档，脏输入不炸 ✓")


def t_marker_roundtrip():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "notice.json")
    assert rn.read_marker(p) == {}
    ok = rn.write_marker("1000000001", is_group=False, at=time.time() + 3, path=p)
    assert ok
    m = rn.read_marker(p)
    assert m["target"] == "1000000001" and not m["is_group"]
    assert m["at"] > time.time(), "at 应该是将来时刻（真断掉的时刻）"
    rn.clear_marker(p)
    assert rn.read_marker(p) == {}, "清完还能读到 → 会重复通报"
    # 空 target 不写
    assert not rn.write_marker("", path=p)
    # 坏文件当没有
    with open(p, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert rn.read_marker(p) == {}
    print("  4. mark → read → clear，只通报一次 ✓")


def t_group_flag():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "n.json")
    rn.write_marker("123456", is_group=True, path=p)
    assert rn.read_marker(p)["is_group"] is True
    print("  5. 群目标能区分（回群不回私聊）✓")


def t_do_restart_writes_marker():
    """hotrestart.do_restart 带 target 时，便条的 at 必须是「现在+延迟」。"""
    from plugins import hotrestart as hr
    d = tempfile.mkdtemp()
    p = os.path.join(d, "n.json")
    old_run, old_path = hr._run, rn.MARKER_PATH
    old_sup = hr._RESTART_SUPPORTED
    hr._run = lambda argv, timeout=15.0: (0, "", "") if "is-active" not in argv \
        else (0, "active", "")
    hr._RESTART_SUPPORTED = True          # 本组测「便条怎么写」，平台闸另有用例
    rn.MARKER_PATH = p
    tgt = "1000000007"                    # 合成号，不用真号做夹具
    try:
        r = hr.do_restart(5, target=tgt, is_group=False)
        assert r.get("ok"), r
        m = rn.read_marker(p)
        assert m.get("target") == tgt, m
        assert 3 <= (m["at"] - time.time()) <= 6, f"at 应该是 now+5，实际差 {m['at']-time.time():.1f}s"
    finally:
        hr._run, rn.MARKER_PATH = old_run, old_path
        hr._RESTART_SUPPORTED = old_sup
    print("  6. do_restart 写了便条，at = 现在+延迟 ✓")


def t_clean_reply_rejects_backstage():
    """后台术语一律不许放出去 —— 那是把戏演砸，比单调严重得多。"""
    bad = ["重启完成，我回来了。", "服务器已恢复。", "进程回来了。",
           "]**重启完毕**[", "", "   ", "[不说话]"]
    for b in bad:
        assert rn.clean_reply(b) == "", f"漏出去了：{b!r}"
    ok = rn.clean_reply("「我回来了，刚眯了会儿。」")
    assert ok == "我回来了，刚眯了会儿。", ok
    # 超长的不收（模型容易写小作文）
    assert rn.clean_reply("我" * 50) == ""
    print("  7. 后台术语/空回/超长一律不收 ✓")


def t_compose_uses_model_first():
    """有模型就用模型的话；模型失败才落兜底。"""
    calls = {}

    def fake_ai(msg, **kw):
        calls["msg"] = msg
        calls["kw"] = kw
        return "我回来了，刚眯了会儿。"

    got = rn.compose(30, 15, "1000000001", call_ai=fake_ai)
    assert got == "我回来了，刚眯了会儿。", got
    assert "离线约" in calls["msg"] and "30 秒" in calls["msg"]
    assert calls["kw"].get("target_qq") == "1000000001"
    assert not calls["kw"].get("is_group")

    # 群里回群
    rn.compose(30, 15, "123456", is_group=True, call_ai=fake_ai)
    assert calls["kw"].get("group_id") == "123456" and calls["kw"].get("is_group")

    def boom(*a, **k):
        raise RuntimeError("超时")

    fb = rn.compose(30, 15, "1000000001", call_ai=boom, rng=random.Random(0))
    assert "30 秒" in fb, fb
    # 兜底句有三种说法，第三种是「才断了 30 秒，接上了」—— 没有「回来」二字。
    # 断言只钉「确实按秒数说了」+「是这两种收尾之一」，别钉死具体某一句。
    assert re.search(r"(回来|接上)", fb), fb

    # 模型自曝后台 → 也走兜底
    fb2 = rn.compose(30, 15, "1000000001",
                     call_ai=lambda *a, **k: "重启完了")
    assert "30 秒" in fb2 and "重启" not in fb2, fb2
    print("  8. 模型优先，失败/不合格落兜底 ✓")


def t_no_call_ai_is_table():
    """没给 call_ai（自检/离线）时不许抛，直接给表里的句子。"""
    assert rn.compose(5, 3).count("。") >= 1
    print("  9. 没有模型时纯查表，不抛 ✓")


def main():
    print("restart_notice 测试")
    for fn in (t_build_text_varies, t_prefix, t_gap_wording,
               t_marker_roundtrip, t_group_flag, t_do_restart_writes_marker,
               t_clean_reply_rejects_backstage, t_compose_uses_model_first,
               t_no_call_ai_is_table):
        fn()
    print("全过。")


if __name__ == "__main__":
    main()
