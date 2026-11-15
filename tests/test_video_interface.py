# -*- coding: utf-8 -*-
"""tests/test_video_interface.py —— 本体视频出口（2026-11-06）。

不是 AIGC：只验证视频段能不能发出去、插件 ctx 能不能走到宿主出口。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


def main():
    import bot_core as bc

    td = tempfile.mkdtemp(prefix="eb-video-")
    vp = os.path.join(td, "clip.mp4")
    open(vp, "wb").write(b"fake-video-bytes")

    check("URL 原样", bc._video_ref("https://x/y.mp4") == "https://x/y.mp4")
    check("本地转绝对", bc._video_ref(vp) == os.path.abspath(vp))
    check("缺失文件为空", bc._video_ref(os.path.join(td, "no.mp4")) == "")

    calls = []

    def fake_send(segments, target, *, is_group=False, suffix="", kind="text"):
        calls.append({"segments": segments, "target": target,
                      "is_group": is_group, "kind": kind})
        return True, {"retcode": 0}

    orig = bc._ob_send_msg
    bc._ob_send_msg = fake_send
    try:
        check("私聊视频成功", bc.send_video(vp, "12345") is True)
        check("私聊段是 video",
              calls[-1]["segments"][0]["type"] == "video"
              and calls[-1]["kind"] == "video", calls[-1])
        check("视频带本地路径",
              calls[-1]["segments"][0]["data"]["file"] == os.path.abspath(vp), calls[-1])
        check("群视频成功", bc.send_group_video("67890", vp) is True)
        check("群视频 is_group", calls[-1]["is_group"] is True, calls[-1])
        check("缺失文件不发", bc.send_video(os.path.join(td, "no.mp4"), "12345") is False)
    finally:
        bc._ob_send_msg = orig

    # 插件 ctx 出口
    from plugins.abi import PluginContext

    hosted = []

    class Mgr:
        _host_api = {"send_video": lambda path, target=None, group_id=None, thumb="":
                     hosted.append((path, target, group_id, thumb)) or True}

        def _ai_client(self):
            return None

    ctx = PluginContext("video_test", "1.0.0", manager=Mgr())
    check("ctx.send_video 走宿主", ctx.send_video(vp, target="1") is True)
    check("宿主参数完整", hosted and hosted[-1][0] == vp and hosted[-1][1] == "1", hosted)

    print("=" * 58)
    if FAIL:
        print(f"失败 {FAIL} 条：")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print(f"通过 {PASS} / 失败 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
