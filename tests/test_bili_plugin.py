# -*- coding: utf-8 -*-
"""plugins/bili_script 单测（B站读稿插件，2026-10-30）。

只测**插件自己的策略**（开关、注入压缩、登录校验、钩子放行条件），
抓取/签名在 tests/test_bili_transcript.py 里测；这里全部打桩，不碰网络。

运行：python tests/test_bili_plugin.py
"""
import importlib
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


bs = importlib.import_module("plugins.bili_script")

#: 把插件的落盘路径挪到临时目录，别污染仓库里的 data/
_TMP = tempfile.mkdtemp(prefix="biliplug_")
bs.DATA_DIR = _TMP
bs.CONFIG_PATH = os.path.join(_TMP, "config.json")
bs.CACHE_PATH = os.path.join(_TMP, "cache.json")
bs.STATS_PATH = os.path.join(_TMP, "stats.json")

try:
    # ── 1. 元信息 ───────────────────────────────────────────────
    meta = bs.PLUGIN_META
    check("有插件页", meta.get("page") == "plugins/bili_script/ui.html", meta.get("page"))
    check("挂 before_reply", meta.get("hooks") == ["before_reply"], meta.get("hooks"))
    check("优先级在记忆和搜索之间", 40 < int(meta.get("priority") or 0) < 60, meta.get("priority"))
    check("动作表齐全",
          {"get_config", "set_config", "login_set", "login_status", "logout",
           "probe", "get_stats", "clear_cache", "login_qr_start", "login_qr_poll"}
          <= set(bs.PLUGIN_ACTIONS), sorted(bs.PLUGIN_ACTIONS))

    # ── 2. 配置读写 ─────────────────────────────────────────────
    c0 = bs.get_config()
    check("默认启用", c0["enabled"] is True and c0["auto_read"] is True, c0)
    check("默认未登录", c0["logged_in"] is False, c0)
    r = bs.set_config({"max_chars": 123, "max_lines": 2})
    check("保存设置成功", r.get("ok") is True, r)
    check("设置读得回来", bs.get_config()["max_chars"] == 123, bs.get_config())
    check("非法参数被拒", bs.set_config("nope").get("ok") is False)
    bs.set_config({"max_chars": 200, "max_lines": 3})

    # ── 3. 注入压缩 ─────────────────────────────────────────────
    data = {"title": "标题", "owner": "UP",
            "cues": [{"text": f"第{i}句"} for i in range(50)]}
    t = bs._inject_text(data, 200, 3)
    check("注入不超过上限", len(t) <= 200, len(t))
    check("注入带标题", t.startswith("【B站文稿】标题"), t[:30])
    check("注入报总条数", "共 50 条" in t, t[-40:])
    no_cue = bs._inject_text({"title": "T", "owner": "O", "cues": []}, 200, 3)
    check("无字幕时不编内容", "没有字幕轨" in no_cue and "别编" in no_cue, no_cue)
    check("空数据返回空串", bs._inject_text({}, 200, 3) == "")

    # ── 4. 钩子放行条件 ─────────────────────────────────────────
    check("没链接 → 不介入", bs.before_reply(message="今天天气不错") is None)
    check("空消息 → 不介入", bs.before_reply(message="") is None)
    bs.set_config({"auto_read": False})
    check("关掉自动读 → 不介入", bs.before_reply(message="BV1dL8K6HEm3") is None)
    bs.set_config({"auto_read": True})
    bs.set_config({"enabled": False})
    check("插件停用 → 不介入", bs.before_reply(message="BV1dL8K6HEm3") is None)
    bs.set_config({"enabled": True})

    # ── 5. 钩子注入（打桩 read_link）────────────────────────────
    _orig = bs.read_link
    bs.read_link = lambda url, use_cache=True: {
        "ok": True, "cached": True,
        "data": {"title": "测试视频", "owner": "某人",
                 "cues": [{"text": "大家好"}, {"text": "今天讲抽帧"}]}}
    out = bs.before_reply(message="看看 BV1dL8K6HEm3 讲了啥")
    check("命中链接 → inject", isinstance(out, dict) and out.get("action") == "inject", out)
    check("注入内容带标题", "测试视频" in (out or {}).get("content", ""), (out or {}).get("content"))
    check("meta 标了插件名", ((out or {}).get("meta") or {}).get("plugin") == "bili_script", out)

    bs.read_link = lambda url, use_cache=True: {"ok": False, "error": "挂了", "data": None}
    check("读失败 → 放行", bs.before_reply(message="BV1dL8K6HEm3") is None)
    bs.read_link = _orig

    # ── 6. 登录 ─────────────────────────────────────────────────
    check("空 cookie 被拒", bs.login_set({}).get("ok") is False)
    _ok_orig = bs._cookie_ok
    bs._cookie_ok = lambda ck: {"ok": False, "error": "nav 未登录"}
    check("无效 cookie 被拒", bs.login_set({"cookie": "abc"}).get("ok") is False)
    bs._cookie_ok = lambda ck: {"ok": True, "uname": "测试号", "mid": 1}
    r = bs.login_set({"cookie": "SESSDATA=deadbeefcafe"})
    check("有效 cookie 存下", r.get("ok") is True and r.get("uname") == "测试号", r)
    check("登录后状态为真", bs.login_status()["logged_in"] is True, bs.login_status())
    tail = bs.login_status()["cookie_tail"]
    check("cookie 只露尾号", tail.startswith("dead") and "beefcafe"[-4:] in tail, tail)
    bs.logout()
    check("退出登录生效", bs.get_config()["logged_in"] is False)
    bs._cookie_ok = _ok_orig

    # ── 7. 统计与缓存 ───────────────────────────────────────────
    bs._cache_put("BV1#1#", {"title": "x"})
    check("缓存写得进", bs.get_stats()["cache_size"] == 1, bs.get_stats())
    check("TTL 过期取不到", bs._cache_get("BV1#1#", 0) is not None)   # ttl 至少 1 小时
    bs.clear_cache()
    check("清空缓存", bs.get_stats()["cache_size"] == 0, bs.get_stats())
finally:
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
