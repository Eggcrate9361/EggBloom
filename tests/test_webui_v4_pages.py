# -*- coding: utf-8 -*-
"""v4 页面功能验证：用 mock 数据把已迁移页面整页跑通。

为什么必须做这个
----------------
v4 是**新建**的前端，不是改旧文件。所以"页面能渲染"不等于"功能没丢" ——
必须逐页验证：接口有没有接对、**风控二次确认这类小流程有没有保留**、
自检/检测结果有没有逐项渲染。

已覆盖：
  · 协议页 —— /api/protocol/* 全部六个端点 + **风控二次确认** + 启动前自检 + 自动检测

它抓到过的真 bug：
  `fmt()` 对所有数字套了 `toLocaleString()`，端口 3000 被渲染成「3,000」。
  端口/QQ号这类**标识性数值必须原样输出**，千分位只适用于"计数"。

⚠️ 必须用**系统 Python**（Playwright）。缺 Playwright 时整体跳过，不会让套件变红。
   为什么起本地 HTTP 服务：`file://` 下 `fetch('/api/...')` 会变成
   `file:///D:/api/...`，Playwright 的 route 匹配不到，mock 不生效（踩过）。
"""
import os

# 自动找到本机 ms-playwright 下的 chromium（pip 的 playwright 不带浏览器，
# 浏览器是单独下载、版本号跟库绑定）。详见 tests/_pw_chrome.py。
sys_ = __import__("sys")
sys_.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pw_chrome import resolve_chrome as _resolve_chrome  # noqa: E402
import functools, base64, sys, http.server, io, json, re, socketserver, subprocess, tempfile, threading
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except Exception as _e:
    print(f"[SKIP] 未安装 Playwright（{type(_e).__name__}）→ 跳过 v4 页面验证")
    sys.exit(0)

# ★ 2026-10-22：原来是写死的 `Path(r"D:\git\EggBloom\webui")` 和 `D:\workbuddy_tmp`。
#   本机能跑，但换机器 / 换路径 / 上 CI 就必然失败 —— 测试要能跟着仓库走。
#   WEBUI 由本文件位置反推；截图输出到系统临时目录（不污染仓库，也不要求 D 盘存在）。
_HERE = Path(__file__).resolve().parent
WEBUI = _HERE.parent / "webui"
OUT = Path(tempfile.mkdtemp(prefix="egggbloom_v4shots_"))
errors, checks = [], []


def ck(name, cond, detail=""):
    checks.append((name, bool(cond), detail))
    print(("  [OK]   " if cond else "  [FAIL] ") + name + ("" if cond else "  " + str(detail)))


# ── 0. 起本地服务 ──
class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


handler = functools.partial(Quiet, directory=str(WEBUI))
srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"
print(f"本地服务: {BASE}")

STATUS = {
    "protocol_running": True, "protocol_driver": "snowluma",
    "drivers": ["napcat", "llonebot", "snowluma"],
    "api_port": 3000, "event_port": 3005, "qq_path": r"E:\QQ\QQ.exe", "status": {},
    "risks": {
        "napcat": {"level": "notice", "label": "风险提示",
                   "text": "NapCat BootMain 注入真实 QQ，重启会重扫登录态。"},
        "llonebot": {"level": "warning", "label": "未测试 · 不保证兼容",
                     "text": "LLOneBot 为 QQNT 插件，本项目未实测。"},
        "snowluma": {"level": "notice", "label": "风险提示",
                     "text": "SnowLuma 需手动打开 QQ 客户端保持登录。"},
    },
}
CHECK = {"ok": False, "driver": "snowluma", "items": [
    {"key": "exe", "label": "SnowLuma 运行时", "ok": True, "hint": "已找到 node.exe"},
    {"key": "qq", "label": "QQ 客户端路径", "ok": False, "hint": "未配置，请在下方填写"}]}
AUTO = {"ok": True, "recommended": "snowluma", "candidates": [
    {"driver": "snowluma", "name": "SnowLuma", "exe": r"E:\SnowLuma\index.mjs",
     "found": True, "running": True, "desc": "手动登录，风控低"},
    {"driver": "napcat", "name": "NapCat", "exe": "", "found": False,
     "running": False, "desc": ""}]}
LOGS = {"logs": [
    {"time": "16:01:02", "level": "info", "msg": "协议层已启动"},
    {"time": "16:01:03", "level": "warn", "msg": "事件端口 3005 被占用，已顺延"},
    {"time": "16:01:05", "level": "error", "msg": "登录态失效，需重新扫码"}]}

# 日志页现在吃 /api/events（服务端 webui_server.collect_events 的形状）。
# 四类各给一条 + 2 条调试行，用来验"调试不进事件流、但页脚要点名条数"。
EVENTS = {
    "events": [
        {"kind": "message", "time": "16:01:10", "text": "今天去不去",
         "dir": "in", "scope": "群 829854136", "who": "小明"},
        {"kind": "message", "time": "16:01:12", "text": "去啊 几点", "dir": "out",
         "scope": "群 829854136", "who": "水星"},
        {"kind": "decision", "time": "16:01:11",
         "text": "无触发消息，决策插件建议插话: 今天去不去",
         "tag": "决策放行"},
        {"kind": "tool", "time": "16:01:13", "text": "search_web、fetch_page",
         "tag": "工具", "tools": ["search_web", "fetch_page"]},
        {"kind": "cost", "time": "16:01:13",
         "text": "AI 回复 (4411 tokens, ￥0.002205, 场景 private)"},
    ],
    "counts": {"message": 2, "decision": 1, "tool": 1, "cost": 1, "error": 0,
               "debug": 2},
    "total": 7,
}

GROUPS = {"ok": True, "groups": [
    {"group_id": "123456", "group_name": "核院 2026 级", "member_count": 128,
     "bot_role": "admin", "bot_is_admin": True, "bot_card": "水星",
     "reply_enabled": True, "whitelisted": True, "block_reason": "", "new_member_card": ""},
    {"group_id": "301444", "group_name": "某个陌生群", "member_count": 301,
     "bot_role": "member", "bot_is_admin": False, "bot_card": "",
     "reply_enabled": True, "whitelisted": False,
     "block_reason": "不在群白名单（白名单共 12 个群）", "new_member_card": ""},
    {"group_id": "777888", "group_name": "已关自动回复的群", "member_count": 40,
     "bot_role": "owner", "bot_is_admin": True, "bot_card": "",
     "reply_enabled": False, "whitelisted": True,
     "block_reason": "该群自动回复已关闭（群管理页 reply_enabled=False）",
     "new_member_card": "欢迎新同学"},
]}
GROUP_MEMBERS = {"ok": True, "group_id": "123456", "bot_qq": "10001", "members": [
    {"user_id": "10001", "nickname": "水星", "card": "水星", "role": "admin", "title": "",
     "is_bot": True},
    {"user_id": "20002", "nickname": "同学甲", "card": "", "role": "owner", "title": "", "is_bot": False},
]}
BOT_STATUS = {"data": {"bot_running": True, "pid": 12345, "uptime": 3725,
                       "qq_path": r"E:\QQ\QQ.exe", "protocol_enabled": True,
                       "protocol_online": True, "protocol_url": "http://127.0.0.1:3000"}}
TOKENS = {"snapshot": {
    "group": {"requests": 120, "input_tokens": 400000, "output_tokens": 30000,
              "total_tokens": 430000, "cache_hit_tokens": 350000,
              "cache_miss_tokens": 50000, "cache_hit_rate": 87.5},
    "private": {"requests": 15, "input_tokens": 30000, "output_tokens": 4000,
                "total_tokens": 34000, "cache_hit_tokens": 20000,
                "cache_miss_tokens": 10000, "cache_hit_rate": 66.7},
    "all": {"requests": 135, "input_tokens": 430000, "output_tokens": 34000,
            "total_tokens": 464000, "cache_hit_tokens": 370000,
            "cache_miss_tokens": 60000, "cache_hit_rate": 86.0}},
    "recent": [{"scene": "group", "total_tokens": 3355, "cache_hit_rate": 87.5, "time": "16:20:01"},
               {"scene": "private", "total_tokens": 1200, "cache_hit_rate": 66.7, "time": "16:20:05"}]}
CONFIG = {
    "bot_qq": "10001", "api_provider": "deepseek", "model_name": "deepseek-flash",
    "api_url": "https://api.deepseek.com/chat/completions",
    # 敏感字段：服务端回传的是**掩码**（webui_server._mask_secret）
    "api_key": "«redacted:sk-123…»",
    "onebot_token": "«redacted:abc123…»",
    "ai_temperature": 0.7, "ai_max_tokens": 800, "ai_top_p": 0.95,
    "ai_frequency_penalty": 0.1, "ai_presence_penalty": 0.15,
    "group_idle_exit_min_silence": 600, "group_last_exit_cooldown": 3000,
    "group_msg_buffer_window": 3, "group_recent_msgs_window": 30,
    "group_session_max_replies": 20, "group_idle_exit_probability": 0.3,
    "group_idle_exit_enable": True,
    "protocol_enabled": True, "protocol_api_port": 3000, "protocol_event_port": 3005,
    "protocol_driver": "snowluma",
    "meme_enable": True, "meme_idle_exit_enable": True, "meme_cooldown": 60,
    "recall_feedback_enable": True, "recall_window_seconds": 10,
    "embed_enabled": True, "embed_top_k": 8,
    "session_ttl": 300, "session_max_rounds": 7,
    "admin_qqs": ["10001"], "admin_commands_enable": True,
    "private_reply_enabled": True, "startup_cooldown": 60,
    "notify_admin": False, "msg_split_len": 500, "idle_timeout": 300,
    "group_whitelist": ["123456", "234567"],
    "recall_feedback_pre_messages": ["？", "你撤回了一条消息", "还好你撤了 我差点就回了"],
    "recall_feedback_post_messages": ["撤回了？行 那我也不用回了"],
    "custom_exit_phrases_idle": ["困了 先撤了", "有事 回头聊"],
    "custom_exit_phrases_short": ["嗯 走了"],
    "llama_driver": "llama-server", "llama_model_path": r"E:\models\x.gguf",
    "llama_model_name": "qwen", "llama_port": 8081,
    "comfyui_host": "127.0.0.1", "comfyui_port": 8000, "comfyui_timeout": 120,
    "providers": [{"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com",
                   "api_key": "«redacted:sk-123…»", "has_api_key": True,
                   "models": [{"id": "deepseek-flash", "name": "DeepSeek Chat"}]}],
    "group_restrictions": {"123456": {"reply_enabled": True}},
}
config_calls = []
action_calls = []
group_calls = []
bot_calls = []
switch_calls = []
# 卡片 <img> 用的 1x1 PNG：表情包页会真的去取 /memes/<dir>/<file>，
# 不 mock 就会 404，而 404 会被「无 JS 报错」那条断言当成页面故障。
PNG1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

# 库装了 ≠ 浏览器在（Playwright 的浏览器单独下载、版本号跟库绑定）。
# 先试真正起一个：起不来就整篇跳过并 exit 0 —— 本文件全部内容都依赖浏览器，
# 缺环境不该让套件判红。CI 的 windows-latest 正是这种状态。
try:
    _probe = sync_playwright().start()
    _probe_b = _probe.chromium.launch(executable_path=_resolve_chrome())
    _probe_b.close()
    _probe.stop()
except Exception as _e:
    print("SKIP: 起不了浏览器（%s）：%s" % (type(_e).__name__, str(_e)[:120]))
    print("      需要 `playwright install chromium`，或设 PW_CHROME 指向本机 chromium")
    sys.exit(0)

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=_resolve_chrome())
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.on("pageerror", lambda e: errors.append("pageerror: " + str(e)[:240]))
    pg.on("console", lambda m: errors.append("console." + m.type + ": " + m.text[:200])
          if m.type == "error" else None)

    def route(r):
        u = r.request.url
        if "/api/plugins" in u and "/api/plugins/toggle" not in u:
            return r.fulfill(status=200, content_type="application/json", body=json.dumps({
                "plugins": [{"name": "ego", "enabled": True},
                            {"name": "mood_engine", "enabled": False}]}))
        if "/api/protocol/status" in u:
            return r.fulfill(status=200, content_type="application/json", body=json.dumps(STATUS))
        if "/api/protocol/check" in u:
            return r.fulfill(status=200, content_type="application/json", body=json.dumps(CHECK))
        if "/api/protocol/auto-detect" in u:
            return r.fulfill(status=200, content_type="application/json", body=json.dumps(AUTO))
        if "/api/protocol/logs/clear" in u:
            return r.fulfill(status=200, content_type="application/json", body='{"ok":true}')
        if "/api/protocol/logs" in u:
            return r.fulfill(status=200, content_type="application/json", body=json.dumps(LOGS))
        if u.rstrip("/").endswith("/api/events"):
            return r.fulfill(status=200, content_type="application/json", body=json.dumps(EVENTS))
        # 配置页的「接入与诊断」动作
        # 控制台 / 记忆 / 生图 / 表情包
        if "/api/command" in u:
            action_calls.append(("command", json.loads(r.request.post_data or "{}")))
            return r.fulfill(status=200, content_type="application/json",
                             body='{"ok":true,"output":"状态: 运行中 | 运行时长: 01:02:03"}')
        if "/api/memory/detail" in u:
            return r.fulfill(status=200, content_type="application/json", body=json.dumps({
                "qq": "20002", "rounds": [{"time": "16:00", "user": "你好", "bot": "在的"}]}))
        if "/api/memory/list" in u:
            return r.fulfill(status=200, content_type="application/json", body=json.dumps({
                "items": [{"qq": "20002", "count": 12, "last": "16:00"},
                          {"qq": "30003", "count": 5, "last": "15:20"}]}))
        if u.rstrip("/").endswith("/api/memory"):
            return r.fulfill(status=200, content_type="application/json", body=json.dumps({
                "data": {"memory_total": 4080, "learned_messages": 4080, "active_groups": 7,
                         "storage_kb": 1024.5,
                         "expressions": [{"text": "666 太强了", "scene": "praise", "score": 85}]}}))
        if "/api/imagegen/generate" in u:
            action_calls.append(("generate", json.loads(r.request.post_data or "{}")))
            return r.fulfill(status=200, content_type="application/json",
                             body='{"ok":true,"image":"/memes/demo.png"}')
        if "/api/imagegen/status" in u:
            return r.fulfill(status=200, content_type="application/json", body=json.dumps({
                "comfyui_running": True, "comfyui_host": "127.0.0.1", "comfyui_port": 8000,
                "comfyui_checkpoint": "sd_xl_base.safetensors", "workflow_path": "wf.json",
                "prompt_node_id": "353", "comfyui_default_width": 1024,
                "comfyui_default_height": 1536, "comfyui_timeout": 120}))
        if "/api/memes/import" in u:
            action_calls.append(("memes_import", json.loads(r.request.post_data or "{}")))
            return r.fulfill(status=200, content_type="application/json",
                             body='{"success":true,"imported":3}')
        if "/api/memes/delete" in u:
            action_calls.append(("memes_delete", json.loads(r.request.post_data or "{}")))
            return r.fulfill(status=200, content_type="application/json", body='{"ok":true}')
        if u.rstrip("/").endswith("/api/memes"):
            return r.fulfill(status=200, content_type="application/json", body=json.dumps({
                "memes": [{"name": "kaguya/a.png", "tags": ["可爱", "日常"]},
                         {"name": "kaguya/b.jpg", "tags": ["可爱"]},
                         {"name": "vrchat/c.gif", "tags": ["无语"]}]}))

        if "/api/llm/test" in u:
            action_calls.append(("llm_test", json.loads(r.request.post_data or "{}")))
            return r.fulfill(status=200, content_type="application/json",
                             body='{"ok":true,"model":"deepseek-flash","reply":"你好"}')
        if "/api/llama/scan" in u:
            action_calls.append(("llama_scan", json.loads(r.request.post_data or "{}")))
            return r.fulfill(status=200, content_type="application/json", body=json.dumps({
                "ok": True,
                "llama_servers": [{"path": r"E:\llama\llama-server.exe"}],
                "models": [{"path": r"E:\models\qwen.gguf"}]}))
        if "/api/llama/start" in u:
            action_calls.append(("llama_start", json.loads(r.request.post_data or "{}")))
            return r.fulfill(status=200, content_type="application/json",
                             body='{"ok":true,"driver":"llama-server"}')
        if "/api/llama/stop" in u:
            action_calls.append(("llama_stop", {}))
            return r.fulfill(status=200, content_type="application/json", body='{"ok":true}')
        if "/api/llama/open" in u:
            action_calls.append(("llama_open", json.loads(r.request.post_data or "{}")))
            return r.fulfill(status=200, content_type="application/json",
                             body='{"ok":true,"url":"http://127.0.0.1:8081"}')
        if "/api/llama/test" in u:
            action_calls.append(("llama_test", json.loads(r.request.post_data or "{}")))
            return r.fulfill(status=200, content_type="application/json", body='{"ok":true,"model":"qwen"}')
        if "/api/imagegen/test" in u:
            action_calls.append(("imagegen_test", json.loads(r.request.post_data or "{}")))
            return r.fulfill(status=200, content_type="application/json", body='{"ok":true}')
        if "/api/config/model" in u:
            config_calls.append(("model", json.loads(r.request.post_data or "{}")))
            return r.fulfill(status=200, content_type="application/json",
                             body='{"success":true}')
        if "/api/config/reload" in u:
            return r.fulfill(status=200, content_type="application/json", body='{"ok":true}')
        if u.rstrip("/").endswith("/api/config"):
            if r.request.method == "POST":
                config_calls.append(("save", json.loads(r.request.post_data or "{}")))
                return r.fulfill(status=200, content_type="application/json", body='{"ok":true}')
            return r.fulfill(status=200, content_type="application/json", body=json.dumps(CONFIG))
        if "/api/bot/status" in u:
            return r.fulfill(status=200, content_type="application/json", body=json.dumps(BOT_STATUS))
        if "/api/bot/start" in u:
            bot_calls.append("start")
            return r.fulfill(status=200, content_type="application/json",
                             body='{"success":true,"output":"Bot 已启动"}')
        if "/api/bot/stop" in u:
            bot_calls.append("stop")
            return r.fulfill(status=200, content_type="application/json",
                             body='{"success":true,"output":"Bot 已停止"}')
        if "/api/token-stats" in u:
            return r.fulfill(status=200, content_type="application/json", body=json.dumps(TOKENS))
        if "/api/groups/members" in u:
            return r.fulfill(status=200, content_type="application/json", body=json.dumps(GROUP_MEMBERS))
        if "/api/groups/settings" in u:
            body = json.loads(r.request.post_data or "{}")
            group_calls.append(("settings", body))
            return r.fulfill(status=200, content_type="application/json", body='{"ok":true}')
        if "/api/groups/action" in u:
            body = json.loads(r.request.post_data or "{}")
            group_calls.append(("action", body))
            return r.fulfill(status=200, content_type="application/json", body='{"ok":true}')
        if "/api/groups" in u:
            return r.fulfill(status=200, content_type="application/json", body=json.dumps(GROUPS))
        if "/api/protocol/switch" in u:
            body = json.loads(r.request.post_data or "{}")
            switch_calls.append(body)
            if not body.get("confirm"):
                return r.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "success": False, "need_confirm": True,
                    "output": "「napcat」属非官方登录通道。重启会重扫 QQ 登录态，如需继续请二次确认。"}))
            return r.fulfill(status=200, content_type="application/json",
                             body='{"success":true,"output":"已切换到 napcat"}')
        return r.fulfill(status=200, content_type="application/json", body="{}")

    # 表情包图片：mock 环境没有真文件，回 1×1 PNG。
    # ⚠️ 必须单独注册路由 —— 上面那条是 `**/api/**`，`/memes/a.png` 根本不会进去
    #   （第一版把图片分支写在 api route 里 = 死代码，404 依旧）。
    # ⚠️ glob 必须是 `**/memes/**`：表情包名可能带子目录（kaguya/a.png），
    #   `*` 不跨 `/`，写成 `**/memes/*` 会漏掉带目录的那批 → 404 → 被当成页面故障。
    MEME_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
                "AAAABJRU5ErkJggg==")
    pg.route("**/memes/**", lambda r: r.fulfill(
        status=200, content_type="image/png", body=base64.b64decode(MEME_PNG)))
    pg.route("**/api/**", route)
    pg.goto(f"{BASE}/webui_artistic_v4.html", wait_until="load")
    pg.wait_for_timeout(900)
    pg.evaluate("() => go(1)")
    pg.wait_for_timeout(1100)

    print("\n=== 协议页渲染 ===")
    txt = pg.inner_text(".panel")
    ck("标题是「协议」", "协议" in pg.inner_text("#ttl"), pg.inner_text("#ttl"))
    # ★ 2026-10-22 修正：文案由「运行中」改为「在线」。
    #   2026-10-19 把连接判据从「本界面点过启动」改成「OneBot 是否可达」
    #   （s.protocol_online 优先，缺失才回落 protocol_running）——
    #   外部启动的 NapCat/LLOneBot 不经过本界面，旧判据会把在线误报成「已停止」。
    #   文案跟着改成「在线/离线」正是为了不再暗示「只有本界面启动才算数」。
    ck("连接状态＝在线", "在线" in txt, txt[:200])
    ck("★ 在线来源标注（本界面启动 / 外部驱动）",
       "本界面启动" in txt or "外部驱动" in txt, txt[:200])
    ck("★ API/事件端口", "3000" in txt and "3005" in txt)
    ck("★ QQ 路径", "QQ.exe" in txt)
    ck("★ 三个驱动都在", all(d in txt for d in ("napcat", "llonebot", "snowluma")))
    ck("★ 风险说明逐条渲染", "未测试" in txt and "不保证兼容" in txt)
    ck("★ 日志渲染（含 ERROR）", "登录态失效" in txt and "ERROR" in txt)

    print("\n=== ★ 风控二次确认 ===")
    pg.click('[data-drv="napcat"]')
    pg.wait_for_timeout(700)
    t2 = pg.inner_text(".panel")
    ck("第一次点击 → 弹二次确认而非直接切换", "二次确认" in t2, t2[-300:])
    ck("确认文案带风险说明", "非官方登录通道" in t2)
    ck("此时只发了一次请求且未带 confirm",
       len(switch_calls) == 1 and not switch_calls[0].get("confirm"), switch_calls)
    pg.click("#pConfirm")
    pg.wait_for_timeout(1000)
    ck("★ 确认后第二次请求带 confirm=true",
       len(switch_calls) == 2 and switch_calls[1].get("confirm") is True, switch_calls)

    print("\n=== 启动前自检 ===")
    pg.click("#pCheck")
    pg.wait_for_timeout(600)
    t3 = pg.inner_text("#pOut")
    ck("逐项渲染", "SnowLuma 运行时" in t3 and "QQ 客户端路径" in t3, t3[:200])
    ck("★ 就绪/缺失分别标注", "就绪" in t3 and "缺失" in t3)
    ck("结论如实（存在未就绪项）", "存在未就绪项" in t3)

    print("\n=== 自动检测 ===")
    pg.click("#pAuto")
    pg.wait_for_timeout(600)
    t4 = pg.inner_text("#pOut")
    ck("渲染 + 推荐", "推荐" in t4 and "SnowLuma" in t4, t4[:160])
    ck("状态区分", "运行中" in t4 and "未找到" in t4)

    print("\n=== 群管理页 ===")
    pg.evaluate("() => go(2)")          # 第 3 项 = 群管理
    pg.wait_for_timeout(1400)
    g = pg.inner_text(".panel")
    ck("标题是「群管理」", "群管理" in pg.inner_text("#ttl"), pg.inner_text("#ttl"))
    ck("总览读数（群总数/参与中/被拦截）",
       "群总数" in g and "参与中" in g and "被拦截" in g, g[:220])
    ck("★ 群名与群号都在", "核院 2026 级" in g and "123456" in g and "301444" in g)
    ck("★ Bot 身份渲染（群主/管理员/成员）",
       "管理员" in g and "成员" in g and "群主" in g)

    print("\n=== ★ block_reason 必须可见（P0-2 的成果不能被埋掉）===")
    ck("白名单外群的拦截原因直接显示在列表里", "不在群白名单" in g)
    ck("reply_enabled=False 的原因也显示", "自动回复已关闭" in g)

    print("\n=== 入群名片（只在详情里渲染，列表行保持干净）===")
    pg.click('[data-open="777888"]')
    pg.wait_for_timeout(1000)
    ck("★ 打开有配置的群 → 名片值被带进输入框",
       pg.input_value("#gCard") == "欢迎新同学", pg.input_value("#gCard"))
    pg.click("#gClose")
    pg.wait_for_timeout(400)

    print("\n=== 参与开关 ===")
    group_calls.clear()
    pg.click('[data-toggle="123456"]')
    pg.wait_for_timeout(1000)
    ck("★ 切换参与 → POST /groups/settings 带 reply_enabled",
       any(k == "settings" and "reply_enabled" in c for k, c in group_calls), group_calls)

    print("\n=== 展开详情：成员 + 群动作 ===")
    pg.evaluate("() => go(2)")
    pg.wait_for_timeout(1300)
    group_calls.clear()
    pg.click('[data-open="123456"]')
    pg.wait_for_timeout(1200)
    d = pg.inner_text(".panel")
    ck("成员列表渲染（含「本机」标记）", "同学甲" in d and "本机" in d, d[-400:])
    pg.click('#gSaveCard')
    pg.wait_for_timeout(800)
    ck("★ 保存名片 → POST /groups/settings 带 new_member_card",
       any(k == "settings" and "new_member_card" in c for k, c in group_calls), group_calls)
    group_calls.clear()
    # v4 从 1.5 起**不再用系统 confirm**（用户反馈"群管理的弹窗是系统弹窗"）：
    # 全员禁言改成"应用内二次确认"= 点两下（第一下武装、第二下执行）；
    # 成员禁言改成"原地展开时长选项"（1分/10分/1时/1天）。用户反馈"只能禁言 10 分钟"。
    pg.once("dialog", lambda d: d.accept())   # 兜底：若还有别的 dialog 不至于卡住
    pg.click('#gWholeBan')
    pg.wait_for_timeout(400)
    pg.click('#gWholeBan')                    # 第二下才真正执行
    pg.wait_for_timeout(800)
    ck("★ 全员禁言 → POST /groups/action {action:'whole_ban'}",
       any(k == "action" and c.get("action") == "whole_ban" for k, c in group_calls), group_calls)
    group_calls.clear()
    pg.click('[data-m="ban"][data-u="20002"]')
    pg.wait_for_timeout(400)
    ck("★ 成员禁言 → 原地展开时长选项（不再写死 10 分钟）",
       pg.locator('[data-d]').count() >= 3, pg.locator('[data-d]').count())
    pg.click('[data-d="60"]')                 # 选「1 小时」
    pg.wait_for_timeout(800)
    ck("★ 成员禁言 → {action:'ban', user_id, duration_minutes=60}",
       any(k == "action" and c.get("action") == "ban" and c.get("user_id") == "20002"
           and c.get("duration_minutes") == 60 for k, c in group_calls), group_calls)

    pg.screenshot(path=str(OUT / "_v4_groups.png"), full_page=True)

    print("\n=== Bot 页 ===")
    bot_calls.clear()
    pg.evaluate("() => go(8)")          # 第 9 项 = Bot
    pg.wait_for_timeout(1200)
    bt = pg.inner_text(".panel")
    ck("标题是「Bot」", "Bot" in pg.inner_text("#ttl"), pg.inner_text("#ttl"))
    ck("运行状态＝运行中", "运行中" in bt, bt[:160])
    ck("★ PID 原样显示（不被千分位）", "12345" in bt and "12,345" not in bt, bt[:200])
    ck("★ 运行时长人性化（3725s → 1 时 2 分）", "1 时 2 分" in bt, bt[:200])
    ck("QQ 路径", "QQ.exe" in bt)
    ck("协议连接＝在线（2026-10-19 改读真实探测）", "在线" in bt)
    ck("运行中时「启动」按钮应为禁用",
       pg.eval_on_selector("#bStart", "e => e.disabled") is True)
    pg.click("#bStop")
    pg.wait_for_timeout(900)
    ck("★ 停止 → POST /api/bot/stop", "stop" in bot_calls, bot_calls)
    ck("停止结果有反馈", "已停止" in pg.inner_text("#bOut"), pg.inner_text("#bOut"))

    print("\n=== Token 页（零图表）===")
    pg.evaluate("() => go(6)")          # 第 7 项 = Token
    pg.wait_for_timeout(1200)
    tk = pg.inner_text(".panel")
    ck("标题是「Token」", "Token" in pg.inner_text("#ttl"), pg.inner_text("#ttl"))
    ck("★ 总 Token 渲染", "464,000" in tk, tk[:220])
    ck("★ 缓存命中率渲染（86%）", "86" in tk and "%" in tk, tk[:260])
    ck("★ 按场景表（群聊/私聊）", "群聊" in tk and "私聊" in tk, tk[:300])
    ck("★ 最近记录渲染（含 tokens 与命中率）",
       "3355 tokens" in tk or "3,355 tokens" in tk, tk[-300:])
    ck("★ 页面内没有 canvas/svg 图表",
       pg.eval_on_selector_all(".panel canvas, .panel svg", "els => els.length") == 0)

    pg.screenshot(path=str(OUT / "_v4_token.png"), full_page=True)

    print("\n=== 配置中心 ===")
    pg.evaluate("() => go(7)")          # 第 8 项 = 配置中心
    pg.wait_for_timeout(1300)
    cf = pg.inner_text(".panel")
    ck("标题是「配置中心」", "配置中心" in pg.inner_text("#ttl"), pg.inner_text("#ttl"))
    # ★ 2026-10-24：配置页改成 6 个分组（接入/说话/群聊/互动/功能/界面），
    #   原来的 15 个面板标签变成组内小节。覆盖度仍由 audit_config_parity.py 核对，
    #   这里只锁「分组存在 + 小节标题是中文面板名 + 字段带中文标签」。
    groups = pg.eval_on_selector_all(".cfg-grp b", "e=>e.map(x=>x.textContent)")
    ck("★ 配置页分成 6 个分组", groups == ["接入", "说话", "群聊", "互动", "功能", "界面"], groups)
    seen = []
    for _g in ["connect", "talk", "grouptalk", "interact", "feature"]:
        pg.evaluate("(g)=>{const b=[...document.querySelectorAll('.cfg-grp')]"
                    ".find(x=>x.dataset.grp===g); if(b) b.click();}", _g)
        pg.wait_for_timeout(120)
        seen += pg.evaluate("()=>window.__cfPanelLabels()")
    ck("★ 组内小节标题是中文面板名",
       all(w in seen for w in ["基础", "对话大脑", "群聊", "表情包", "撤回反馈",
                               "退场语", "权限", "自动通过"]), seen)
    pg.evaluate("()=>window.__cfGo('basic')")
    pg.wait_for_timeout(200)
    ck("★ 当前小节字段带中文标签而不是裸键名",
       pg.evaluate("()=>{const f=document.querySelector('#cfArea .cf-fld');"
                   "return !!f && !!f.querySelector('.cf-lab') && !!f.querySelector('[data-k],[data-sw]');}"))
    nctl = pg.eval_on_selector_all(".panel [data-k]", "e => e.length")
    names = pg.eval_on_selector_all(".panel [data-k]", "e => e.map(x=>x.dataset.k)")
    print(f"    [诊断] data-k 控件 {nctl} 个；前 20 个：{names[:20]}")
    print(f"    [诊断] 页面 JS 报错 {len(errors)} 条：{errors[:3]}")
    ck("★ 未登记标签的键仍在「其他配置项」里可编辑", nctl > 10, nctl)
    masked = pg.eval_on_selector_all('.panel [data-k][data-masked="1"]', "e => e.map(x=>x.dataset.k)")
    ck("★ 敏感字段标了 data-masked", bool(masked), masked)

    # 引导横幅：夹具里 api_key 有值 → 已配好，横幅必须收起来（不能打扰配好的人）
    ck("★ 已配置模型时不显示引导横幅",
       pg.eval_on_selector("#cfgSetup", "e=>e.hidden") is True)

    print("\n=== ★ 日志页：事件视图（不是原始日志行）===")
    pg.evaluate("() => go(9)")
    pg.wait_for_timeout(1100)
    ev_rows = pg.eval_on_selector_all("#evList .evt", "e => e.map(x=>x.className)")
    ck("★ 日志页画的是事件行", len(ev_rows) >= 4, ev_rows)
    ck("★ 事件行带五类之一的 kind 标记",
       all(("k-message" in c) or ("k-decision" in c) or ("k-tool" in c)
           or ("k-cost" in c) or ("k-error" in c)
           for c in ev_rows), ev_rows)
    ev_text = pg.inner_text("#evList")
    ck("★ 消息写明「谁 · 在哪说 + 说了什么」",
       "今天去不去" in ev_text and "小明" in ev_text, ev_text[:260])
    ck("★ 决策写明结论（建议插话）", "建议插话" in ev_text, ev_text[:260])
    ck("★ 工具调用写明工具名", "search_web" in ev_text and "fetch_page" in ev_text, ev_text[:260])
    ck("★ AI 行写明 token 与花费", "4411 tokens" in ev_text and "￥" in ev_text,
       ev_text[:300])
    ck("★ 调试行不出现在事件流里", "loaded module" not in ev_text, ev_text[:300])
    ck("★ 页脚点名被隐藏的调试行数",
       "2" in pg.inner_text("#evMore") and "调试" in pg.inner_text("#evMore"),
       pg.inner_text("#evMore"))
    # 分类筛选：五类各自点一次，只能留下自己那一类
    for k, cls in (("tool", "k-tool"), ("cost", "k-cost"), ("decision", "k-decision")):
        pg.click(f'[data-k="{k}"]')
        pg.wait_for_timeout(300)
        only = pg.eval_on_selector_all("#evList .evt", "e => e.map(x=>x.className)")
        ck(f"★ 点「{k}」只留 {k} 事件",
           len(only) >= 1 and all(cls in c for c in only), only)
    pg.click('[data-k=""]')
    pg.wait_for_timeout(300)

    print("\n=== ★ 控制台：原始日志流搬到了这里 ===")
    pg.evaluate("() => go(11)")
    pg.wait_for_timeout(1200)
    cx = pg.inner_text("#cxLog")
    ck("★ 控制台有原始日志（含调试行）",
       "协议层已启动" in cx and "事件端口 3005" in cx, cx[:260])
    ck("★ 日志行数统计在", "3 / 3 行" in pg.inner_text("#cxLogStats"),
       pg.inner_text("#cxLogStats"))
    pg.fill("#cxLogSearch", "3005")
    pg.wait_for_timeout(300)
    ck("★ 原始日志可搜索", "1 / 3 行" in pg.inner_text("#cxLogStats"),
       pg.inner_text("#cxLogStats"))
    pg.fill("#cxLogSearch", "")
    pg.wait_for_timeout(200)

    print("\n=== ★ 插件页：网格 + 1:1 插图位 ===")
    pg.evaluate("() => go(13)")
    pg.wait_for_timeout(1100)
    cards = pg.eval_on_selector_all(".plg-grid .plg", "e => e.length")
    ck("★ 插件用网格卡片而不是三列窄行", cards == 2, cards)
    art = pg.eval_on_selector_all(".plg-art", "e => e.length")
    ck("★ 每张卡都有一个插图位", art == cards, art)
    ratio = pg.eval_on_selector(".plg-art", "e => {const r=e.getBoundingClientRect();"
                                "return Math.abs(r.width - r.height) < 2 ? 1 : r.width/r.height;}")
    ck("★ 插图位是 1:1 正方形", ratio == 1, ratio)
    ck("★ 插图位是「留位」形态（首字母 + 角标），不是破图",
       pg.eval_on_selector_all(".plg-art .ch", "e=>e.length") == cards
       and pg.eval_on_selector_all(".plg-art .rt", "e=>e.length") == cards)
    ck("★ 停用的插件在网格里可见且标了停用",
       "mood_engine" in pg.inner_text(".plg-grid") and "停用" in pg.inner_text(".plg-grid"))

    # 回到配置中心并切回「接入」小节 —— 后面几条断言（只读键、掩码、类型化编辑）
    # 用的都是这一页的控件，前面那几段导航会把它换掉。
    pg.evaluate("() => go(7)")
    pg.wait_for_timeout(1100)
    pg.evaluate("()=>window.__cfGo('basic')")
    pg.wait_for_timeout(400)

    print("\n=== ★ 只读键不能有可编辑入口（providers 含掩码 Key）===")
    editable = pg.eval_on_selector_all(".panel [data-k]", "e => e.map(x=>x.dataset.k)")
    ck("`providers` 不在可编辑控件里", "providers" not in editable, editable[:12])
    ck("`group_restrictions` 也不在（归群管理页）", "group_restrictions" not in editable)
    ck("列表类配置（group_whitelist）可以编辑", "group_whitelist" in editable)

    print("\n=== ★★ 掩码不能被写回（不可逆损坏）===")
    ck("未改动时「保存改动」按钮禁用",
       pg.eval_on_selector("#cSave", "e => e.disabled") is True)
    config_calls.clear()
    pg.fill('[data-k="bot_qq"]', "20002")          # 只改一个普通字段
    pg.wait_for_timeout(400)
    ck("改动后按钮启用且计数正确",
       pg.eval_on_selector("#cSave", "e => !e.disabled") is True
       and "1" in pg.inner_text("#cSave"), pg.inner_text("#cSave"))
    pg.click("#cSave")
    pg.wait_for_timeout(1000)
    saved = [c[1] for c in config_calls if c[0] == "save"]
    ck("确实提交了一次保存", len(saved) == 1, config_calls)
    if saved:
        body = saved[0]
        ck("★ 提交内容只含改动的键（bot_qq）", list(body.keys()) == ["bot_qq"], body)
        ck("★★ 提交内容**不含** api_key（否则真 Key 会被写成占位符）", "api_key" not in body, body)
        ck("★★ 提交内容不含 onebot_token", "onebot_token" not in body, body)
        ck("★★ 提交内容不含 providers（内部全是掩码 Key）", "providers" not in body, body)
        ck("★ 提交的掩码字段值里没有 «redacted",
           not any(isinstance(v, str) and "redacted" in v for v in body.values()), body)

    print("\n=== 类型化编辑 ===")
    config_calls.clear()
    pg.evaluate("() => go(7)")
    pg.wait_for_timeout(1300)
    # 「其他配置项」默认收起（用户定稿）——先展开才能操作兜底区的控件
    pg.evaluate("() => { const h = document.getElementById('cOrphanHd'); if (h && document.getElementById('cOrphanBody').hidden) h.click(); }")
    pg.wait_for_timeout(400)
    pg.select_option('[data-k="protocol_enabled"]', "0")
    pg.wait_for_timeout(400)
    pg.click("#cSave")
    pg.wait_for_timeout(900)
    saved2 = [c[1] for c in config_calls if c[0] == "save"]
    ck("★ 布尔项提交为 false（不是字符串 '0'）",
       bool(saved2) and saved2[0].get("protocol_enabled") is False, saved2)

    config_calls.clear()
    pg.evaluate("() => go(7)")
    pg.wait_for_timeout(1300)
    # ★ 设置项现在按 v3 的面板归位并带中文标签，所以要先切到字段所属面板。
    #   （protocol_enabled 没在 v3 的面板里登记，仍留在下方「其他配置项」区，
    #     那里布尔项还是原生 select —— 两种形态都要能改，这是有意的。）
    pg.evaluate("""(k) => {
      const m = window.FIELD_META.fields[k];
      if (!m) return;
      window.__cfGo(m.panel);
    }""", "ai_temperature")
    pg.wait_for_timeout(350)
    ck("字段已归入带中文标签的面板（温度）",
       pg.evaluate("()=>{const e=document.querySelector('#cfArea [data-k=\\'ai_temperature\\']');"
                   "return !!e && !!e.closest('.cf-fld') && /温度/.test(e.closest('.cf-fld').textContent);}"))
    pg.fill('#cfArea [data-k="ai_temperature"]', "1.25")
    pg.wait_for_timeout(400)
    pg.click("#cSave")
    pg.wait_for_timeout(900)
    saved3 = [c[1] for c in config_calls if c[0] == "save"]
    ck("★ 数字项提交为 number（不是字符串）",
       bool(saved3) and saved3[0].get("ai_temperature") == 1.25, saved3)

    # ── 静默时段（2026-10-24 新增的两个真实字段）──────────────
    # 这两项是"睡觉"的全部人工入口。它们没渲染出来 / 保存不上去，
    # 用户就没有任何办法让 Bot 在凌晨闭嘴，功能等于不存在。
    # 断言到"值真的进了 save 请求"这一层，而不是只看控件在不在。
    config_calls.clear()
    pg.evaluate("() => go(7)")
    pg.wait_for_timeout(1300)
    pg.evaluate("""() => {
      const m = window.FIELD_META.fields['quiet_hours'];
      if (m) window.__cfGo(m.panel);
    }""")
    pg.wait_for_timeout(350)
    ck("静默时段字段渲染在群聊面板",
       pg.evaluate("()=>{const e=document.querySelector('#cfArea [data-k=\\'quiet_hours\\']');"
                   "return !!e && !!e.closest('.cf-fld') && /静默/.test(e.closest('.cf-fld').textContent);}"))
    pg.fill('#cfArea [data-k="quiet_hours"]', "1:00-7:00")
    pg.wait_for_timeout(300)
    pg.select_option('#cfArea [data-k="quiet_when_mentioned"]', "ignore")
    pg.wait_for_timeout(300)
    pg.click("#cSave")
    pg.wait_for_timeout(900)
    saved_q = [c[1] for c in config_calls if c[0] == "save"]
    ck("★ 静默时段与唤醒策略都提交进了 save 请求",
       bool(saved_q) and saved_q[0].get("quiet_hours") == "1:00-7:00"
       and saved_q[0].get("quiet_when_mentioned") == "ignore", saved_q)

    # ── 日志级别（2026-10-24 补的读取方）────────────────────────────
    # 这个键的读取方 logger_setup.get_logger 早就写好了，但 config.py 里
    # 没有 get_log_level 函数，ImportError 被吞 → 日志级别恒为 INFO。
    # 断言到"下拉能选、值进 save"这一层：只渲染不保存等于还是改不了。
    config_calls.clear()
    pg.evaluate("() => go(7)")
    pg.wait_for_timeout(1300)
    pg.evaluate("""() => {
      const m = window.FIELD_META.fields['log_level'];
      if (m) window.__cfGo(m.panel);
    }""")
    pg.wait_for_timeout(350)
    ck("★ 日志级别字段渲染在基础面板",
       pg.evaluate("()=>{const e=document.querySelector('#cfArea [data-k=\\'log_level\\']');"
                   "return !!e && /日志/.test(e.closest('.cf-fld').textContent);}"))
    opts = pg.eval_on_selector_all(
        '#cfArea [data-k="log_level"] option', "e=>e.map(x=>x.value)")
    # 第一项是 v4 通用的「-- 未设置 --」占位（空值），不是第五个级别
    ck("★ 日志级别下拉只有 logging 认的四个值",
       [o for o in opts if o] == ["DEBUG", "INFO", "WARNING", "ERROR"], opts)
    pg.select_option('#cfArea [data-k="log_level"]', "DEBUG")
    pg.wait_for_timeout(300)
    pg.click("#cSave")
    pg.wait_for_timeout(900)
    saved_lv = [c[1] for c in config_calls if c[0] == "save"]
    ck("★ 日志级别提交进了 save 请求",
       bool(saved_lv) and saved_lv[0].get("log_level") == "DEBUG", saved_lv)

    # ── 群聊分条（2026-10-24 补齐的两个键）──────────────────────────
    # 这两个键此前只存在于 example 和 bot_core 兜底里，配置页没有入口：
    # example 写 5、兜底 3、DEFAULT_CONFIG 里没有 → 同一件事三个数。
    # 现在三处统一为 5，并加了入口。断言到"值进了 save 请求"。
    config_calls.clear()
    pg.evaluate("""() => {
      const m = window.FIELD_META.fields['group_send_max_parts'];
      if (m) window.__cfGo(m.panel);
    }""")
    pg.wait_for_timeout(1300)
    ck("★ 单次回复最多拆几条渲染在群聊面板",
       pg.evaluate("()=>{const e=document.querySelector('#cfArea [data-k=\\'group_send_max_parts\\']');"
                   "return !!e && /拆几条/.test(e.closest('.cf-fld').textContent);}"))
    pg.fill('#cfArea [data-k="group_send_max_parts"]', "7")
    pg.wait_for_timeout(300)
    pg.click("#cSave")
    pg.wait_for_timeout(900)
    saved_p = [c[1] for c in config_calls if c[0] == "save"]
    ck("★ 分条上限提交进了 save 请求",
       bool(saved_p) and saved_p[0].get("group_send_max_parts") == 7, saved_p)

    pg.screenshot(path=str(OUT / "_v4_config.png"), full_page=True)

    print("\n=== ★ 配置页的「接入与诊断」动作（旧版有 23 个按钮，不能丢）===")
    pg.evaluate("() => go(7)")
    pg.wait_for_timeout(1300)
    # 2026-10-24：这些按钮属于「接入」分组，切到别的组时整块隐藏（不再永远堆在页面顶部）。
    # 所以检查前先切回接入；只切组不重载页面。
    pg.evaluate("()=>{const b=[...document.querySelectorAll('.cfg-grp')]"
                ".find(x=>x.dataset.grp==='connect'); if(b) b.click();}")
    pg.wait_for_timeout(250)
    btn_ids = ["cWizard", "cTest", "cKeyClear", "cScan", "cLsStart", "cLsStop",
               "cLsTest", "cLsOpen", "cImgTest", "cImgStart", "cImgStop", "cImgOpen"]
    miss = [b for b in btn_ids if pg.eval_on_selector_all(f"#{b}", "e => e.length") == 0]
    ck("★ 12 个动作按钮全部渲染（旧版 23 个按钮的核心动作）", not miss, miss)
    ck("★ 4 个预览按钮渲染",
       pg.eval_on_selector_all("[data-pv]", "e => e.length") == 4,
       pg.eval_on_selector_all("[data-pv]", "e => e.map(x=>x.dataset.pv)"))

    action_calls.clear()
    pg.click("#cTest")
    pg.wait_for_timeout(900)
    ck("★ 测试连接 → POST /llm/test",
       any(k == "llm_test" for k, _ in action_calls), action_calls)
    _t = [c for k, c in action_calls if k == "llm_test"]
    ck("★ 测试连接带上 provider/model",
       bool(_t) and _t[0].get("model_name") and _t[0].get("api_provider"), _t)

    action_calls.clear()
    pg.click("#cScan")
    pg.wait_for_timeout(1200)
    ck("★ 探测本地模型 → POST /llama/scan", any(k == "llama_scan" for k, _ in action_calls))
    ck("探测结果逐条渲染（exe 与 gguf 路径）",
       "llama-server.exe" in pg.inner_text("#cDiag")
       and "qwen.gguf" in pg.inner_text("#cDiag"), pg.inner_text("#cDiag")[:200])

    action_calls.clear()
    pg.click("#cLsStart")
    pg.wait_for_timeout(900)
    _st = [c for k, c in action_calls if k == "llama_start"]
    ck("★ 启动 llama-server → use_local_llm=true 且 driver=llama-server",
       bool(_st) and _st[0].get("use_local_llm") is True
       and _st[0].get("driver") == "llama-server", _st)
    action_calls.clear()
    pg.click("#cLsOpen")
    pg.wait_for_timeout(800)
    ck("★ 打开 8081 → /llama/open {port:8081}",
       any(k == "llama_open" and c.get("port") == 8081 for k, c in action_calls), action_calls)

    action_calls.clear()
    pg.click("#cLsTest")
    pg.wait_for_timeout(800)
    ck("★ 测试本地连接 → POST /llama/test", any(k == "llama_test" for k, _ in action_calls),
       action_calls)
    action_calls.clear()
    pg.click("#cImgStart")
    pg.wait_for_timeout(800)
    ck("★ 启动生图模型 → /llama/start（用 image_llama_* 配置）",
       any(k == "llama_start" for k, _ in action_calls), action_calls)
    action_calls.clear()
    pg.click("#cImgStop")
    pg.wait_for_timeout(700)
    ck("★ 停止生图模型 → /llama/stop", any(k == "llama_stop" for k, _ in action_calls))

    action_calls.clear()
    pg.click("#cImgTest")
    pg.wait_for_timeout(900)
    _it = [c for k, c in action_calls if k == "imagegen_test"]
    ck("★ 测试生图 → /imagegen/test 带 host/port/timeout",
       bool(_it) and _it[0].get("host") and _it[0].get("port") and _it[0].get("timeout"), _it)

    print("\n=== ★ 配置向导（三步）===")
    pg.click("#cWizard")
    pg.wait_for_timeout(700)
    wz = pg.inner_text(".modal")
    ck("向导打开且是三步结构", "步骤 1" in wz and "步骤 2" in wz and "步骤 3" in wz, wz[:220])
    ck("★ 步骤 2 的 Key 是 password 输入",
       pg.eval_on_selector("#wzKey", "e => e.type") == "password")
    ck("★ 选提供商后自动填充 API URL", pg.input_value("#wzUrl") != "", pg.input_value("#wzUrl"))
    action_calls.clear()
    pg.fill("#wzModel", "deepseek-flash")
    pg.click("#wzTest")
    pg.wait_for_timeout(900)
    ck("★ 向导「测试连接」→ /llm/test", any(k == "llm_test" for k, _ in action_calls))
    pg.click("#wzCancel")
    pg.wait_for_timeout(400)
    ck("取消后弹窗关闭", pg.eval_on_selector_all(".modal-ov", "e => e.length") == 0)

    print("\n=== ★ 预览按钮（纯前端）===")
    pg.click('[data-pv="recall_feedback_pre_messages"]')
    pg.wait_for_timeout(600)
    pv = pg.inner_text(".modal")
    ck("★ 预览弹窗打开并列出内容", "撤回了一条消息" in pv, pv[:220])
    ck("★ 显示条数", "共 3 条" in pv, pv[:160])
    pg.click("#pvX")
    pg.wait_for_timeout(300)

    pg.screenshot(path=str(OUT / "_v4_config2.png"), full_page=True)

    print("\n=== ★ 全 15 页冒烟：逐页导航，不得有 JS 报错、不得是「未迁移」占位 ===")
    PAGE_NAMES = ["仪表盘", "协议", "群管理", "表情包", "生图", "群聊行为", "Token",
                  "配置中心", "Bot", "日志", "通知", "控制台", "记忆", "插件", "Agent"]
    for i, nm in enumerate(PAGE_NAMES):
        before = len(errors)
        pg.evaluate("(i) => go(i)", i)
        pg.wait_for_timeout(950)
        title = pg.inner_text("#ttl")
        body = pg.inner_text(".panel")
        ok_title = nm in title or title.strip() == nm
        ok_no_pending = "尚未迁移" not in body
        ok_rendered = len(body.strip()) > 20
        ok_no_err = len(errors) == before
        ck(f"第 {i+1:2d} 页「{nm}」：标题对/已渲染/非占位/无报错",
           ok_title and ok_no_pending and ok_rendered and ok_no_err,
           {"title": title, "pending": not ok_no_pending,
            "len": len(body.strip()), "newErr": errors[before:]})
    ck("★ 全站没有任何页面仍是「未迁移」占位",
       pg.evaluate("() => document.querySelectorAll('.ni.todo').length") == 0,
       pg.evaluate("() => document.querySelectorAll('.ni.todo').length"))

    print("\n=== 新迁移页的关键行为 ===")
    action_calls.clear()
    pg.evaluate("() => go(11)")          # 控制台
    pg.wait_for_timeout(1000)
    pg.fill("#cxIn", "status")
    pg.click("#cxGo")
    pg.wait_for_timeout(900)
    ck("★ 控制台执行指令 → /api/command",
       any(k == "command" and c.get("command") == "status" for k, c in action_calls), action_calls)
    ck("控制台有输出", "运行中" in pg.inner_text("#cxOut"), pg.inner_text("#cxOut")[:120])

    pg.evaluate("() => go(12)")          # 记忆
    pg.wait_for_timeout(1100)
    mm = pg.inner_text(".panel")
    ck("★ 记忆总览读数", "4,080" in mm or "4080" in mm, mm[:200])
    ck("★ 表达模式渲染", "666 太强了" in mm, mm[:260])
    pg.fill("#mmQq", "20002")
    pg.click("#mmGo")
    pg.wait_for_timeout(900)
    ck("★ 按 QQ 查详情有结果", "在的" in pg.inner_text("#mmDetail"), pg.inner_text("#mmDetail")[:200])

    action_calls.clear()
    pg.evaluate("() => go(4)")           # 生图
    pg.wait_for_timeout(1100)
    ig = pg.inner_text(".panel")
    ck("★ 生图状态渲染（在线 + checkpoint）", "在线" in ig and "sd_xl_base" in ig, ig[:240])
    pg.fill("#igP", "a cat")
    pg.click("#igGo")
    pg.wait_for_timeout(1100)
    ck("★ 生成 → /imagegen/generate 带 prompt",
       any(k == "generate" and c.get("prompt") == "a cat" for k, c in action_calls), action_calls)

    action_calls.clear()
    pg.evaluate("() => go(3)")           # 表情包
    pg.wait_for_timeout(1100)
    me = pg.inner_text(".panel")
    ck("★ 表情包列表与标签渲染", "3 张" in me and "可爱" in me, me[:240])
    # v3 的陈列结构：左栏目录 + 标签云；工具条有网格/Masonry 切换
    ck("★ 左栏目录分类与标签云", pg.eval_on_selector_all("#meDirs .it", "e=>e.length") >= 2
       and pg.eval_on_selector_all("#meCloud .tg", "e=>e.length") >= 1,
       (pg.eval_on_selector_all("#meDirs .it", "e=>e.map(x=>x.textContent)"),
        pg.eval_on_selector_all("#meCloud .tg", "e=>e.map(x=>x.textContent)")))
    ck("★ 网格 / Masonry 视图可切换",
       pg.evaluate("()=>{document.getElementById('meViewMas').click();"
                   "return document.getElementById('meGrid').className.indexOf('mas')>0;}"))
    pg.click("#meViewGrid")
    # 目录导入的输入框已换成 v4 模态（Electron 不支持 window.prompt），
    # 不再触发原生 dialog 事件 —— 直接填模态输入框并点确定。
    pg.click("#meImportDir")
    pg.wait_for_timeout(400)
    pg.fill("#ppIn", "D:/pics")
    pg.click("[data-pp=ok]")
    pg.wait_for_timeout(900)
    ck("★ 导入目录 → /memes/import 带 directory",
       any(k == "memes_import" and c.get("directory") == "D:/pics" for k, c in action_calls),
       action_calls)

    print("\n=== 行为 / 通知（聚焦表单）===")
    pg.evaluate("() => go(5)")
    pg.wait_for_timeout(1100)
    bh = pg.inner_text(".panel")
    ck("★ 行为页渲染关键字段",
       "group_idle_exit_probability" in bh and "poke_timeout" in bh, bh[:240])
    pg.evaluate("() => go(10)")
    pg.wait_for_timeout(1100)
    mo = pg.inner_text(".panel")
    ck("★ 通知页渲染", "notify_admin" in mo or "启用管理员通知" in mo, mo[:240])
    ck("★ 通知页没有图表（旧版那个 chartTrend 是死的）",
       pg.eval_on_selector_all(".panel canvas, .panel svg", "e => e.length") == 0)

    pg.evaluate("() => go(1)")
    pg.wait_for_timeout(1100)
    pg.screenshot(path=str(OUT / "_v4_protocol.png"), full_page=True)
    b.close()

srv.shutdown()
print("\n=== JS 报错 ===")
ck("无 JS 报错", not errors, errors[:4])

npass = sum(1 for _, ok, _ in checks if ok)
print("\n" + "=" * 60)
print(f"通过 {npass} / 失败 {len(checks) - npass}")
sys.exit(0 if npass == len(checks) else 1)
