# -*- coding: utf-8 -*-
"""harness/aigc_worker.py —— 在 AIGC 独立 venv 里跑的后端 worker

## 为什么要有这个文件（而不是在主进程里 import piper）

主进程跑的是发行包自带的 python（`<app>/python/python.exe`），**不装任何 AIGC 依赖**。
1.6.0 的红线写在 docs/plan/1.6.0-开工流程.md §7：

> AIGC 依赖污染解释器 → 独立 venv（`data/aigc/venv`），本体 `pip list` 必须不变

所以 piper / faster-whisper 装在 `data/aigc/venv` 里，主进程通过**子进程**调用本文件。
好处：① 本体解释器干净；② 依赖崩了不拖死 bot；③ 换后端只需换 venv。

## 协议

stdin/stdout 都是 UTF-8 的 JSON 单行，**中文一律走文件**（命令行会被 Windows 代码页转坏）：

    <venv>/Scripts/python.exe harness/aigc_worker.py tts \
        --model <voice.onnx> --text-file <utf8.txt> --out <out.wav>
    <venv>/Scripts/python.exe harness/aigc_worker.py asr \
        --audio <in.wav> --model small --model-dir <dir> [--language zh]

成功：{"ok": true, "artifact": "<path>", "ext": ".wav", "meta": {...}}
失败：{"ok": false, "error": "..."}  → 退出码 1

这个文件**只用标准库 + 各自依赖的懒加载**：没装 piper 时也能跑 `asr`，反之亦然。
"""
import argparse
import json
import os
import sys
import traceback


def _emit(obj, code=0):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return code


def _setup_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def cmd_tts(args):
    from piper import PiperVoice, SynthesisConfig

    with open(args.text_file, encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        return _emit({"ok": False, "error": "文本为空"}, 1)

    out = os.path.abspath(args.out)
    d = os.path.dirname(out)
    if d:
        os.makedirs(d, exist_ok=True)

    voice = PiperVoice.load(args.model)
    cfg = SynthesisConfig(length_scale=float(args.length_scale or 1.0))
    import wave
    with wave.open(out, "wb") as wf:
        voice.synthesize_wav(text, wf, syn_config=cfg)
    with wave.open(out, "rb") as wf:
        frames, rate, ch = wf.getnframes(), wf.getframerate(), wf.getnchannels()
    dur = frames / float(rate or 1)
    return _emit({
        "ok": True, "artifact": out, "ext": ".wav",
        "meta": {"duration": round(dur, 2), "sample_rate": rate, "channels": ch,
                 "bytes": os.path.getsize(out), "chars": len(text),
                 "voice": os.path.basename(args.model)},
    })


def cmd_asr(args):
    from faster_whisper import WhisperModel

    if args.hf_endpoint:
        os.environ.setdefault("HF_ENDPOINT", args.hf_endpoint)
        os.environ.setdefault("HF_HUB_ENDPOINT", args.hf_endpoint)
    model = WhisperModel(
        args.model,
        device=args.device or "cpu",
        compute_type=args.compute_type or "int8",
        download_root=args.model_dir or None,
    )
    segments, info = model.transcribe(
        args.audio, language=(args.language or "zh"), beam_size=5, vad_filter=True)
    text = "".join(getattr(s, "text", "") for s in segments).strip()
    return _emit({
        "ok": True, "artifact": "", "text": text,
        "meta": {"language": getattr(info, "language", args.language),
                 "duration": round(float(getattr(info, "duration", 0.0) or 0.0), 2),
                 "chars": len(text), "model": args.model},
    })


def cmd_probe(args):
    """报告这个 venv 里到底装了什么（页面「语音」页签用它显示可用性）。"""
    out = {"ok": True}
    try:
        import piper  # noqa: F401
        out["piper"] = True
    except Exception as e:
        out["piper"] = False
        out["piper_error"] = f"{type(e).__name__}: {e}"
    try:
        import faster_whisper  # noqa: F401
        out["faster_whisper"] = True
    except Exception as e:
        out["faster_whisper"] = False
        out["asr_error"] = f"{type(e).__name__}: {e}"
    out["python"] = sys.version.split()[0]
    return _emit(out)


def main(argv=None):
    _setup_stdout()
    ap = argparse.ArgumentParser(description="EggBloom AIGC worker")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tts")
    p.add_argument("--model", required=True, help="音色 .onnx 路径")
    p.add_argument("--text-file", required=True, help="UTF-8 文本文件（中文别走命令行）")
    p.add_argument("--out", required=True)
    p.add_argument("--length-scale", default="1.0")
    p.set_defaults(fn=cmd_tts)

    p = sub.add_parser("asr")
    p.add_argument("--audio", required=True)
    p.add_argument("--model", default="small")
    p.add_argument("--model-dir", default="")
    p.add_argument("--language", default="zh")
    p.add_argument("--device", default="cpu")
    p.add_argument("--compute-type", default="int8")
    p.add_argument("--hf-endpoint", default="")
    p.set_defaults(fn=cmd_asr)

    p = sub.add_parser("probe")
    p.set_defaults(fn=cmd_probe)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as e:
        return _emit({"ok": False,
                      "error": f"{type(e).__name__}: {e}",
                      "trace": traceback.format_exc()[-800:]}, 1)


if __name__ == "__main__":
    sys.exit(main())
