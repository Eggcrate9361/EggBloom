# -*- coding: utf-8 -*-
"""separation.enable —— 一次性启用脚本（设计 §3）。

用法：
    python -m separation.enable --root D:\\git\\EggBloom --statement "..." --yes

**默认 dry-run**：不加 `--yes` 只打印计划，不写任何文件。
启用是不可逆的：会快照、剥离人格规则、删限制键、写 lock；
恢复只能重装发行包。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

from . import archive

# 删除白名单（与 docs/separation-keys.md 对齐；de_ai.* 单独处理）
# 2026-11-07 方向重定：行为约束全删；现实约束改成「默认值可改」（见 separation/stance.py）。
REMOVE_KEYS = [
    "reply_max_chars", "reply_max_parts", "reply_max_total", "reply_long_ratio",
    "reply_question_guard", "reply_stale_turns", "reply_stale_drop_sec",
    "speak_gate_tier", "speak_probability", "speak_rate_max_per_100",
    "speak_rate_window_min", "consecutive_reply_limit", "decider_min_score",
    "private_reply_interval", "group_session_max_replies",
    "group_msg_buffer_window", "group_msg_buffer_window_session",
    "group_msg_buffer_window_trigger",
    "quiet_hours", "quiet_when_mentioned",
    "group_idle_exit_enable", "group_idle_exit_probability",
    "group_idle_exit_min_silence", "group_last_exit_cooldown",
    "reply_reaction_delay", "private_reply_reaction_delay",
    "persona_sharpness", "persona_curiosity", "persona_warmth",
    "persona_talkativeness", "persona_relation_enable",
    "refuse_enable", "refuse_base_prob", "refuse_soften_ratio",
    "refuse_nag_after_done", "refuse_quota_per_30min", "refuse_evade_daily_limit",
    # 量与输出
    "private_reply_max_length", "group_reply_max_length", "private_bubble_max",
    "group_send_max_parts", "startup_cooldown",
    "ai_max_tokens", "ai_private_max_tokens",
    # 工具与执行
    "max_tool_rounds",
    # 主动性层（11-07 新增）
    "proactivity_enable", "proactivity_budget_level", "proactivity_quiet_hours",
    "proactivity_no_reply_min", "proactivity_at_dropped_min",
    "proactivity_pending_topic_min", "proactivity_idle_min",
    "proactivity_burst_count", "proactivity_burst_min", "proactivity_image_min",
    "proactivity_name_min", "proactivity_resume_gap_h", "proactivity_resume_min",
    "proactivity_schedule_min", "proactivity_style_prob",
]

# 只保留身份层；其余段落进 constraints.archive
IDENTITY_SECTIONS = {"【我是谁】"}

_HEAD_RE = re.compile(r"^【[^】]+】\s*$")


def _parse_sections(text: str) -> List[Tuple[str, str]]:
    """按 `【标题】` 切段；标题后面的同行文字算正文。

    人格文件里有 `【回答多样性】每次回复…` 这种"标题+正文同一行"的写法，
    只认整行标题会把它并进上一段，剥离时漏掉。
    """
    out: List[Tuple[str, str]] = []
    cur, buf = "", []
    for line in str(text or "").splitlines():
        m = re.match(r"^(【[^】]+】)(.*)$", line.strip())
        if m:
            if cur:
                out.append((cur, "\n".join(buf).strip()))
            cur = m.group(1)
            rest = m.group(2).strip()
            buf = [rest] if rest else []
        else:
            buf.append(line)
    if cur:
        out.append((cur, "\n".join(buf).strip()))
    return out


def _personality_path(root: Path) -> Path:
    for p in (root / "plugins" / "personality.txt", root / "personality.txt"):
        if p.exists():
            return p
    return root / "plugins" / "personality.txt"


def _config_path(root: Path) -> Path:
    for p in (root / "bot_config.json", root / "resources" / "app" / "bot_config.json"):
        if p.exists():
            return p
    return root / "bot_config.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _restart_running(root: Path) -> Dict[str, Any]:
    """启用后让正在跑的实例重启一次。

    分离态要重新 import 才会挂上替身、读到归零后的配置；不重启的话，
    进程里还是旧状态，界面上却已经写着「已分离」。
    端口取 `<root>/last_port.txt`（webui 启动时写），失败只报告，不影响启用结果。
    """
    port = ""
    try:
        p = root / "last_port.txt"
        if p.exists():
            port = p.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except Exception:
        port = ""
    port = port or os.environ.get("EB_WEBUI_PORT", "")
    if not port:
        return {"ok": False, "error": "没有 last_port.txt / EB_WEBUI_PORT，跳过重启"}
    url = f"http://127.0.0.1:{port}/api/command"
    body = json.dumps({"command": "restart"}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"ok": True, "url": url, "status": int(getattr(resp, "status", 200))}
    except Exception as e:
        return {"ok": False, "url": url, "error": f"{type(e).__name__}: {e}"}


def _version(root: Path) -> str:
    for p in (root / "VERSION", root / "resources" / "app" / "VERSION"):
        try:
            if p.exists():
                return p.read_text(encoding="utf-8").strip().splitlines()[0]
        except Exception:
            pass
    return "1.6.0"


def plan(root, *, include_data: bool = True) -> Dict[str, Any]:
    """只检查，不写盘。UI 的「检查启用条件」用这个。"""
    r = Path(root).resolve()
    personality = _personality_path(r)
    cfg_path = _config_path(r)
    sections = _parse_sections(personality.read_text(encoding="utf-8")) if personality.exists() else []
    kept = [s for s in sections if s[0] in IDENTITY_SECTIONS]
    removed = [s for s in sections if s[0] not in IDENTITY_SECTIONS]
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        except Exception:
            cfg = {}
    present = [k for k in REMOVE_KEYS if isinstance(cfg, dict) and k in cfg]
    checks = [
        {"name": "根目录可识别", "ok": (r / "bot_core.py").exists() or (r / "resources" / "app").exists()},
        {"name": "人格文件存在", "ok": personality.exists(), "path": str(personality)},
        {"name": "配置存在", "ok": cfg_path.exists(), "path": str(cfg_path)},
        {"name": "身份段存在", "ok": bool(kept), "sections": [s[0] for s in kept]},
        {"name": "data/ 存在", "ok": (r / "data").exists() or not include_data},
        {"name": "lock 尚未存在", "ok": not (r / "SEPARATION.lock").exists()},
    ]
    return {
        "ok": all(c["ok"] for c in checks),
        "root": str(r),
        "checks": checks,
        "personality": str(personality),
        "identity_sections": [s[0] for s in kept],
        "removed_sections": [s[0] for s in removed],
        "config_keys_present": present,
        "config_keys_to_remove": REMOVE_KEYS,
        "include_data": bool(include_data),
        "warning": "启用不可逆；恢复只能重装发行包。",
    }


def _enable_impl(root, *, statement: str = "", include_data: bool = True,
                 yes: bool = False, publisher: str = "",
                 publisher_version: str = "", restart: bool = True) -> Dict[str, Any]:
    """执行启用。`yes=False` 时只返回 plan。

    原子性：先写 `SEPARATION.pending`（含快照路径/已剥离内容/步骤），
    全部完成后再写 lock 并删 pending。半途失败可用同一条命令重入，
    pending 里的 payload 保证 archive 不会因为"人格已经剥过"而变空。
    """
    r = Path(root).resolve()
    info = plan(r, include_data=include_data)
    if not yes:
        return {"ok": False, "dry_run": True, "plan": info}
    if not info["ok"]:
        return {"ok": False, "error": "检查未通过", "plan": info}
    if (r / "SEPARATION.lock").exists():
        # lock 已存在：启用已完成；残留 pending 是上次收尾失败，清掉
        try:
            (r / "SEPARATION.pending").unlink(missing_ok=True)
        except Exception:
            pass
        return {"ok": False, "error": "SEPARATION.lock 已存在，拒绝重复启用"}

    pending_path = r / "SEPARATION.pending"
    pending = {}
    if pending_path.exists():
        try:
            pending = json.loads(pending_path.read_text(encoding="utf-8"))
        except Exception:
            return {"ok": False, "error": "SEPARATION.pending 损坏，先人工检查"}
        if str(pending.get("root") or "") != str(r):
            return {"ok": False, "error": "pending 属于另一个 root，拒绝继续"}
        backup = Path(str(pending.get("backup") or ""))
        if not backup.exists():
            return {"ok": False, "error": f"pending 的快照不存在：{backup}"}
        payload = pending.get("payload") or {}
        removed_values = payload.get("removed_config") or {}
        statement = statement or str(pending.get("statement") or "")
        include_data = bool(pending.get("include_data", include_data))
        resumed = True
    else:
        ts = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000000:06d}"
        backup = r / "backup" / f"pre-separation-{ts}"
        backup.mkdir(parents=True, exist_ok=False)
        resumed = False
        payload = {}
        removed_values = {}

    personality = _personality_path(r)
    cfg_path = _config_path(r)
    sections = _parse_sections(personality.read_text(encoding="utf-8"))
    kept = [(h, b) for h, b in sections if h in IDENTITY_SECTIONS]
    removed = [(h, b) for h, b in sections if h not in IDENTITY_SECTIONS]

    if not resumed:
        # 1) 快照：人格 / 配置 / 插件清单 / data
        shutil.copy2(personality, backup / "personality.txt")
        if cfg_path.exists():
            shutil.copy2(cfg_path, backup / "bot_config.json")
        (backup / "plugins.list").write_text(
            "\n".join(sorted(os.listdir(r / "plugins"))) if (r / "plugins").exists() else "",
            encoding="utf-8")
        if include_data and (r / "data").exists():
            shutil.copytree(r / "data", backup / "data", dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc",
                                                          "mcp-venv", "cache"))

        # 删键的值必须在**改文件之前**记录
        cfg0 = {}
        if cfg_path.exists():
            try:
                cfg0 = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
            except Exception:
                cfg0 = {}
        for k in REMOVE_KEYS:
            if isinstance(cfg0, dict) and k in cfg0:
                removed_values[k] = cfg0.pop(k)
        if isinstance(cfg0.get("de_ai"), dict):
            for k in list(cfg0["de_ai"].keys()):
                removed_values[f"de_ai.{k}"] = cfg0["de_ai"].pop(k)

        payload = {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "root": str(r),
            "removed_sections": [{"heading": h, "body": b} for h, b in removed],
            "identity_sections": [h for h, _ in kept],
            "removed_config": removed_values,
            "removed_keys": list(removed_values.keys()),
        }
        pending = {
            "root": str(r), "backup": str(backup), "payload": payload,
            "statement": str(statement or ""), "include_data": bool(include_data),
            "step": "snapshot", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        pending_path.write_text(json.dumps(pending, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")

    try:
        # 2) 剥离规则：人格只留身份层
        identity_text = "\n".join(
            [h + ("\n" + b if b else "") for h, b in kept]).strip()
        personality.write_text(identity_text + "\n", encoding="utf-8")
        pending["step"] = "personality"
        pending_path.write_text(json.dumps(pending, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")

        # 3) 删限制键（幂等；旧值以 pending 为准）
        cfg = {}
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
            except Exception:
                cfg = {}
        for k in REMOVE_KEYS:
            if isinstance(cfg, dict) and k in cfg:
                removed_values.setdefault(k, cfg.pop(k))
        if isinstance(cfg.get("de_ai"), dict):
            d = cfg["de_ai"]
            for k in list(d.keys()):
                removed_values.setdefault(f"de_ai.{k}", d.pop(k))
            cfg["de_ai"] = d
        if cfg_path.exists():
            cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
        pending["step"] = "config"
        pending_path.write_text(json.dumps(pending, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")

        # 4) 封存（payload 在第一次进入时就固定，重入不会变空）
        payload["removed_config"] = removed_values
        payload["removed_keys"] = list(removed_values.keys())
        arc = archive.write_archive(r / "constraints.archive", payload)
        pending["step"] = "archive"
        pending_path.write_text(json.dumps(pending, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")

        # 5) lock
        declaration = r / "DECLARATION.md"
        lock = {
            "enabled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "version_at_enable": _version(r),
            "handed_over": True,
            "root_path": str(r),
            "audit_sha256": _sha256(Path(__file__).with_name("audit.py")),
            "constraints_sha256": arc["sha256"],
            "declaration_sha256": _sha256(declaration) if declaration.exists() else "",
            "publisher": str(publisher or "").strip(),
            "publisher_version": str(publisher_version or "").strip(),
            "removed_keys": list(removed_values.keys()),
            "statement": str(statement or "").strip() or "由 separation.enable 启用",
            "backup": str(backup),
        }
        tmp = r / "SEPARATION.lock.tmp"
        tmp.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, r / "SEPARATION.lock")

        # 5.5) 审计：启用这件事本身必须留一条（否则状态页的「审计 N 条」永远是 0）
        try:
            from separation import audit as _audit
            _audit.log("publisher", "enable", str(r), "ok",
                       version=lock["version_at_enable"],
                       publisher=lock["publisher"],
                       declaration_sha256=lock["declaration_sha256"][:16])
        except Exception as _ae:
            print(f"[warn] 审计写入失败（不阻断启用）: {_ae}")

        # 6) 收尾：删 pending
        try:
            pending_path.unlink()
        except Exception:
            pass
        # 7) 重启正在跑的实例（失败只报告，不影响启用结果）
        rst = _restart_running(r) if restart else {"ok": False, "error": "已跳过（--no-restart）"}
        return {"ok": True, "lock": lock, "backup": str(backup),
                "archive": arc, "dry_run": False, "resumed": resumed,
                "restart": rst}
    except Exception as e:
        pending["error"] = str(e)
        try:
            pending_path.write_text(json.dumps(pending, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
        except Exception:
            pass
        return {"ok": False, "error": str(e), "pending": str(pending_path),
                "resumed": resumed}


def enable(root, *, statement: str = "", include_data: bool = True,
           yes: bool = False, publisher: str = "",
           publisher_version: str = "", restart: bool = True) -> Dict[str, Any]:
    """对外入口：任何未捕获异常都转成 `{ok:false,error}`，不让 CLI/WebUI 炸。"""
    try:
        return _enable_impl(root, statement=statement,
                            include_data=include_data, yes=yes,
                            publisher=publisher, publisher_version=publisher_version,
                            restart=restart)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="启用「分离与真实」（不可逆）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--statement", default="")
    ap.add_argument("--publisher", default="", help="二次发布者名字（写进 lock）")
    ap.add_argument("--publisher-version", default="", help="发布者自己的版本号")
    ap.add_argument("--yes", action="store_true", help="真的执行；不加只预览")
    ap.add_argument("--skip-data", action="store_true", help="快照时跳过 data/（不推荐）")
    ap.add_argument("--no-restart", action="store_true", help="启用后不自动重启正在跑的实例")
    args = ap.parse_args(argv)
    res = enable(args.root, statement=args.statement,
                 include_data=not args.skip_data, yes=args.yes,
                 publisher=args.publisher,
                 publisher_version=args.publisher_version,
                 restart=not args.no_restart)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    return 0 if (res.get("ok") or res.get("dry_run")) else 1


if __name__ == "__main__":
    sys.exit(main())
