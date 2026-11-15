# -*- coding: utf-8 -*-
"""autonomy.tools —— 自主层作为 agent 工具组（设计 §10.4）。

只在分离态注册/注入：probe（看环境）、plan（定方案）、install（执行七段）。
产品态不自动开；要申请也得走人确认（1.6.x 再接）。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict

from . import acquire, breaker, loop, plan as plan_mod, probe as probe_mod, verify as verify_mod, wire as wire_mod
from .capability_gap import Gap

_GROUP = "autonomy"


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    try:
        from data_store import get_data_dir
        return Path(get_data_dir())
    except Exception:
        return _root() / "data"


class RealBackend:
    """真机后端：venv / 下载 / 自验 / 接线。只在分离态被 agent 触发。"""

    def probe_env(self, dd, net_check):
        return probe_mod.env_report(data_dir=dd, net_check=net_check,
                                    disk_paths=[str(dd), str(_root())])

    def make_plan(self, gap, env):
        return plan_mod.make_plan(gap, env, data_dir=_data_dir(),
                                  plugins_dir=_root() / "plugins")

    def acquire(self, art, dd):
        kind = art.get("kind")
        if kind == "pip":
            venv = Path(dd) / "autonomy" / "venv"
            if not (venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")).exists():
                r = acquire.make_venv(sys.executable, venv)
                if not r.get("ok"):
                    return r
            return acquire.pip_install(venv, str(art.get("name") or ""))
        if kind in ("model", "bin"):
            return acquire.download_resume(
                str(art.get("url") or ""), art.get("target"),
                http_get=_http_get,
                total_hint=int(art.get("size_bytes") or 0),
                expected_sha256=str(art.get("sha256") or ""))
        if kind == "plugin":
            return {"ok": False, "error": "插件产物走 plugin_forge，autonomy 不直接写"}
        return {"ok": False, "error": f"未知产物类型：{kind}"}

    def verify(self, art, result, dd):
        kind = art.get("kind")
        if kind == "pip":
            return _verify_pip(Path(dd) / "autonomy" / "venv", str(art.get("name") or ""))
        if kind == "bin":
            return verify_mod.verify_bin(art.get("target"), expect="ffmpeg"
                                         if "ffmpeg" in str(art.get("name") or "").lower()
                                         else "")
        if kind == "model":
            return verify_mod.verify_file(art.get("target"), min_bytes=1)
        return {"ok": bool((result or {}).get("ok"))}

    def wire(self, p, acquired, dd, root):
        records = []
        for item in acquired:
            art = item["artifact"]
            records.append(wire_mod.record_installed(dd, {
                "need": p.get("need"), "kind": art.get("kind"),
                "name": art.get("name"), "target": art.get("target"),
                "source_url": art.get("url", ""), "reason": p.get("reason", ""),
            }))
        return {"ok": True, "records": records}


def _http_get(url: str, headers: Dict[str, str]):
    req = urllib.request.Request(url, headers={
        "User-Agent": "EggBloom/1.6 (+autonomy)",
        **(headers or {}),
    })
    r = urllib.request.urlopen(req, timeout=120)
    status = int(getattr(r, "status", 200))
    def gen():
        while True:
            chunk = r.read(4 * 1024 * 1024)
            if not chunk:
                break
            yield chunk
    return status, gen()


def _verify_pip(venv_dir: Path, package: str) -> Dict[str, Any]:
    py = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.exists():
        return {"ok": False, "error": "venv 不存在"}
    import subprocess
    try:
        p = subprocess.run([str(py), "-m", "pip", "show", str(package)],
                           capture_output=True, text=True, timeout=60)
        return {"ok": p.returncode == 0, "stdout": (p.stdout or "")[:400]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_probe(need: str = "", **_kw) -> str:
    dd = _data_dir()
    env = probe_mod.env_report(data_dir=dd, disk_paths=[str(dd), str(_root())])
    path = probe_mod.write_report(env, dd)
    return f"环境报告已落盘：{path}\n" + json.dumps(env, ensure_ascii=False)[:1200]


def tool_plan(need: str = "", kind: str = "", package: str = "", url: str = "",
              sha256: str = "", size_bytes: int = 0, **_kw) -> str:
    dd = _data_dir()
    g = Gap("self_report", str(need or "unknown"), "agent requested")
    env = probe_mod.env_report(data_dir=dd, disk_paths=[str(dd), str(_root())])
    p = plan_mod.make_plan(g, env, data_dir=dd, plugins_dir=_root() / "plugins",
                           kind=kind, package=package, url=url, sha256=sha256,
                           size_bytes=int(size_bytes or 0))
    return plan_mod.render(p) + "\n" + json.dumps(p, ensure_ascii=False)[:1200]


def tool_install(need: str = "", kind: str = "", package: str = "", url: str = "",
                 sha256: str = "", size_bytes: int = 0, **_kw) -> str:
    if not str(need or "").strip():
        return "[错误] 需要具体能力名（例如 tts / asr / ocr）"
    argv = ["--need", str(need), "--kind", str(kind or ""),
            "--package", str(package or ""), "--url", str(url or ""),
            "--sha256", str(sha256 or ""), "--size-bytes", str(int(size_bytes or 0)),
            "--root", str(_root()), "--data", str(_data_dir()), "--json"]
    code = ("import sys; sys.path.insert(0, %r); "
            "from autonomy.runner import main; sys.exit(main(%r))"
            % (str(_root()), argv))
    try:
        from harness import tool_proc as tp
        return tp.job_start({"cmd": [sys.executable, "-u", "-c", code],
                             "label": "autonomy:" + str(need)[:24]})
    except Exception as e:
        return f"[错误] 启动自主安装失败：{type(e).__name__}: {e}"


def tool_status(**_kw) -> str:
    dd = _data_dir()
    inst = wire_mod.read_installed(dd)
    lines = [f"已安装记录 {len(inst)} 条"]
    for r in inst[-8:]:
        lines.append(f"  - {r.get('need')} {r.get('kind')} → {r.get('target')}")
    blocked = {}
    try:
        p = Path(dd) / "autonomy" / "blocked.json"
        if p.exists():
            blocked = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    if blocked:
        lines.append("熔断：" + "、".join(f"{k}({v.get('failures')}次)" for k, v in blocked.items()))
    return "\n".join(lines)


_SCHEMAS = [
    {"type": "function", "function": {
        "name": "autonomy_probe",
        "description": "探测本机环境（Python/磁盘/网络/已装），落盘 EnvReport。装任何东西前先用。",
        "parameters": {"type": "object", "properties": {
            "need": {"type": "string", "description": "缺的能力名，例如 tts"}},
            "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "autonomy_plan",
        "description": "把能力缺口变成可执行计划：产物类型、落点、磁盘预检。不下载。",
        "parameters": {"type": "object", "properties": {
            "need": {"type": "string"}, "kind": {"type": "string", "enum": ["pip", "model", "bin", "plugin"]},
            "package": {"type": "string"}, "url": {"type": "string"},
            "sha256": {"type": "string"}, "size_bytes": {"type": "integer"}},
            "required": ["need"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "autonomy_install",
        "description": "执行七段：探测→计划→获取→自验→接线。长任务；默认只在分离态可用。",
        "parameters": {"type": "object", "properties": {
            "need": {"type": "string"}, "kind": {"type": "string", "enum": ["pip", "model", "bin", "plugin"]},
            "package": {"type": "string"}, "url": {"type": "string"},
            "sha256": {"type": "string"}, "size_bytes": {"type": "integer"}},
            "required": ["need"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "autonomy_status",
        "description": "看已安装记录与熔断状态。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
]


def register() -> int:
    """注册到 tool_registry；重复调用幂等。返回注册数量。"""
    try:
        from harness import tool_registry as tr
    except Exception:
        return 0
    fns = {"autonomy_probe": tool_probe, "autonomy_plan": tool_plan,
           "autonomy_install": tool_install, "autonomy_status": tool_status}
    # tool_runner 统一用 func(args_dict) 调用；这里把 dict 展开成关键字参数
    fns = {n: (lambda a, _f=f: _f(**(a or {}))) for n, f in fns.items()}
    n = 0
    for s in _SCHEMAS:
        name = s["function"]["name"]
        try:
            tr.register(name, s, fn=fns[name], groups=(tr.GROUP_AUTONOMY,),
                        source="autonomy")
            n += 1
        except Exception:
            pass
    return n
