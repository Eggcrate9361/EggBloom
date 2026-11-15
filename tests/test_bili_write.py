# -*- coding: utf-8 -*-
"""写能力（1.6.0）的闸门与回读测试。

写接口翻车是封号级别的，所以这里测的不是"能不能发"，而是**该不该发**：
默认关、干跑、熔断、冷却、日上限、同视频冷却、白名单、回读校验、审计留痕。

运行：python tests/test_bili_write.py
"""
import importlib
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


bs = importlib.import_module("plugins.bili")
import harness.bili_api as BA  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="biliw_")
bs.DATA_DIR = _TMP
bs.CONFIG_PATH = os.path.join(_TMP, "config.json")
bs.CACHE_DIR = os.path.join(_TMP, "cache")
bs.STATS_PATH = os.path.join(_TMP, "stats.json")
bs.AUDIT_PATH = os.path.join(_TMP, "actions.jsonl")

def _ck(mod, v):
    """直接写 cookie —— set_config 不接受 cookie（那是 login_set 的活）。"""
    cfg = mod._cfg()
    cfg["cookie"] = v
    mod._save(mod.CONFIG_PATH, cfg)


INFO = {"bvid": "BV1dL8K6HEm3", "aid": 9, "cid": 77, "title": "T", "owner": "UP",
        "owner_mid": 5, "stat": {}}


class FakeAPI:
    """替身：默认成功；可切失败/回读不到。"""
    ok = True
    verified = True
    calls = []

    def __init__(self, cookie=""):
        self.cookie = cookie

    def csrf(self):
        return "jct" if "bili_jct" in (self.cookie or "") else ""

    def view(self, bvid="", aid=0):
        return dict(INFO)

    def nav_user(self):
        return {"ok": True, "mid": 100, "uname": "me"}

    def post_comment(self, aid, text, bvid="", root=0, parent=0):
        FakeAPI.calls.append(("post_comment", aid, text))
        return ({"ok": True, "code": 0, "rpid": 12345, "error": ""} if FakeAPI.ok
                else {"ok": False, "code": -352, "error": "风控"})

    def send_danmaku(self, cid, text, progress_ms=0, mode=1):
        FakeAPI.calls.append(("send_danmaku", cid, text))
        return {"ok": True, "code": 0, "error": ""}

    def like(self, aid, on=True):
        return {"ok": True, "code": 0, "error": ""}

    def coin(self, aid, n=1):
        return {"ok": True, "code": 0, "error": ""}

    def follow(self, mid, on=True):
        return {"ok": True, "code": 0, "error": ""}

    def find_my_comment(self, aid, mid, rpid=0, pages=2):
        return {"text": "x"} if FakeAPI.verified else None


_REAL = BA.BiliAPI
BA.BiliAPI = FakeAPI

try:
    # ── 1. 默认全关 ─────────────────────────────────────────────
    st = bs.write_status()
    check("默认关闭写能力", st["enabled"] is False, st)
    check("默认干跑", st["dry_run"] is True, st)
    check("默认日上限 3", st["daily_max"] == 3, st)
    check("没有历史动作", st["total"] == 0, st)

    r = bs.post_comment({"url": "BV1dL8K6HEm3", "text": "你好"})
    check("关着 → 拒绝", r.get("ok") is False and r.get("blocked") is True, r)
    check("拒绝原因写清楚", "未启用" in r.get("error", ""), r.get("error"))
    check("拒绝也留痕", len(bs._recent_actions(10)) == 1, bs._recent_actions(10))

    # ── 2. 打开但仍干跑 ────────────────────────────────────────
    _ck(bs, "SESSDATA=a; bili_jct=b")
    bs.set_write_config({"write_enabled": True})
    FakeAPI.calls.clear()
    r = bs.post_comment({"url": "BV1dL8K6HEm3", "text": "你好"})
    check("开了之后干跑通过", r.get("ok") is True and r.get("dry_run") is True, r)
    check("干跑不发请求", FakeAPI.calls == [], FakeAPI.calls)

    # ── 3. 关掉干跑 → 真发 + 回读 ──────────────────────────────
    bs.set_write_config({"write_dry_run": False, "write_min_seconds": 0})
    bs._audit({"kind": "comment", "ok": True, "ts": time.time() - 60,
               "date": "2000-01-01", "dry_run": True})   # 让冷却/额度不挡
    FakeAPI.ok, FakeAPI.verified = True, True
    FakeAPI.calls.clear()
    r = bs.post_comment({"url": "BV1dL8K6HEm3", "text": "说得对"})
    check("真实发送成功", r.get("ok") is True and not r.get("dry_run"), r)
    check("真的调了发评论", any(c[0] == "post_comment" for c in FakeAPI.calls), FakeAPI.calls)
    check("回读到了", r.get("verified") is True, r)
    check("回的提示说可见", "可见" in (r.get("hint") or ""), r.get("hint"))

    # ── 4. 同视频冷却 ──────────────────────────────────────────
    r = bs.post_comment({"url": "BV1dL8K6HEm3", "text": "再来一条"})
    check("同视频 24h 内被挡", r.get("ok") is False and "小时" in r.get("error", ""), r)

    # ── 5. 日上限 ──────────────────────────────────────────────
    bs.clear_cache()
    # 造 3 条今天成功的记录（不同视频）
    for i in range(3):
        bs._audit({"kind": "comment", "bvid": f"BV1CCC{i:06d}", "ok": True,
                   "ts": time.time() - 100 - i, "date": time.strftime("%Y-%m-%d")})
    r = bs.post_comment({"url": "BV1dL8K6HEm3", "text": "第四条"})
    check("超过日上限被挡", r.get("ok") is False and "上限" in r.get("error", ""), r)

    # ── 6. 熔断 ────────────────────────────────────────────────
    bs2 = importlib.reload(bs)
    bs2.DATA_DIR = _TMP
    bs2.CONFIG_PATH = os.path.join(_TMP, "config.json")
    bs2.CACHE_DIR = os.path.join(_TMP, "cache")
    bs2.STATS_PATH = os.path.join(_TMP, "stats.json")
    bs2.AUDIT_PATH = os.path.join(_TMP, "actions2.jsonl")
    _ck(bs2, "SESSDATA=a; bili_jct=b")
    bs2.set_write_config({"write_enabled": True, "write_dry_run": False,
                          "write_min_seconds": 0})
    FakeAPI.ok = False
    r1 = bs2.post_comment({"url": "BV1dL8K6HEm3", "text": "a"})
    r2 = bs2.post_comment({"url": "BV1AAAAAAAAA", "text": "b"})
    r3 = bs2.post_comment({"url": "BV1BBBBBBBBB", "text": "c"})
    check("第一次失败照实返回", r1.get("ok") is False and r1.get("code") == -352, r1)
    check("连续两次失败后熔断", r3.get("ok") is False and "停手" in r3.get("error", ""), r3)
    check("熔断状态可查", bs2.write_status()["breaker"] is True, bs2.write_status())

    # ── 7. 文本长度 ────────────────────────────────────────────
    bs3 = importlib.reload(bs)
    bs3.DATA_DIR = _TMP
    bs3.CONFIG_PATH = os.path.join(_TMP, "config.json")
    bs3.CACHE_DIR = os.path.join(_TMP, "cache")
    bs3.STATS_PATH = os.path.join(_TMP, "stats.json")
    bs3.AUDIT_PATH = os.path.join(_TMP, "actions3.jsonl")
    _ck(bs3, "SESSDATA=a; bili_jct=b")
    bs3.set_write_config({"write_enabled": True, "write_text_max": 20})
    r = bs3.post_comment({"url": "BV1dL8K6HEm3", "text": "长" * 30})
    check("超长被挡", r.get("ok") is False and "太长" in r.get("error", ""), r)
    r = bs3.post_comment({"url": "BV1dL8K6HEm3", "text": "  "})
    check("空白被挡", r.get("ok") is False and "为空" in r.get("error", ""), r)

    # ── 8. 白名单 ──────────────────────────────────────────────
    bs3.set_write_config({"write_up_whitelist": ["999"], "write_text_max": 180})
    r = bs3.post_comment({"url": "BV1dL8K6HEm3", "text": "不在白名单"})
    check("UP 不在白名单被挡", r.get("ok") is False and "白名单" in r.get("error", ""), r)
    bs3.set_write_config({"write_up_whitelist": ["5"]})
    FakeAPI.ok = True   # 上面熔断段把它打成 False 了
    r = bs3.post_comment({"url": "BV1dL8K6HEm3", "text": "在白名单"})
    check("UP 在白名单放行", r.get("ok") is True, r)

    # ── 9. 没有 CSRF ───────────────────────────────────────────
    bs4 = importlib.reload(bs)
    bs4.DATA_DIR = _TMP
    bs4.CONFIG_PATH = os.path.join(_TMP, "config.json")
    bs4.CACHE_DIR = os.path.join(_TMP, "cache")
    bs4.STATS_PATH = os.path.join(_TMP, "stats.json")
    bs4.AUDIT_PATH = os.path.join(_TMP, "actions4.jsonl")
    _ck(bs4, "SESSDATA=a")          # 没有 bili_jct
    bs4.set_write_config({"write_enabled": True})
    r = bs4.post_comment({"url": "BV1dL8K6HEm3", "text": "hi"})
    check("缺 bili_jct 被挡", r.get("ok") is False and "bili_jct" in r.get("error", ""), r)
finally:
    BA.BiliAPI = _REAL
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
