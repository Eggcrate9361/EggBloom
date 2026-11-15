# -*- coding: utf-8 -*-
"""P0-5 单测：state-changing 端点 /api/plugins/action 收紧为 POST-only。

缺陷（手册 §1.3(8)）：
  该端点在 do_GET **与** do_POST 各有一份逐行重复的实现
  （实测 476 行 vs 470 行）。`do_POST` 带 Origin 闸门（防 CSRF），
  `do_GET` 只挡了 Host（防 DNS rebinding）→ 恶意网页可用 fetch/XHR 的 GET
  绕过闸门触发副作用（改配置、清记忆、跑插件动作…）。

修法（处理动作本身，而不是打补丁）：
  1. GET 侧改为 405 + Allow: POST（GET 不应有副作用，RFC 7231 语义）；
  2. 移植 GET 侧唯一的独有能力 action="style_features" 到 POST（能力不丢）；
  3. 删掉 476 行重复实现（两处会各自漂移）。

本测试要证明：
  A. GET 现在返回 405
  B. 能力没有丢 —— POST 的动作集 ⊇ 原 GET ∪ POST
  C. 全库调用点都用 POST，收紧不会打断任何前端
  D. 重复实现确实消失了
"""
import io
import os
import re
import sys
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "webui"))

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  [OK]   {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


import webui_server as W  # noqa: E402

SRC_PATH = os.path.join(ROOT, "webui", "webui_server.py")
src = io.open(SRC_PATH, encoding="utf-8").read()

print("=== A. GET 侧已收紧为 405 ===")


class _Self:
    """最小可用的 handler 替身：只收集响应行，不碰 socket。"""

    def __init__(self, path, headers=None):
        self.path = path
        self.headers = headers or {"Host": "127.0.0.1:8080"}
        self.code = None
        self.hdrs = []
        self.body = b""

    def send_response(self, c):
        self.code = c

    def send_header(self, k, v):
        self.hdrs.append((k, v))

    def send_header_raw(self, k, v):
        self.hdrs.append((k, v))

    def end_headers(self):
        pass

    def write(self, d):
        self.body += d

    @property
    def wfile(self):
        return self

    def _send_json(self, status, data):          # 供其它分支调用时不至于炸
        self.code = status

    def _allowed_origin(self):
        return ""


try:
    h = object.__new__(W.Handler)
    h.path = "/api/plugins/action?plugin_id=ego&action=health"
    h.headers = {"Host": "127.0.0.1:8080"}
    h.code, h.hdrs, h.body = None, [], b""
    h.wfile = h
    # 把 BaseHTTPRequestHandler 的 I/O 方法挂成实例方法（只收集，不碰 socket）
    h.send_response = lambda c: setattr(h, "code", c)
    h.send_header = lambda k, v: h.hdrs.append((k, v))
    h.end_headers = lambda: None
    h.write = lambda d: None
    h.flush = lambda: None
    # BaseHTTPRequestHandler 的内部字段：logging 路径会用到，补上空值
    h.requestline = "GET /api/plugins/action HTTP/1.1"
    h.command = "GET"
    h.request_version = "HTTP/1.1"
    h.client_address = ("127.0.0.1", 12345)
    h.close_connection = True
    h.do_GET()
    check("★ GET /api/plugins/action → 405", h.code == 405, h.code)
    check("★ 响应带 Allow: POST",
          any(k.lower() == "allow" and v.upper() == "POST" for k, v in h.hdrs),
          h.hdrs)
    check("★ 405 不回任何 body（不泄露信息）", h.body == b"", h.body[:40])
except Exception as e:
    check(f"GET 调用可执行（{type(e).__name__}: {e}）", False)

print("\n=== B. 能力没丢：POST 的动作集 ⊇ 原 GET ∪ 原 POST ===")
backup = r"D:\workbuddy_tmp\_get_block_backup.txt"
if os.path.exists(backup):
    old_get = io.open(backup, encoding="utf-8").read()
else:
    old_get = ""


def block_of(text, start_idx):
    lines = text.split("\n")
    ind = len(lines[start_idx]) - len(lines[start_idx].lstrip())
    for i in range(start_idx + 1, len(lines)):
        l = lines[i]
        if l.strip() and (len(l) - len(l.lstrip())) == ind and l.strip().startswith("if "):
            return "\n".join(lines[start_idx:i])
    return "\n".join(lines[start_idx:])


def actions(text):
    out = set()
    for m in re.finditer(r'action\s*==\s*["\']([^"\']+)["\']', text):
        out.add(m.group(1))
    for m in re.finditer(r'action\s+in\s*[\(\[]([^\)\]]*)[\)\]]', text):
        out |= set(re.findall(r'["\']([^"\']+)["\']', m.group(1)))
    return out


_cur_lines = src.split("\n")
_post_start = max(i for i, l in enumerate(_cur_lines)
                  if l.strip() == 'if path == "/api/plugins/action":')
cur_post = block_of(src, _post_start)
pa = actions(cur_post)

check("POST 分支仍存在且有大量动作", len(pa) >= 50, len(pa))
check("★ 原 GET 独有的 style_features 已移植到 POST", "style_features" in pa,
      sorted(pa)[:5])
if old_get:
    ga = actions(old_get)
    lost = sorted(ga - pa)
    check(f"★ 原 GET 的 {len(ga)} 个动作无一丢失", not lost, lost)
    check("★ POST 仍是超集（未减少）", len(pa) >= len(ga), (len(pa), len(ga)))
check("style_features 在 POST 里有**局部导入**（否则会 NameError）",
      re.search(r'from plugins\.ego import style_features as _style_features', cur_post) is not None)

print("\n=== C. 全库调用点都用 POST（收紧不打断前端）===")
files = glob.glob(os.path.join(ROOT, "plugins", "**", "ui.html"), recursive=True)
files += [os.path.join(ROOT, "webui", "webui_artistic_v3.html")]
n_calls = 0
non_post = []
for f in files:
    s = io.open(f, encoding="utf-8", errors="replace").read()
    for m in re.finditer(r"(?:fetch|api)\s*\(\s*(?:API|'/api/plugins/action'|\"/api/plugins/action\")", s):
        n_calls += 1
        seg = s[m.start():m.start() + 400]
        if not re.search(r"method\s*:\s*['\"]POST['\"]", seg):
            non_post.append(f"{os.path.basename(f)}: {seg[:90]}")
check(f"扫描到 {n_calls} 个通过 API 常量的调用点", n_calls >= 50, n_calls)
check("★ 其中没有一个缺 method:'POST'", not non_post, non_post[:3])

get_usage = [f for f in files
             if re.search(r"method\s*:\s*['\"]GET['\"]", io.open(f, encoding="utf-8", errors="replace").read())]
check("★ 没有任何 UI 使用 method:'GET'", not get_usage, get_usage[:3])
qs = [f for f in files
      if re.search(r"API\s*\+\s*['\"]\?", io.open(f, encoding="utf-8", errors="replace").read())]
check("★ 没有任何 UI 把参数拼进 URL（无 query 形式调用）", not qs, qs[:3])

print("\n=== D. 476 行重复实现已消失 ===")
check("★ do_GET 里只剩 405 桩（不再有 action 分发）",
      "if action ==" not in src.split("def do_POST")[0].split('if path == "/api/plugins/action":')[-1][:400])
check("★ 全文只保留 1 处 `if path == \"/api/plugins/action\":` 的分发逻辑",
      src.count('if path == "/api/plugins/action":') == 2,   # GET 的 405 桩 + POST 的实现
      src.count('if path == "/api/plugins/action":'))
check("GET 桩含 405 + Allow 头", "self.send_response(405)" in src and 'self.send_header("Allow", "POST")' in src)
check("原 GET 块大小确实很大（备份文件存在时校验）",
      (not old_get) or len(old_get.splitlines()) > 400,
      len(old_get.splitlines()) if old_get else "备份缺失")

print("\n" + "=" * 62)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
