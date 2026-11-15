# -*- coding: utf-8 -*-
"""harness/aigc_backends.py —— AIGC 真后端适配器（1.6.0 P1.5 第②③刀）

## 这一层干什么

`aigc_task.py` 只管任务（状态机 / 产物目录 / 配额），**不知道模型怎么跑**。
本文件把三种真后端接到它的注册表上：

    tts  → piper（离线，中文音色 zh_CN-huayan-medium）
    asr  → faster-whisper（转写，默认 small）
    image→ ComfyUI（沿用 bot_core 既有链路，本层暂不重复实现）

## 为什么后端在子进程里跑

主进程是发行包自带的 python，**不装 AIGC 依赖**（红线见
docs/plan/1.6.0-开工流程.md §7：本体 `pip list` 必须不变）。
所以依赖装在 `data/aigc/venv`，调用统一走 `harness/aigc_worker.py`，
协议是单行 JSON（见那个文件的头注释）。

## 踩到的坑（写进代码，别再踩）

1. **espeak-ng 打不开非 ASCII 路径**。piper 合成中文要先过 espeak 音素化，
   而 espeak 是 C 实现、用窄字符 fopen —— 部署路径里有「水星」两个字就读不到
   `phontab`，报错还假装是 `D:/a/piper1-gpl/...` 那种构建机路径（误导性极强）。
   对策：数据目录放在 ASCII 路径下（必要时复制一份到 LOCALAPPDATA），
   用 `ESPEAK_DATA_PATH` 指过去。`.onnx` 模型走 onnxruntime，中文路径没事。
2. **pip 走清华源会 403**（pip 26 的 UA 被拦），官方 pypi.org 正常。
   只影响装机，不影响运行，但别再照着旧笔记换源。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

from harness import aigc_task

#: 默认中文音色（piper 的 zh_CN 只有一个 medium 音色，选它是因为唯一且够用）
DEFAULT_VOICE = "zh_CN-huayan-medium"
DEFAULT_ASR_MODEL = "small"
#: 首次跑 ASR 要从 HF 拉权重；国内直连 huggingface.co 不通，走镜像
DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"

_CAPS_CACHE = {"ts": 0.0, "data": None}
_CAPS_TTL = 300.0


# ── 路径 ────────────────────────────────────────────────────
def app_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def venv_dir(root: str = "") -> str:
    return os.path.join(root or app_root(), "data", "aigc", "venv")


def venv_python(root: str = "") -> str:
    """AIGC venv 的解释器；不存在返回 ""。"""
    base = venv_dir(root)
    for rel in (("Scripts", "python.exe"), ("bin", "python")):
        p = os.path.join(base, *rel)
        if os.path.isfile(p):
            return p
    return ""


def worker_file(root: str = "") -> str:
    return os.path.join(root or app_root(), "harness", "aigc_worker.py")


def models_dir(root: str = "") -> str:
    return os.path.join(root or app_root(), "data", "models")


def _is_ascii(p: str) -> bool:
    try:
        str(p).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _espeak_source(root: str = "") -> str:
    return os.path.join(venv_dir(root), "Lib", "site-packages", "piper", "espeak-ng-data")


def espeak_data_dir(root: str = "") -> str:
    """给 espeak 用的数据目录（**必须是 ASCII 路径**，理由见模块头）。

    优先用 venv 里那份（路径本来就 ASCII 时）；否则复制到
    `%LOCALAPPDATA%\\EggBloom_aigc\\espeak-ng-data` 再指过去。
    返回 "" 表示拿不到 —— 调用方据此判定"TTS 不可用"，而不是硬跑起来报怪错。
    """
    src = _espeak_source(root)
    if not os.path.isdir(src):
        return ""
    if _is_ascii(src):
        return src
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    dst = os.path.join(base, "EggBloom_aigc", "espeak-ng-data")
    if not os.path.isfile(os.path.join(dst, "phontab")):
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isdir(dst):
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst)
        except Exception:
            return ""
    return dst if os.path.isfile(os.path.join(dst, "phontab")) else ""


def resolve_voice(name: str = "", root: str = "") -> str:
    """音色名 → .onnx 绝对路径；空名用配置默认；找不到返回 ""。"""
    nm = str(name or "").strip() or DEFAULT_VOICE
    if nm.lower().endswith(".onnx"):
        return nm if os.path.isfile(nm) else ""
    p = os.path.join(models_dir(root), "piper", nm + ".onnx")
    return p if os.path.isfile(p) else ""


def list_voices(root: str = "") -> list:
    d = os.path.join(models_dir(root), "piper")
    try:
        return sorted(n[:-5] for n in os.listdir(d) if n.endswith(".onnx"))
    except OSError:
        return []


# ── 子进程调用 ──────────────────────────────────────────────
def _run(root: str, argv: list, timeout: float = 300.0) -> dict:
    """跑一次 worker，返回它那行 JSON。失败也返回 dict（带 ok=False），不抛。"""
    py = venv_python(root)
    if not py:
        return {"ok": False, "error": "AIGC venv 不存在（data/aigc/venv）"}
    wf = worker_file(root)
    if not os.path.isfile(wf):
        return {"ok": False, "error": f"worker 缺失: {wf}"}
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    esp = espeak_data_dir(root)
    if esp:
        env["ESPEAK_DATA_PATH"] = esp
        env["ESPEAKNG_DATA_PATH"] = esp
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        p = subprocess.run([py, wf] + list(argv), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=env, creationflags=flags)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"后端超时（{int(timeout)}s）"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    for line in reversed((p.stdout or "").strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return json.loads(line)
        except Exception:
            continue
    tail = ((p.stdout or "") + "\n" + (p.stderr or "")).strip()[-400:]
    return {"ok": False, "error": tail or f"worker 无输出（exit={p.returncode}）"}


def capabilities(root: str = "", *, refresh: bool = False) -> dict:
    """探测 venv 里装了什么（带 5 分钟缓存，页面轮询不会一直起子进程）。"""
    import time as _t
    now = _t.time()
    if (not refresh and _CAPS_CACHE["data"] is not None
            and now - _CAPS_CACHE["ts"] < _CAPS_TTL):
        return dict(_CAPS_CACHE["data"])
    py = venv_python(root)
    data = {"venv": bool(py), "python": py, "piper": False, "faster_whisper": False}
    if py:
        res = _run(root, ["probe"], timeout=60)
        data.update({k: v for k, v in res.items() if k in
                     ("piper", "faster_whisper", "python", "piper_error", "asr_error")})
    data["voices"] = list_voices(root)
    data["default_voice"] = DEFAULT_VOICE
    data["espeak_data"] = espeak_data_dir(root)
    _CAPS_CACHE["data"] = dict(data)
    _CAPS_CACHE["ts"] = now
    return data


def _write_installed(family: str, files: list, root: str = "", extra: dict = None) -> None:
    """`data/models/<family>/_installed.json` —— 装机凭据，页面与排障都看它。"""
    d = os.path.join(models_dir(root), family)
    try:
        os.makedirs(d, exist_ok=True)
        payload = {"family": family, "files": files, "at": _now_str()}
        payload.update(extra or {})
        with open(os.path.join(d, "_installed.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _now_str() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── 后端实现 ────────────────────────────────────────────────
def make_tts_backend(root: str = "", cfg_getter=None):
    """text → wav。params: {text, voice?, length_scale?}"""

    def _backend(task, params, ctx):
        text = str((params or {}).get("text") or "").strip()
        if not text:
            raise ValueError("缺少 text")
        if len(text) > 2000:
            raise ValueError("文本过长（上限 2000 字）")
        # 先查 venv 再查音色：什么都没装的时候，说"没有音色"会把人引到错的地方
        if not venv_python(root):
            raise RuntimeError("AIGC venv 不存在（data/aigc/venv）；"
                               "跑 python scripts/install_aigc.py")
        voice = resolve_voice((params or {}).get("voice") or "", root)
        if not voice:
            raise RuntimeError("没有可用的 piper 音色（data/models/piper/*.onnx）")
        if not espeak_data_dir(root):
            raise RuntimeError("espeak-ng 数据目录不可用（见 aigc_backends 模块头）")
        ctx.report(0.15, "准备文本")
        tmpd = tempfile.mkdtemp(prefix="aigc_tts_")
        txt = os.path.join(tmpd, "in.txt")
        out = os.path.join(tmpd, "out.wav")
        with open(txt, "w", encoding="utf-8") as f:
            f.write(text)
        ctx.report(0.35, "合成中")
        res = _run(root, ["tts", "--model", voice, "--text-file", txt, "--out", out,
                          "--length-scale", str((params or {}).get("length_scale") or 1.0)],
                   timeout=180)
        if not res.get("ok"):
            raise RuntimeError(res.get("error") or "piper 合成失败")
        ctx.report(0.95, "落盘")
        meta = dict(res.get("meta") or {})
        meta["voice"] = os.path.basename(voice)
        return {"artifact": res["artifact"], "ext": ".wav", "meta": meta}

    return _backend


def asr_model_ref(name: str = "", root: str = "") -> str:
    """ASR 模型引用：装好的本地目录优先，否则给模型名让 faster-whisper 自己下。

    为什么要这一步：`aigc_asr_model` 默认写的是名字（small），但装的时候可能
    只装了 base。名字直接喂给 faster-whisper 会**默默去联网下载**（国内还不通），
    表现成"卡住十几分钟"。所以：
      ① 配的那个型号装了 → 用它；
      ② 没装、但机器上**只装了一个** → 用那一个（本地为主场景下这是常态）；
      ③ 一个都没有 → 才把名字交给 faster-whisper 去下。
    选了哪一个会写进 meta（`model_dir`），不是黑箱。
    """
    nm = str(name or "").strip() or DEFAULT_ASR_MODEL
    wdir = os.path.join(models_dir(root), "whisper")
    exact = os.path.join(wdir, nm)
    if os.path.isfile(os.path.join(exact, "model.bin")):
        return exact
    try:
        installed = [d for d in os.listdir(wdir)
                     if os.path.isfile(os.path.join(wdir, d, "model.bin"))]
    except OSError:
        installed = []
    if len(installed) == 1:
        return os.path.join(wdir, installed[0])
    return nm


def make_asr_backend(root: str = "", cfg_getter=None):
    """wav → 文本。params: {audio, language?, model?}"""

    def _backend(task, params, ctx):
        audio = str((params or {}).get("audio") or "").strip()
        if not audio or not os.path.isfile(audio):
            raise FileNotFoundError(f"音频不存在: {audio}")
        model = asr_model_ref((params or {}).get("model") or "", root)
        mdir = os.path.join(models_dir(root), "whisper")
        os.makedirs(mdir, exist_ok=True)
        ctx.report(0.2, "加载模型（首次会下载权重）")
        res = _run(root, ["asr", "--audio", audio, "--model", model,
                          "--model-dir", mdir,
                          "--language", str((params or {}).get("language") or "zh"),
                          "--hf-endpoint", DEFAULT_HF_ENDPOINT],
                   timeout=1800)
        if not res.get("ok"):
            raise RuntimeError(res.get("error") or "faster-whisper 转写失败")
        text = str(res.get("text") or "")
        ctx.report(0.95, "写文本")
        tmpd = tempfile.mkdtemp(prefix="aigc_asr_")
        out = os.path.join(tmpd, "out.txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        meta = dict(res.get("meta") or {})
        meta["text"] = text
        meta["audio"] = os.path.basename(audio)
        # 用了哪个模型（目录或名字）要能看见 —— 见 asr_model_ref 的三档选择
        meta["model_ref"] = model
        return {"artifact": out, "ext": ".txt", "meta": meta}

    return _backend


def bootstrap(service=None, root: str = "", *, force: bool = False) -> dict:
    """把装好的后端挂到任务层。返回 {"registered": [...], "caps": {...}}。

    幂等：已经注册过的 kind 不重复注册（除 `force=True`）。
    装了什么挂什么 —— 没装的不挂，提交时由任务层给出"后端未安装"，
    而不是挂一个必然失败的桩（那会把"没装"和"跑挂了"混成一件事）。
    """
    svc = service or aigc_task.get_service()
    caps = capabilities(root)
    done = []
    if (force or not svc.has_backend(aigc_task.KIND_TTS)) and caps.get("piper"):
        if resolve_voice("", root):
            svc.register_backend(aigc_task.KIND_TTS, make_tts_backend(root))
            _write_installed("piper", [os.path.basename(resolve_voice("", root))],
                             root, {"voice": DEFAULT_VOICE})
            done.append(aigc_task.KIND_TTS)
    if (force or not svc.has_backend(aigc_task.KIND_ASR)) and caps.get("faster_whisper"):
        svc.register_backend(aigc_task.KIND_ASR, make_asr_backend(root))
        done.append(aigc_task.KIND_ASR)
    return {"registered": done, "caps": caps}


def reset_caps_cache() -> None:
    _CAPS_CACHE["data"] = None
    _CAPS_CACHE["ts"] = 0.0


__all__ = ["app_root", "venv_python", "espeak_data_dir", "resolve_voice",
           "list_voices", "capabilities", "make_tts_backend", "make_asr_backend",
           "bootstrap", "reset_caps_cache", "DEFAULT_VOICE", "DEFAULT_ASR_MODEL"]
