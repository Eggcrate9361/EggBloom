# -*- coding: utf-8 -*-
"""plugins/bili 单测（B站插件，2026-10-30，P0）。

只测插件自己的策略：开关、注入压缩、缓存、钩子放行、登录校验。
抓取/签名在 tests/test_bili_api.py，这里全部打桩，不碰网络。

运行：python tests/test_bili.py
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


bs = importlib.import_module("plugins.bili")

_TMP = tempfile.mkdtemp(prefix="bili2_")
bs.DATA_DIR = _TMP
bs.CONFIG_PATH = os.path.join(_TMP, "config.json")
bs.CACHE_DIR = os.path.join(_TMP, "cache")
bs.STATS_PATH = os.path.join(_TMP, "stats.json")

try:
    # ── 1. 元信息 ───────────────────────────────────────────────
    meta = bs.PLUGIN_META
    check("页面路径对", meta.get("page") == "plugins/bili/ui.html", meta.get("page"))
    check("挂 before_reply", meta.get("hooks") == ["before_reply"], meta.get("hooks"))
    check("优先级在记忆与搜索之间", 40 < int(meta.get("priority") or 0) < 60)
    need = {"get_config", "set_config", "parse", "probe", "summary",
            "login_set", "login_qr_start", "login_qr_poll", "login_status",
            "logout", "get_stats", "clear_cache"}
    check("动作表齐全", need <= set(bs.PLUGIN_ACTIONS), sorted(bs.PLUGIN_ACTIONS))

    # ── 2. 配置 ─────────────────────────────────────────────────
    c = bs.get_config()
    check("默认启用+自动读", c["enabled"] and c["auto_read"], c)
    check("默认未登录", c["logged_in"] is False, c)
    check("默认抓弹幕和热评", c["with_danmaku"] and c["with_comments"], c)
    bs.set_config({"inject_chars": 180, "inject_lines": 3})
    check("设置能存能读", bs.get_config()["inject_chars"] == 180, bs.get_config())
    check("非法参数被拒", bs.set_config("x").get("ok") is False)
    bs.set_config({"inject_chars": 240, "inject_lines": 2})

    # ── 3. 注入压缩 ─────────────────────────────────────────────
    data = {"info": {"title": "标题", "owner": "UP", "stat": {"view": 123456789}},
            "transcript": {"cues": [{"text": f"第{i}句"} for i in range(40)]},
            "danmaku": {"hot": [{"word": "名场面", "count": 9}, {"word": "笑死", "count": 5}]},
            "comments": []}
    t = bs._inject_text(data, 240, 2)
    check("注入不超上限", len(t) <= 240, len(t))
    check("注入带标题", t.startswith("【B站】标题"), t[:24])
    check("注入带播放量", "1.2亿播放" in t, t[:40])
    check("注入报稿子条数", "共 40 条" in t, t[-30:])
    check("注入带弹幕高频", "名场面" in t, t)

    only_dm = bs._inject_text({"info": {"title": "T", "owner": ""},
                               "transcript": {"cues": []},
                               "danmaku": {"hot": [{"word": "梗", "count": 3}]}}, 240, 2)
    check("没稿子时用弹幕梗顶上", "弹幕里反复出现" in only_dm, only_dm)

    nothing = bs._inject_text({"info": {"title": "T", "owner": ""},
                               "transcript": {"cues": []}, "danmaku": {"hot": []}}, 240, 2)
    check("什么都没有时明说别编", "别编" in nothing, nothing)
    check("没标题返回空串", bs._inject_text({}, 240, 2) == "")

    check("数字格式化 亿", bs._fmt_num(123456789) == "1.2亿", bs._fmt_num(123456789))
    check("数字格式化 万", bs._fmt_num(12345) == "1.2万", bs._fmt_num(12345))
    check("数字格式化 小数字", bs._fmt_num(999) == "999", bs._fmt_num(999))

    # ── 4. 缓存 ─────────────────────────────────────────────────
    os.makedirs(bs.CACHE_DIR, exist_ok=True)
    bs._cache_put("BV1x#1", {"info": {"title": "cached"}})
    check("缓存写进去了", bs._cache_size() == 1, bs._cache_size())
    hit = bs._cache_get("BV1x#1")
    check("缓存读得回来", (hit or {}).get("info", {}).get("title") == "cached", hit)
    bs.clear_cache()
    check("清缓存后为空", bs._cache_size() == 0)

    # ── 5. 钩子放行条件 ─────────────────────────────────────────
    check("没有链接 → 放行", bs.before_reply(message="今天天气不错啊哈哈哈") is None)
    check("空消息 → 放行", bs.before_reply(message="") is None)
    bs.set_config({"auto_read": False})
    check("关了自动读 → 放行", bs.before_reply(message="看看 BV1dL8K6HEm3 讲了啥") is None)
    bs.set_config({"auto_read": True})
    bs.set_config({"enabled": False})
    check("插件停用 → 放行", bs.before_reply(message="看看 BV1dL8K6HEm3") is None)
    bs.set_config({"enabled": True})

    # ── 6. 钩子注入（打桩 read）────────────────────────────────
    _orig_read = bs.read
    bs.read = lambda url, fresh=False: {"ok": True, "cached": True, "data": {
        "info": {"title": "测试视频", "owner": "某人", "stat": {"view": 1000}},
        "transcript": {"cues": [{"text": "第一句"}]},
        "danmaku": {"hot": []}, "comments": [],
        "inject": "【B站】测试视频"}}
    out = bs.before_reply(message="看看这个 BV1dL8K6HEm3 讲了什么")
    check("命中链接 → inject", isinstance(out, dict) and out.get("action") == "inject", out)
    check("注入内容是压缩版", "测试视频" in (out or {}).get("content", ""), out)
    check("meta 标了插件名", ((out or {}).get("meta") or {}).get("plugin") == "bili", out)
    bs.read = lambda url, fresh=False: {"ok": False, "error": "挂了"}
    check("读失败 → 放行", bs.before_reply(message="BV1dL8K6HEm3") is None)
    bs.read = _orig_read

    # ── 7. 登录 ─────────────────────────────────────────────────
    check("空 cookie 被拒", bs.login_set({}).get("ok") is False)
    _st_orig = bs.login_status
    bs.login_status = lambda: {"logged_in": False, "hint": "nav 未登录", "uname": ""}
    r = bs.login_set({"cookie": "SESSDATA=deadbeef"})
    check("校验不过 → 不存", r.get("ok") is False and bs.get_config()["logged_in"] is False, r)
    bs.login_status = lambda: {"logged_in": True, "uname": "测试号", "cookie_tail": "dead…beef"}
    r = bs.login_set({"cookie": "deadbeefcafe"})
    check("校验通过 → 存下", r.get("ok") is True and r.get("uname") == "测试号", r)
    check("自动补 SESSDATA= 前缀", "SESSDATA" in bs._cfg().get("cookie", ""), bs._cfg().get("cookie"))
    bs.logout()
    check("退出登录清掉 cookie", bs.get_config()["logged_in"] is False)
    bs.login_status = _st_orig

    # ── 8. 摘要：没稿子要说清 ───────────────────────────────────
    r = bs.summary({"data": {"info": {}, "transcript": {"cues": []}}})
    check("没稿子 → 摘要失败且说明原因", r.get("ok") is False and "字幕" in r.get("error", ""), r)
    check("既没 data 也没链接 → 报错", bs.summary({}).get("ok") is False)

    # ── 9. agent 链路契约（A3 要读这个，不是装饰）──────────────
    ag = bs.PLUGIN_AGENT
    check("角色声明为能力供给", ag.get("role") == "capability", ag.get("role"))
    check("覆盖私聊+群聊", set(ag.get("channel") or []) == {"private", "group"},
          ag.get("channel"))
    check("有降级链声明", len(ag.get("degrade") or []) >= 3, ag.get("degrade"))
    check("有成本画像", "est_seconds" in (ag.get("cost") or {}), ag.get("cost"))
    tools = bs.agent_tools()
    check("工具数 >= 6", len(tools) >= 6, len(tools))
    check("声明里的工具名与实际一致",
          set(ag.get("tools") or []) == {t["name"] for t in tools},
          (set(ag.get("tools") or []), {t["name"] for t in tools}))
    check("工具定义是 function 形状",
          all(t.get("name") and t.get("desc") and
              (t.get("params") or {}).get("type") == "object" for t in tools),
          [t.get("name") for t in tools])
    check("每个工具都要 url 参数",
          all("url" in ((t.get("params") or {}).get("required") or []) for t in tools))

    r = bs.agent_call("bili_nope", {})
    check("未知工具 → ok=False 且不抛",
          r.get("ok") is False and "没有这个工具" in r.get("error", ""), r)
    bs.read = lambda url, fresh=False: {"ok": True, "data": {
        "info": {"title": "T"}, "transcript": {"cues": [{"text": "x"}]},
        "danmaku": {"total": 7, "hot": []}, "comments": [{"text": "c"}]}}
    r = bs.agent_call("bili_danmaku", {"url": "BV1dL8K6HEm3"})
    check("agent_call 取弹幕", r.get("ok") is True and r["data"].get("total") == 7, r)
    r = bs.agent_call("bili_comments", {"url": "BV1dL8K6HEm3"})
    check("agent_call 取热评", r.get("ok") is True and len(r["data"]) == 1, r)
    r = bs.agent_call("bili_transcript", {"url": "BV1dL8K6HEm3"})
    check("agent_call 取文稿", r.get("ok") is True and r["data"].get("cues"), r)
    bs.read = lambda url, fresh=False: {"ok": True, "data": {"transcript": {"cues": []}}}
    r = bs.agent_call("bili_transcript", {"url": "BV1dL8K6HEm3"})
    check("没字幕时带 note 说明原因（不许让模型猜）",
          "需要登录" in (r.get("note") or ""), r)
    bs.read = _orig_read

    # ── 10. 统计 ────────────────────────────────────────────────
    st = bs.get_stats()
    check("统计带缓存条数", "cache_size" in st, st)
    check("统计带登录态", "logged_in" in st, st)
finally:
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
