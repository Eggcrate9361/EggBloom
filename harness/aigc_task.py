# -*- coding: utf-8 -*-
"""harness/aigc_task.py —— AIGC 统一任务层（1.6.0 P1.5 第①刀）

## 为什么要有这一层

生图、语音、视频三种产物，背后是三种完全不同的东西：ComfyUI 是
"提交 workflow → 轮询 history → 取图"的 HTTP 后端；piper 是本地进程吐 wav；
faster-whisper 是本地模型吃音频吐文本。**但它们对界面是同一件事**：

    提交 → 看进度 → 拿产物 → 能删掉

没有统一层的时候，每种后端都会各自演化出一套状态机、目录规则、配额账本，
三套之间必然漂移（本仓库已经吃过"两条链路各有一套请求头"的亏，见
`bot_core._llm_request_headers` 的注释）。

所以这一层**只管任务，不管模型**：

  · 状态机：`pending → running → done / failed`，另有 `rejected`（关掉/超额）
  · 产物：`data/media/<kind>/<yyyymmdd>/<task_id>.<ext>` + 同名 `.json` 元数据
  · 配额：每日张数 / 每日秒数，按**本地日期**滚动
  · 后端：注册表注入，测试用假后端，不碰网络也不碰磁盘模型

## 刻意的三条约束

① `aigc_enable` 为假时**一个请求都不发** —— 连后端函数都不调用（验收第 6 条）。
   不是"提交后立刻失败"，而是根本不进管道，否则关掉开关照样可能触发下载/计费。
② 时钟可注入（`clock=`）—— 不用真实时间，配额滚动与日期目录才能确定性测试。
③ 任务状态只在这里改；后端只能通过 `ctx.report()` 汇报进度、通过返回值交产物。
   后端直接写任务字段会让"谁改的"永远查不清。
"""
from __future__ import annotations

import datetime
import json
import os
import threading
import time
import uuid

# ── 种类 ────────────────────────────────────────────────────
KIND_IMAGE = "image"
KIND_TTS = "tts"
KIND_ASR = "asr"
KIND_VIDEO = "video"
KINDS = (KIND_IMAGE, KIND_TTS, KIND_ASR, KIND_VIDEO)

# ── 状态 ────────────────────────────────────────────────────
ST_PENDING = "pending"
ST_RUNNING = "running"
ST_DONE = "done"
ST_FAILED = "failed"
ST_REJECTED = "rejected"

TERMINAL = (ST_DONE, ST_FAILED, ST_REJECTED)

#: 各 kind 的默认扩展名（后端可以覆盖）
DEFAULT_EXT = {
    KIND_IMAGE: ".png",
    KIND_TTS: ".wav",
    KIND_ASR: ".txt",
    KIND_VIDEO: ".mp4",
}

#: 配额键：kind → 配置里"每日上限"的名字
_QUOTA_KEY = {
    KIND_IMAGE: "aigc_daily_image_quota",
    KIND_TTS: "aigc_daily_tts_seconds",
}


def read_cfg_dict(raw_getter):
    """把本层用到的配置项集中成**一处字面读取**。

    为什么要有这个函数（而不是到处 `cfg.get(动态键)`）：
      `scripts/export_config_docs.py` 统计"某键有几处代码引用"时只认
      `cfg.get("键名")` / `cfg["键名"]` 这种**字面**形态。动态键名（比如配额表
      里存的字符串）会被算成 0 处，配置文档于是把它们列进"仅存在于 example、
      代码没人读" —— 实际有人读，只是读法不透明。集中在这里写死，文档能看见
      真实引用，也不会出现"改了键名漏改一处"。
    """
    try:
        cfg = dict(raw_getter() or {})
    except Exception:
        cfg = {}
    return {
        "aigc_enable": bool(cfg.get("aigc_enable", False)),
        "aigc_out_dir": str(cfg.get("aigc_out_dir") or ""),
        "aigc_daily_image_quota": cfg.get("aigc_daily_image_quota", 0),
        "aigc_daily_tts_seconds": cfg.get("aigc_daily_tts_seconds", 0),
        "aigc_image_backend": str(cfg.get("aigc_image_backend") or ""),
        "aigc_tts_backend": str(cfg.get("aigc_tts_backend") or ""),
        "aigc_tts_voice": str(cfg.get("aigc_tts_voice") or ""),
        "aigc_asr_backend": str(cfg.get("aigc_asr_backend") or ""),
        "aigc_asr_model": str(cfg.get("aigc_asr_model") or ""),
    }


class TaskContext:
    """交给后端的句柄：只能汇报进度，不能改状态。"""

    def __init__(self, service, task_id):
        self._svc = service
        self.task_id = task_id

    def report(self, progress, note=""):
        """进度 0.0~1.0。后端在长耗时循环里周期性调用。"""
        self._svc._set_progress(self.task_id, progress, note)

    def is_cancelled(self):
        """删除任务时置位，后端应在循环里自行退出。"""
        return self._svc._is_cancelled(self.task_id)


class AigcService:
    """统一任务层。一个进程一个实例，挂到 webui 的 /api/aigc/*。"""

    def __init__(self, root, cfg_getter=None, clock=None):
        """
        root: 产物根目录（通常是 data/media）
        cfg_getter: 无参函数 → 配置 dict；默认读 config.load_config()
        clock: 无参函数 → 时间戳；测试注入
        """
        self.root = os.path.abspath(root)
        self._cfg_getter = cfg_getter
        self._clock = clock or (lambda: time.time())
        self._lock = threading.RLock()
        self._tasks = {}                 # task_id -> dict
        self._backends = {}              # kind -> callable(task, params, ctx)
        self._cancelled = set()
        self._threads = {}

    # ── 配置 ────────────────────────────────────────────────
    def _raw_cfg(self):
        if self._cfg_getter is not None:
            try:
                return dict(self._cfg_getter() or {})
            except Exception:
                return {}
        try:
            import config
            return dict(config.load_config() or {})
        except Exception:
            return {}

    def _cfg(self):
        """归一后的配置（只含本层关心的键，字面读取见 read_cfg_dict）。"""
        return read_cfg_dict(self._raw_cfg)

    def enabled(self):
        return bool(self._cfg().get("aigc_enable", False))

    # ── 后端注册 ────────────────────────────────────────────
    def register_backend(self, kind, fn):
        """注册一个后端。fn(task, params, ctx) 返回 {"artifact": 路径, "meta": {...}}。

        返回的 artifact 已存在的文件会被直接收下；返回 bytes 则由本层落盘。
        """
        self._backends[kind] = fn

    def has_backend(self, kind):
        return kind in self._backends

    # ── 目录与配额 ──────────────────────────────────────────
    def _day(self, ts=None):
        ts = self._clock() if ts is None else ts
        return datetime.datetime.fromtimestamp(ts).strftime("%Y%m%d")

    def day_dir(self, kind, ts=None):
        return os.path.join(self.root, kind, self._day(ts))

    def daily_usage(self, kind, ts=None):
        """当天已用的 (数量, 秒数) —— 靠数当天目录里的 .json 元数据。"""
        d = self.day_dir(kind, ts)
        count, seconds = 0, 0.0
        try:
            names = os.listdir(d)
        except OSError:
            return 0, 0.0
        for n in names:
            if not n.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, n), encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                continue
            if meta.get("status") in TERMINAL:
                count += 1
                try:
                    seconds += float((meta.get("meta") or {}).get("duration") or 0)
                except Exception:
                    pass
        return count, seconds

    def quota_check(self, kind, params=None):
        """返回 (ok, reason)。未配置配额 = 不限。"""
        key = _QUOTA_KEY.get(kind)
        if not key:
            return True, ""
        limit = self._cfg().get(key)
        try:
            limit = float(limit or 0)
        except Exception:
            limit = 0
        if limit <= 0:
            return True, ""
        count, seconds = self.daily_usage(kind)
        if kind == KIND_TTS:
            need = 0.0
            try:
                need = float((params or {}).get("text") and len(str((params or {}).get("text"))) / 5.0 or 0)
            except Exception:
                need = 0.0
            if seconds + need > limit:
                return False, f"今日语音额度已用完（{seconds:.0f}/{limit:.0f} 秒）"
            return True, ""
        if count >= limit:
            return False, f"今日额度已用完（{count}/{limit:.0f} 张）"
        return True, ""

    # ── 提交 ────────────────────────────────────────────────
    def submit(self, kind, params=None, *, sync=False):
        """提交一个任务，返回 {"ok": bool, "task": {...}} 或 {"ok": False, "reason": str}。"""
        params = dict(params or {})
        if kind not in KINDS:
            return {"ok": False, "reason": f"未知种类: {kind}"}
        if not self.enabled():
            # 验收第 6 条：关掉时一个请求都不发 —— 连后端都不碰
            return {"ok": False, "reason": "AIGC 未启用（aigc_enable=false）"}
        ok, reason = self.quota_check(kind, params)
        if not ok:
            task = self._new_task(kind, params, ST_REJECTED, error=reason)
            self._register(task)
            return {"ok": False, "reason": reason, "task": task}
        fn = self._backends.get(kind)
        if fn is None:
            task = self._new_task(kind, params, ST_FAILED, error=f"后端未安装: {kind}")
            self._register(task)
            return {"ok": False, "reason": task["error"], "task": task}

        task = self._new_task(kind, params, ST_PENDING)
        self._register(task)
        ctx = TaskContext(self, task["id"])
        if sync:
            self._run(task["id"], fn, ctx)
        else:
            th = threading.Thread(target=self._run, args=(task["id"], fn, ctx),
                                  daemon=True, name=f"aigc-{kind}")
            self._threads[task["id"]] = th
            th.start()
        with self._lock:
            return {"ok": True, "task": dict(self._tasks.get(task["id"], task))}

    def _register(self, task):
        """进内存表并落盘。少这一步，get() 永远查不到自己刚提交的任务。"""
        with self._lock:
            self._tasks[task["id"]] = dict(task)
        self._persist(task)

    def _new_task(self, kind, params, status, error=""):
        now = self._clock()
        tid = "aigc_%s_%d_%s" % (kind, int(now * 1000), uuid.uuid4().hex[:4])
        return {
            "id": tid,
            "kind": kind,
            "status": status,
            "progress": 0.0,
            "note": "",
            "error": error,
            "params": params,
            "artifact": "",
            "meta_path": "",
            "created_at": now,
            "started_at": 0.0,
            "finished_at": 0.0,
        }

    def _run(self, tid, fn, ctx):
        self._update(tid, status=ST_RUNNING, started_at=self._clock())
        try:
            result = fn(self.get(tid) or {}, self._task_params(tid), ctx) or {}
        except Exception as e:
            self._update(tid, status=ST_FAILED, error=f"{type(e).__name__}: {e}",
                         finished_at=self._clock())
            self._persist(self.get(tid))
            return
        if ctx.is_cancelled():
            return
        try:
            artifact = self._save_artifact(tid, result)
        except Exception as e:
            self._update(tid, status=ST_FAILED, error=f"落盘失败: {e}",
                         finished_at=self._clock())
            self._persist(self.get(tid))
            return
        self._update(tid, status=ST_DONE, progress=1.0, artifact=artifact,
                     finished_at=self._clock(),
                     meta=result.get("meta") or {})
        self._persist(self.get(tid))

    def _task_params(self, tid):
        with self._lock:
            return dict((self._tasks.get(tid) or {}).get("params") or {})

    def _save_artifact(self, tid, result):
        """把后端产物落到 data/media/<kind>/<day>/<task_id>.<ext>，并写同名 .json。"""
        with self._lock:
            task = self._tasks.get(tid) or {}
        kind = task.get("kind") or KIND_IMAGE
        d = self.day_dir(kind)
        os.makedirs(d, exist_ok=True)
        ext = str(result.get("ext") or DEFAULT_EXT.get(kind, ".bin"))
        if not ext.startswith("."):
            ext = "." + ext
        out = os.path.join(d, f"{tid}{ext}")

        src = result.get("artifact")
        if isinstance(src, (bytes, bytearray)):
            with open(out, "wb") as f:
                f.write(src)
        elif isinstance(src, str) and src and os.path.exists(src):
            if os.path.abspath(src) != os.path.abspath(out):
                with open(src, "rb") as fi, open(out, "wb") as fo:
                    fo.write(fi.read())
        elif isinstance(src, str) and src:
            # 后端直接给了目标路径（少见）：信任它但要求存在
            if not os.path.exists(src):
                raise FileNotFoundError(f"后端声称的产物不存在: {src}")
            out = src
        else:
            raise ValueError("后端没有返回产物（artifact 为空）")
        return out

    # ── 查询 / 更新 ─────────────────────────────────────────
    def _update(self, tid, **kw):
        with self._lock:
            t = self._tasks.get(tid)
            if not t:
                return
            t.update(kw)

    def _set_progress(self, tid, progress, note=""):
        try:
            p = max(0.0, min(1.0, float(progress)))
        except Exception:
            return
        self._update(tid, progress=p, note=str(note or ""))

    def _is_cancelled(self, tid):
        return tid in self._cancelled

    def get(self, tid):
        with self._lock:
            t = self._tasks.get(str(tid))
            return dict(t) if t else None

    def list(self, kind=None, limit=50):
        with self._lock:
            items = [dict(t) for t in self._tasks.values()
                     if (kind is None or t.get("kind") == kind)]
        items.sort(key=lambda t: t.get("created_at") or 0, reverse=True)
        return items[:max(1, int(limit or 50))]

    def delete(self, tid, remove_files=True):
        """删除任务。运行中的任务先置取消位，让后端自己退出。"""
        tid = str(tid)
        with self._lock:
            task = self._tasks.pop(tid, None)
        if not task:
            return False
        self._cancelled.add(tid)
        if remove_files:
            for p in (task.get("artifact"), task.get("meta_path")):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
        return True

    # ── 持久化 ──────────────────────────────────────────────
    def _persist(self, task):
        """把任务快照写成产物目录里的同名 .json（产物没出来也写，便于排查）。"""
        if not task:
            return
        try:
            d = self.day_dir(task.get("kind") or KIND_IMAGE, task.get("created_at"))
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, f"{task['id']}.json")
            snap = dict(task)
            snap.pop("meta_path", None)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False, indent=2)
            with self._lock:
                if task["id"] in self._tasks:
                    self._tasks[task["id"]]["meta_path"] = path
        except Exception:
            pass

    def load_from_disk(self, kind=None, day=None):
        """启动时把当天（或指定日）的元数据读回内存，界面重启后列表还在。"""
        kinds = [kind] if kind else list(KINDS)
        for k in kinds:
            d = self.day_dir(k) if day is None else os.path.join(self.root, k, day)
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            for n in names:
                if not n.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(d, n), encoding="utf-8") as f:
                        t = json.load(f)
                except Exception:
                    continue
                if t.get("status") in (ST_PENDING, ST_RUNNING):
                    # 上次进程退出时没跑完的任务：标失败，别让它永远转圈
                    t["status"] = ST_FAILED
                    t["error"] = t.get("error") or "进程退出时任务未完成"
                t["meta_path"] = os.path.join(d, n)
                with self._lock:
                    self._tasks[t.get("id") or n[:-5]] = t


# ── 进程级单例 ──────────────────────────────────────────────
_default = None
_default_lock = threading.Lock()


def get_service(root=None):
    """取进程级单例（webui 的 /api/aigc/* 用）。

    root 优先用配置里的 `aigc_out_dir`（用户指定产物目录），
    没配才落到 `<app>/data/media`。
    """
    global _default
    with _default_lock:
        if _default is None:
            if root is None:
                cfg = read_cfg_dict(_default_raw_cfg)
                root = cfg.get("aigc_out_dir") or ""
            if not root:
                base = os.path.dirname(os.path.abspath(__file__))
                root = os.path.join(os.path.dirname(base), "data", "media")
            _default = AigcService(root)
        return _default


def _default_raw_cfg():
    import config
    return config.load_config()


def reset_service(svc=None):
    """测试用：替换/清空单例。"""
    global _default
    with _default_lock:
        _default = svc
    return _default
