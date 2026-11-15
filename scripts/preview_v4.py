# -*- coding: utf-8 -*-
"""v4 界面预览服务（带 mock 数据）—— 纯设计预览用，不连真实后端。

    python scripts/preview_v4.py [端口]      # 默认 8848
然后打开 http://127.0.0.1:8848/webui_artistic_v4.html

为什么要它：真实后端没起（或 8080 返回 502）时，页面会因为 /api 失败而一片"读取中"，
没法评审设计。这个服务把 webui/ 静态资源 + 一组像样的 mock 数据一起提供，
打开就能看到完整界面。**它只读、不写、不碰真实数据。**
"""
import functools
import http.server
import json
import os
import socketserver
import sys
import threading
import time

WEBUI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webui")

MOCK = {
    "/api/status": {"bot": {"status": "running", "uptime": 4281},
                    "protocol_running": True, "protocol_driver": "napcat",
                    "api_port": 5700, "event_port": 5701, "groups": 12,
                    "messages_today": 4080, "memes": 37,
                    "napcat_version": "4.0.12", "qq": "10003"},
    "/api/groups": {"groups": [
        {"gid": "200000004", "name": "沙漏测试群", "members": 128, "active": True},
        {"gid": "963258741", "name": "零号频道", "members": 2044, "active": True},
        {"gid": "741852963", "name": "深夜食堂", "members": 37, "active": False},
    ]},
    "/api/plugins": {"plugins": [
        {"name": "web_operator", "enabled": True, "desc": "全能操作"},
        {"name": "web_search", "enabled": True, "desc": "联网搜索"},
        {"name": "meme", "enabled": False, "desc": "表情包"},
        {"name": "scheduler", "enabled": True, "desc": "定时任务"},
    ]},
    "/api/config": {"config": {"group_tool_allowlist": ["get_group_members", "generate_image"],
                              "session_max_rounds": 30, "ai_model": "deepseek-v4-flash"}},
    "/api/tokens": {"snapshot": {"cumulative": {
        "group": {"requests": 812, "input": 182034, "output": 24110, "cache_hit": 90211},
        "private": {"requests": 1436, "input": 341228, "output": 51220, "cache_hit": 210433},
        "web": {"requests": 96, "input": 41220, "output": 3380, "cache_hit": 20114}},
        "scenes": {"group": {}, "private": {}, "web": {}}},
        "recent": [{"scene": "group", "total_tokens": 1840, "cache_hit_rate": "62%",
                    "time": "2026-10-07 22:41:03"}]},
    "/api/scope-policy": {"default_preset": "standard",
                          "presets": {"standard": {}, "readonly": {}, "full": {}, "custom": {}},
                          "capabilities": {"reply": "low", "generate_image": "mid"},
                          "stats": {"scopes": 2, "events": 5, "overrides": 1, "degraded": 0},
                          "audit": []},
    "/api/logs": {"logs": [
        {"time": "22:41:03", "level": "info", "msg": "消息处理完成 · 182ms"},
        {"time": "22:41:07", "level": "info", "msg": "缓存命中 62%"},
        {"time": "22:41:11", "level": "warn", "msg": "工具调用超时 1 次（已降级）"},
        {"time": "22:41:15", "level": "info", "msg": "群 200000004 触发主动搭话"},
    ]},
    "/api/chatlogs": {"recent": [
        {"scene": "group", "total_tokens": 1840, "cache_hit_rate": "62%", "time": "22:40:11"},
        {"scene": "private", "total_tokens": 2310, "cache_hit_rate": "71%", "time": "22:39:52"},
    ]},
    "/api/protocol/status": {"protocol_running": True, "protocol_driver": "napcat",
                             "api_port": 5700, "event_port": 5701, "risks": {},
                             "drivers": ["napcat"]},
}


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")     # ★ 每次都是最新版，别吃缓存
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in MOCK:
            return self._json(MOCK[path])
        if path.startswith("/api/"):
            return self._json({})                        # 其余接口给空对象，不报错
        return super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            try:
                self.rfile.read(n)
            except Exception:
                pass
        return self._json({"ok": True, "preview": True})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8848
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", port), functools.partial(H, directory=WEBUI))
    print("v4 预览（mock 数据，只读）→ http://127.0.0.1:%d/webui_artistic_v4.html" % port)
    print("按 Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()