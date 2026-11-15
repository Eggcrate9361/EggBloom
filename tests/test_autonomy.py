# -*- coding: utf-8 -*-
"""tests/test_autonomy.py —— 自主获取七段的离线契约（2026-11-06）。

不碰网络、不装真包：下载/安装/识别全部注入。
锁：缺口三类信号、探测先于下载、断点续传、自验、熔断、接线记录。
"""
import os
import sys
import tempfile
import time
import json
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from autonomy import capability_gap as gap_mod, probe, plan, acquire, verify, breaker, wire, loop
from autonomy.capability_gap import Gap

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
    td = tempfile.mkdtemp(prefix="eb-auto-")
    os.environ["EB_SEPARATION_AUDIT"] = os.path.join(td, "audit.jsonl")

    print("=== ① 缺口三类信号 ===")
    g = gap_mod.missing_tool("tts_speak", ["tts_speak"], need="tts")
    check("工具存在不报缺口", g is None, g)
    g = gap_mod.missing_tool("tts_speak", [], need="tts")
    check("工具缺失", g and g.kind == "missing_tool" and g.need == "tts", g)
    g = gap_mod.dependency_failure("ModuleNotFoundError: No module named 'soundfile'", need="tts")
    check("依赖缺失", g and g.kind == "dependency", g)
    g = gap_mod.dependency_failure("网络超时", need="tts")
    check("普通错误不算缺口", g is None, g)
    g = gap_mod.self_report("我这里没有把文字变成声音的能力", need="tts")
    check("自述不足（有具体能力）", g and g.kind == "self_report", g)
    g = gap_mod.self_report("我做不到", need="")
    check("自述无能力名不进循环", g is None, g)

    print("=== ② 探测 ===")
    env = probe.env_report(data_dir=td, disk_paths=[td],
                           net_check=lambda u: {"status": "ok"},
                           gpu_probe=lambda: [{"name": "fake", "vram_gb": 8}])
    check("OS/Python/磁盘都在", env.get("os") and env.get("python") and env.get("disk"), env)
    check("网络可注入", env["net"]["pypi"]["status"] == "ok", env.get("net"))
    check("GPU 可注入", env["hw"]["gpu"][0]["name"] == "fake", env.get("hw"))
    rp = probe.write_report(env, td)
    check("EnvReport 落盘", os.path.exists(rp), rp)

    print("=== ③ 计划与磁盘预检 ===")
    g = Gap("dependency", "tts", "No module named soundfile")
    p = plan.make_plan(g, env, data_dir=td, plugins_dir=os.path.join(td, "plugins"),
                       kind="model", url="http://example/x.bin", size_bytes=1024)
    check("计划可行", p.get("ok") is True, p)
    check("模型落点独立", p["artifacts"][0]["target"].endswith("models\\misc\\tts")
          or p["artifacts"][0]["target"].endswith("models/misc/tts"), p)
    gp = Gap("self_report", "ffmpeg", "video")
    pb = plan.make_plan(gp, env, data_dir=td, plugins_dir=os.path.join(td, "app", "plugins"),
                        kind="bin", url="http://x/ffmpeg.exe")
    check("显式 bin 走 bin", pb.get("kind") == "bin", pb)
    check("bin 落到 app/bin",
          pb["artifacts"][0]["target"].replace("\\", "/").endswith("app/bin/ffmpeg.exe"), pb)
    pv = plan.make_plan(gp, env, data_dir=td, plugins_dir=os.path.join(td, "app", "plugins"))
    check("ffmpeg 默认装 imageio-ffmpeg",
          pv.get("kind") == "pip" and pv["artifacts"][0].get("name") == "imageio-ffmpeg", pv)
    po = plan.make_plan(Gap("self_report", "ocr", "读字"), env, data_dir=td,
                        plugins_dir=os.path.join(td, "plugins"))
    check("ocr 默认包名正确", po["artifacts"][0].get("name") == "rapidocr_onnxruntime", po)
    env_big = dict(env)
    env_big["disk"] = {td: {"free_gb": 0.001}}
    p2 = plan.make_plan(g, env_big, data_dir=td, plugins_dir=td,
                        kind="model", size_bytes=10 ** 9)
    check("磁盘不足拒绝", p2.get("ok") is False and "只剩" in p2.get("error", ""), p2)

    print("=== ④ 断点续传 ===")
    dest = os.path.join(td, "download.bin")
    part = dest + ".part"
    with open(part, "wb") as f:
        f.write(b"abc")
    calls = []

    def http_get(url, headers):
        calls.append(dict(headers))
        if headers.get("Range"):
            return 206, b"def"
        return 200, b"abcdef"

    import hashlib
    want = hashlib.sha256(b"abcdef").hexdigest()
    r = acquire.download_resume("http://x", dest, http_get=http_get,
                                total_hint=6, expected_sha256=want)
    check("续传成功", r.get("ok") is True, r)
    check("发了 Range", calls and calls[0].get("Range") == "bytes=3-", calls)
    check("内容完整", open(dest, "rb").read() == b"abcdef")
    check("sha256 保留", r.get("sha256") == want, r.get("sha256"))
    short = os.path.join(td, "short.bin")
    rs = acquire.download_resume("http://x", short, http_get=lambda u, h: (200, b"abc"),
                                 total_hint=10)
    check("短包不静默成功", rs.get("ok") is False and "不完整" in (rs.get("error") or ""), rs)
    check("短包保留 .part", os.path.exists(short + ".part"), rs)

    print("=== ⑤ 自验 ===")
    wav = os.path.join(td, "tts.wav")
    with wave.open(wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 8000)
    check("TTS 时长自验", verify.verify_tts(wav).get("ok") is True, verify.verify_tts(wav))
    check("TTS 空文件拒绝", verify.verify_tts(os.path.join(td, "nope.wav")).get("ok") is False)
    check("可执行文件自验", verify.verify_bin(sys.executable, expect="Python").get("ok") is True)
    check("坏二进制自验拒绝", verify.verify_bin(os.path.join(td, "nope.exe")).get("ok") is False)
    asr = verify.verify_asr(lambda p: "测试", wav)
    check("ASR 闭环自验", asr.get("ok") is True and asr.get("text") == "测试", asr)

    print("=== ⑥ 熔断 ===")
    bd = os.path.join(td, "breaker")
    breaker.fail(bd, "tts", "first")
    check("一次失败不熔断", breaker.is_blocked(bd, "tts").get("blocked") is False)
    breaker.fail(bd, "tts", "second")
    check("两次失败熔断", breaker.is_blocked(bd, "tts").get("blocked") is True,
          breaker.is_blocked(bd, "tts"))

    print("=== ⑦ 七段闭环（假后端）===")
    class Fake:
        def __init__(self):
            self.acquires = 0
            self.verify_ok = True

        def probe_env(self, dd, net_check):
            self.dd = str(dd)
            self.order = getattr(self, "order", []) + ["probe"]
            return {"disk": {str(dd): {"free_gb": 100}}, "net": {}, "installed": {}}

        def make_plan(self, g, env):
            target = os.path.join(self.dd, "artifact.bin")
            return {"ok": True, "need": g.need, "kind": "model",
                    "artifacts": [{"kind": "model", "name": g.need, "target": target}]}

        def acquire(self, art, dd):
            self.acquires += 1
            self.order = getattr(self, "order", []) + ["acquire"]
            with open(art["target"], "wb") as f:
                f.write(b"x" * 2048)
            return {"ok": True, "bytes": 2048}

        def verify(self, art, result, dd):
            return {"ok": self.verify_ok}

        def wire(self, p, acquired, dd, root):
            wire.record_installed(dd, {"need": p.get("need"), "kind": "model",
                                       "target": acquired[0]["artifact"]["target"],
                                       "acquired_by": "ai"})
            return {"ok": True, "records": ["fake"]}

    fb = Fake()
    g = Gap("dependency", "tts", "No module named soundfile")
    r = loop.run(g, data_dir=os.path.join(td, "run1"), root=td, backend=fb)
    check("闭环成功", r.get("ok") is True, r)
    check("交付话术说装好了", "装好了" in (r.get("report") or ""), r.get("report"))
    check("执行了一次获取", fb.acquires == 1, fb.acquires)
    check("探测先于下载", fb.order == ["probe", "acquire"], fb.order)
    _inst = os.path.join(td, "run1", "autonomy", "_installed.json")
    check("安装记录落盘", os.path.exists(_inst), _inst)
    check("记录了 acquired_by=ai",
          json.load(open(_inst, encoding="utf-8"))[0].get("acquired_by") == "ai",
          json.load(open(_inst, encoding="utf-8")))
    check("审计有动作", os.path.exists(os.environ["EB_SEPARATION_AUDIT"]))

    fb2 = Fake(); fb2.verify_ok = False
    rd = os.path.join(td, "run2")
    r1 = loop.run(g, data_dir=rd, root=td, backend=fb2)
    r2 = loop.run(g, data_dir=rd, root=td, backend=fb2)
    r3 = loop.run(g, data_dir=rd, root=td, backend=fb2)
    check("自验失败返回", r1.get("ok") is False and r1.get("step") == "verify", r1)
    check("自验失败删产物", not os.path.exists(os.path.join(rd, "artifact.bin")))
    check("第二次失败后熔断", r2.get("ok") is False, r2)
    check("第三次不再获取", r3.get("blocked") is True and fb2.acquires == 2, r3)

    print("=== ⑧ 工具组注册（分离态）===")
    os.environ["EB_SEPARATION_FORCE"] = "1"
    import separation
    separation.bootstrap()
    from harness import tool_registry as tr
    from harness import bot_hook as bh
    import harness.tool_proc  # 真实启动时 bot_core 会 import；测试手动触发注册
    harness.tool_proc.register_all()
    names = tr.names_in("autonomy")
    # 1.6.0 P3 起 bootstrap() 还登记心跳裁决工具（autonomy/heartbeat.py），
    # 所以这一组是 5 个；四个老工具一个不少，新工具也必须有执行体。
    check("注册 5 个自主工具", set(names) == {
        "autonomy_probe", "autonomy_plan", "autonomy_install", "autonomy_status",
        "autonomy_heartbeat_decide"}, names)
    check("有执行体", "autonomy_install" in tr.functions(names), tr.functions(names).keys())
    check("心跳裁决工具有执行体", "autonomy_heartbeat_decide" in tr.functions(names),
          tr.functions(names).keys())
    schemas = bh.agent_tools_for("随便聊聊", is_group=False)
    snames = [s["function"]["name"] for s in schemas]
    check("agent_tools_for 分离态带自主组", "autonomy_install" in snames, snames)
    check("分离态带 job_wait", "job_wait" in snames, snames)

    print("=== ⑨ agent 循环里的缺口回调 ===")
    from harness import agent_loop as al
    seen = []
    calls = {"n": 0}

    def boom(args):
        raise ModuleNotFoundError("No module named 'soundfile'")

    def chat(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return "", [{"id": "t1", "function": {"name": "tts_tool", "arguments": "{}"}}], {}
        return "done", [], {}

    res = al.run_agent(session_id="gap-test", user_input="读给我听",
                       chat_fn=chat, tool_functions={"tts_tool": boom},
                       persist=False, on_capability_gap=lambda g: (seen.append(g), "用 autonomy_install")[1])
    check("回调收到缺口", bool(seen) and seen[0].need == "tts_tool", seen)
    check("模型看到提示后收尾", res.ok and res.answer == "done", res.to_dict())

    # 自述不足：模型说"没有把文字变成声音的能力" → 推断 tts，再给一轮机会
    seen2 = []
    calls2 = {"n": 0}

    def chat2(messages, tools):
        calls2["n"] += 1
        if calls2["n"] == 1:
            return "我这里没有把文字变成声音的能力，做不到。", [], {}
        return "done2", [], {}

    res2 = al.run_agent(session_id="gap-self", user_input="读给我听",
                        chat_fn=chat2, tool_functions={}, persist=False,
                        on_capability_gap=lambda g: (seen2.append(g), "用 autonomy_install")[1])
    check("自述缺口被识别", bool(seen2) and seen2[0].kind == "self_report"
          and seen2[0].need == "tts", seen2)
    check("自述后多跑了一轮", calls2["n"] == 2 and res2.answer == "done2",
          (calls2, res2.to_dict()))

    print("=== ⑩ 回滚保留相对路径 ===")
    rroot = os.path.join(td, "roll-root")
    os.makedirs(os.path.join(rroot, "plugins"), exist_ok=True)
    open(os.path.join(rroot, "bot_config.json"), "w", encoding="utf-8").write('{"a":1}')
    open(os.path.join(rroot, "plugins", "personality.txt"), "w", encoding="utf-8").write("旧人格")
    rb = wire.snapshot_rollback(os.path.join(td, "roll-data"), rroot)
    open(os.path.join(rroot, "bot_config.json"), "w", encoding="utf-8").write('{"a":2}')
    open(os.path.join(rroot, "plugins", "personality.txt"), "w", encoding="utf-8").write("新人设")
    wire.restore_rollback(rb, rroot)
    check("配置回滚", open(os.path.join(rroot, "bot_config.json"), encoding="utf-8").read() == '{"a":1}')
    check("人格回滚到 plugins 下",
          open(os.path.join(rroot, "plugins", "personality.txt"), encoding="utf-8").read() == "旧人格")
    check("没有回滚到根目录", not os.path.exists(os.path.join(rroot, "personality.txt")))
    _id = os.path.join(td, "idem-data")
    wire.record_installed(_id, {"need": "tts", "kind": "model", "target": "x"})
    wire.record_installed(_id, {"need": "tts", "kind": "model", "target": "x"})
    check("安装记录去重", len(wire.read_installed(_id)) == 1, wire.read_installed(_id))

    print("=== ⑪ 接线失败回滚 ===")
    class FailWire(Fake):
        def wire(self, p, acquired, dd, root):
            return {"ok": False, "error": "接线炸了"}

    rroot2 = os.path.join(td, "wire-root")
    os.makedirs(rroot2, exist_ok=True)
    open(os.path.join(rroot2, "bot_config.json"), "w", encoding="utf-8").write('{"old":1}')
    fw = FailWire()
    rr = loop.run(g, data_dir=os.path.join(td, "wire-run"), root=rroot2, backend=fw)
    check("接线失败返回", rr.get("ok") is False and rr.get("step") == "wire", rr)
    check("配置回滚", open(os.path.join(rroot2, "bot_config.json"), encoding="utf-8").read() == '{"old":1}')
    check("接线失败清产物", not os.path.exists(os.path.join(td, "wire-run", "artifact.bin")))

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
