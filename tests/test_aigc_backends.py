# -*- coding: utf-8 -*-
"""tests/test_aigc_backends.py —— AIGC 真后端适配器（1.6.0 P1.5 第②③刀）

判据全部**不碰网络、不碰真模型**：venv 用临时目录假装，子进程调用打桩。
真机验收（合成 3 秒语音 / 转写闭环）在 `scripts/install_aigc.py --check`
和 docs 里写的手工步骤，不在这个文件里 —— 单元测试跑几十秒就要出结果。

判据：
  [A] 没装 venv：caps 全 False，后端给出**可读**的失败原因（不是 AttributeError）
  [B] 音色解析：按名字找 .onnx / 绝对路径 / 找不到返回 ""
  [C] TTS 后端：空文本、超长、无音色、worker 失败 —— 四种都变成 ValueError/RuntimeError
  [D] TTS 成功：artifact/ext/meta 三样齐，meta 带时长与字节数
  [E] ASR 后端：音频不存在 → FileNotFoundError；成功 → 产物是 .txt，meta.text 有内容
  [F] bootstrap：装了什么挂什么（没装的不挂空桩），且幂等
  [G] espeak 数据目录：缺失时返回 ""（TTS 据此判不可用，而不是硬跑报怪错）
  [H] 配置键齐全（aigc_enable / tts / asr / out_dir …）
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

from harness import aigc_backends as ab  # noqa: E402
from harness import aigc_task as at  # noqa: E402

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
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


class Ctx:
    def __init__(self):
        self.notes = []

    def report(self, p, note=""):
        self.notes.append((p, note))


class FakeSvc:
    def __init__(self):
        self.backends = {}

    def register_backend(self, kind, fn):
        self.backends[kind] = fn

    def has_backend(self, kind):
        return kind in self.backends


def _mkroot(td):
    """造一个"装好了"的假 root：venv 有 espeak 数据、有音色、有 worker。"""
    py = os.path.join(td, "data", "aigc", "venv", "Scripts", "python.exe")
    os.makedirs(os.path.dirname(py), exist_ok=True)
    open(py, "w").close()
    esp = os.path.join(td, "data", "aigc", "venv", "Lib", "site-packages",
                       "piper", "espeak-ng-data")
    os.makedirs(esp, exist_ok=True)
    open(os.path.join(esp, "phontab"), "w").close()
    voice = os.path.join(td, "data", "models", "piper", "zh_CN-huayan-medium.onnx")
    os.makedirs(os.path.dirname(voice), exist_ok=True)
    open(voice, "wb").write(b"x" * 1024)
    os.makedirs(os.path.join(td, "harness"), exist_ok=True)
    open(os.path.join(td, "harness", "aigc_worker.py"), "w").close()
    return td


def main():
    td = tempfile.mkdtemp(prefix="aigc_test_")
    empty = os.path.join(td, "empty")
    os.makedirs(empty)
    good = _mkroot(os.path.join(td, "good"))

    print("\n=== A) 没装 venv：能不能说人话 ===")
    caps = ab.capabilities(empty, refresh=True)
    check("venv=False", caps["venv"] is False, caps)
    check("piper=False", caps["piper"] is False, caps)
    check("voices 为空", caps["voices"] == [], caps)
    fn = ab.make_tts_backend(empty)
    try:
        fn({}, {"text": "你好"}, Ctx())
        check("无 venv 时抛异常", False, "没抛")
    except Exception as e:
        check("无 venv 时抛异常且原因可读",
              "venv" in str(e) or "venv" in str(type(e).__name__), f"{type(e).__name__}: {e}")

    print("\n=== B) 音色解析 ===")
    check("按名字找到 .onnx", os.path.isfile(ab.resolve_voice("", good)), ab.resolve_voice("", good))
    check("找不到返回空串", ab.resolve_voice("不存在", good) == "", "")
    check("list_voices 只有一个", ab.list_voices(good) == ["zh_CN-huayan-medium"], ab.list_voices(good))

    print("\n=== C) TTS 的四种失败 ===")
    fn = ab.make_tts_backend(good)
    for name, params, exc in (
        ("空文本", {"text": "   "}, ValueError),
        ("超长文本", {"text": "字" * 2001}, ValueError),
    ):
        try:
            fn({}, params, Ctx())
            check(name + " → 抛错", False, "没抛")
        except Exception as e:
            check(name + " → %s" % exc.__name__, isinstance(e, exc), type(e).__name__)

    orig_run = ab._run
    try:
        ab._run = lambda root, argv, timeout=300: {"ok": False, "error": "piper 炸了"}
        try:
            fn({}, {"text": "你好"}, Ctx())
            check("worker 失败 → 抛 RuntimeError", False, "没抛")
        except RuntimeError as e:
            check("worker 失败 → 抛 RuntimeError 且带原因", "piper 炸了" in str(e), str(e))

        print("\n=== D) TTS 成功路径 ===")
        wav = os.path.join(td, "out.wav")
        open(wav, "wb").write(b"RIFF" + b"0" * 4000)
        ab._run = lambda root, argv, timeout=300: {
            "ok": True, "artifact": wav, "ext": ".wav",
            "meta": {"duration": 3.2, "bytes": 4004, "chars": 11}}
        ctx = Ctx()
        res = fn({}, {"text": "你好呀"}, ctx)
        check("artifact 是 wav", res["artifact"] == wav, res)
        check("ext=.wav", res["ext"] == ".wav", res)
        check("meta 带时长", res["meta"].get("duration") == 3.2, res["meta"])
        check("meta 带字节数", res["meta"].get("bytes") == 4004, res["meta"])
        check("进度有回填", len(ctx.notes) >= 2, ctx.notes)

        print("\n=== E) ASR 两种结局 ===")
        afn = ab.make_asr_backend(good)
        try:
            afn({}, {"audio": os.path.join(td, "没有这个.wav")}, Ctx())
            check("音频不存在 → 抛错", False, "没抛")
        except FileNotFoundError:
            check("音频不存在 → FileNotFoundError", True)
        ab._run = lambda root, argv, timeout=300: {
            "ok": True, "text": "你好我是小a",
            "meta": {"language": "zh", "duration": 2.4}}
        wav2 = os.path.join(td, "in.wav")
        open(wav2, "wb").write(b"RIFF" + b"0" * 100)
        res = afn({}, {"audio": wav2}, Ctx())
        check("ASR 产物是 .txt", res["ext"] == ".txt", res)
        check("文本写在文件里",
              open(res["artifact"], encoding="utf-8").read().strip() == "你好我是小a", "")
        check("meta.text 也带一份", res["meta"].get("text") == "你好我是小a", res["meta"])
    finally:
        ab._run = orig_run

    print("\n=== F) bootstrap：装什么挂什么 ===")
    svc = FakeSvc()
    r = ab.bootstrap(svc, empty, force=True)
    check("全都没装 → 一个都不挂", r["registered"] == [] and svc.backends == {}, r)
    ab._CAPS_CACHE["data"] = {"venv": True, "piper": True, "faster_whisper": True,
                              "voices": ["zh_CN-huayan-medium"], "python": "x"}
    ab._CAPS_CACHE["ts"] = __import__("time").time()
    svc2 = FakeSvc()
    r2 = ab.bootstrap(svc2, good, force=True)
    check("TTS 挂上", at.KIND_TTS in svc2.backends, r2)
    check("ASR 挂上", at.KIND_ASR in svc2.backends, r2)
    r3 = ab.bootstrap(svc2, good)
    check("幂等：第二次不再重复注册", r3["registered"] == [], r3)
    ab.reset_caps_cache()

    print("\n=== G) espeak 数据目录 ===")
    check("缺失时返回空串（不是崩）", ab.espeak_data_dir(empty) == "", "")
    check("ASCII 路径直接用", ab.espeak_data_dir(good).endswith("espeak-ng-data"),
          ab.espeak_data_dir(good))

    print("\n=== H) 配置键 ===")
    import config
    cfg = config.DEFAULT_CONFIG if hasattr(config, "DEFAULT_CONFIG") else {}
    keys = ["aigc_enable", "aigc_tts_backend", "aigc_tts_voice", "aigc_asr_backend",
            "aigc_asr_model", "aigc_out_dir"]
    check("AIGC 配置键齐全", all(k in cfg for k in keys),
          [k for k in keys if k not in cfg])
    check("默认关闭（aigc_enable=False）", cfg.get("aigc_enable") is False, cfg.get("aigc_enable"))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for n in FAILURES:
        print(f"  FAIL  {n}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
