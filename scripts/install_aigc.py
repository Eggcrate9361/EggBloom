# -*- coding: utf-8 -*-
"""scripts/install_aigc.py —— 一条命令把 AIGC 依赖与权重装齐（1.6.0 P1.5）

    python scripts/install_aigc.py                 # venv 依赖 + 中文音色（TTS）
    python scripts/install_aigc.py --asr base      # 再装一个 ASR 模型（tiny/base/small）
    python scripts/install_aigc.py --check         # 只体检，不下载

它在做什么（以及为什么这么做）：

1. **独立 venv**：`data/aigc/venv`。本体解释器一个包都不装 —— 这是
   1.6.0 开工流程 §7 的红线（本体 `pip list` 必须不变）。
2. **pip 源用官方 pypi.org**：清华源对 pip 26 的 UA 返回 403（`from versions: none`），
   踩过一次，别再照旧笔记换源。
3. **音色**：piper 的 `zh_CN-huayan-medium`（.onnx 63MB + .json）。
   新版 piper（1.8）**不会**按名字自动下载，必须自己拉这两个文件。
4. **espeak-ng 数据目录必须是 ASCII 路径**：espeak 是 C 实现、窄字符 fopen，
   部署目录里有中文就报 "Illegal byte sequence"，而报错信息伪装成
   `D:/a/piper1-gpl/...` 那种构建机路径（极具误导性）。装的时候就复制好，
   运行期只读。
5. **ASR 权重走 hf-mirror**：huggingface.co 国内不通。同时关掉 hf_xet
   （走 xet 更慢，实测 ~60KB/s）。

装完 `harness/aigc_backends.capabilities()` 会自己发现（5 分钟缓存）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(ROOT, "data", "aigc", "venv")
MODELS = os.path.join(ROOT, "data", "models")
PIPER_DIR = os.path.join(MODELS, "piper")
WHISPER_DIR = os.path.join(MODELS, "whisper")

PIPER_VOICE = "zh_CN-huayan-medium"
PIPER_BASE = ("https://hf-mirror.com/rhasspy/piper-voices/resolve/main/"
              "zh/zh_CN/huayan/medium")
HF = "https://hf-mirror.com"
ASR_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
}


def say(msg):
    print(msg, flush=True)


def venv_python():
    for rel in (("Scripts", "python.exe"), ("bin", "python")):
        p = os.path.join(VENV, *rel)
        if os.path.isfile(p):
            return p
    return ""


def ensure_venv():
    py = venv_python()
    if py:
        return py
    say("[1/4] 建 venv: " + VENV)
    subprocess.check_call([sys.executable, "-m", "venv", VENV])
    return venv_python()


def pip_install(py):
    say("[2/4] 装 piper-tts + faster-whisper（官方源，清华源会 403）")
    subprocess.check_call([py, "-m", "pip", "install", "--upgrade", "pip",
                           "-i", "https://pypi.org/simple", "-q"])
    subprocess.check_call([py, "-m", "pip", "install", "piper-tts", "faster-whisper",
                           "-i", "https://pypi.org/simple"])


def _download(url, dst, min_size=1024):
    if os.path.isfile(dst) and os.path.getsize(dst) >= min_size:
        say("    已存在，跳过: " + os.path.basename(dst))
        return True
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    say("    下载 " + os.path.basename(dst) + " ← " + url.split("/resolve/")[0])
    tmp = dst + ".part"
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            step = 8 * 1024 * 1024
            mark = step
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if done >= mark:
                    pct = f" {done * 100 // total}%" if total else ""
                    say(f"      {done // 1048576} MB{pct}")
                    mark += step
        os.replace(tmp, dst)
        return os.path.getsize(dst) >= min_size
    except Exception as e:
        say("    失败: %r" % (e,))
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def install_tts():
    say("[3/4] 中文音色（piper " + PIPER_VOICE + "）")
    ok1 = _download(f"{PIPER_BASE}/{PIPER_VOICE}.onnx",
                    os.path.join(PIPER_DIR, PIPER_VOICE + ".onnx"), 10 << 20)
    ok2 = _download(f"{PIPER_BASE}/{PIPER_VOICE}.onnx.json",
                    os.path.join(PIPER_DIR, PIPER_VOICE + ".onnx.json"), 500)
    _write_installed("piper", [PIPER_VOICE + ".onnx", PIPER_VOICE + ".onnx.json"],
                     {"voice": PIPER_VOICE})
    return ok1 and ok2


def install_espeak(py):
    """espeak-ng 数据目录 → ASCII 路径（运行期只读这里）。"""
    src = os.path.join(VENV, "Lib", "site-packages", "piper", "espeak-ng-data")
    if not os.path.isdir(src):
        say("    [跳过] venv 里没有 espeak-ng-data（piper 未装好？）")
        return False
    try:
        src.encode("ascii")
        say("    venv 路径本身是 ASCII，直接用: " + src)
        return True
    except UnicodeEncodeError:
        pass
    dst = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                       "EggBloom_aigc", "espeak-ng-data")
    if os.path.isfile(os.path.join(dst, "phontab")):
        say("    已就绪: " + dst)
        return True
    say("    复制到 ASCII 路径: " + dst)
    try:
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
        return os.path.isfile(os.path.join(dst, "phontab"))
    except Exception as e:
        say("    失败: %r" % (e,))
        return False


def install_asr(model):
    repo = ASR_REPOS.get(model)
    if not repo:
        say("    未知模型: %s（可选 %s）" % (model, "/".join(ASR_REPOS)))
        return False
    say("[4/4] ASR 模型 " + model + "（" + repo + "，走 hf-mirror）")
    base = f"{HF}/{repo}/resolve/main/"
    dst = os.path.join(WHISPER_DIR, model)
    files = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]
    ok = True
    for fn in files:
        ok = _download(base + fn, os.path.join(dst, fn), 100) and ok
    if ok:
        _write_installed("whisper", files, {"model": model})
    return ok


def _write_installed(family, files, extra=None):
    d = os.path.join(MODELS, family)
    os.makedirs(d, exist_ok=True)
    payload = {"family": family, "files": files, "installed_at": _now()}
    payload.update(extra or {})
    with open(os.path.join(d, "_installed.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _now():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def check():
    sys.path.insert(0, ROOT)
    from harness import aigc_backends as ab
    caps = ab.capabilities(refresh=True)
    say(json.dumps(caps, ensure_ascii=False, indent=2))
    ok = caps.get("venv") and caps.get("piper") and caps.get("voices")
    say("\n结论：" + ("TTS 可用" if ok else "TTS 不可用") +
        ("；ASR 可用" if caps.get("faster_whisper") else "；ASR 不可用"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="装配 EggBloom AIGC（TTS/ASR）")
    ap.add_argument("--asr", nargs="?", const="small", default="",
                    help="顺带装一个 ASR 模型：tiny/base/small，默认 small")
    ap.add_argument("--skip-pip", action="store_true")
    ap.add_argument("--check", action="store_true", help="只体检")
    args = ap.parse_args()

    if args.check:
        return check()

    py = ensure_venv()
    if not py:
        say("venv 建不起来，停。")
        return 1
    if not args.skip_pip:
        pip_install(py)
    tts_ok = install_tts()
    install_espeak(py)
    asr_ok = install_asr(args.asr) if args.asr else True
    say("\n完成：TTS=%s  ASR=%s" % (tts_ok, asr_ok if args.asr else "未装"))
    try:
        return check()
    except Exception as e:
        say("体检失败: %r" % (e,))
        return 0 if tts_ok else 1


if __name__ == "__main__":
    sys.exit(main())
