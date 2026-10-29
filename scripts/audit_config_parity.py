# -*- coding: utf-8 -*-
"""配置中心（v4）功能对等验收：逐项核对 v3 的设置项是否真的能改、能存。

验收清单（对着 v3 一个字段一个字段核）：
  A. 面板与字段   —— v3 的 15 个面板都出现，字段总数不小于 v3 的登记数
  B. 控件形态     —— 开关是 .sw 而不是"开启/关闭"下拉；退场语是划线列表不是 JSON 框
  C. 交互         —— 切面板、加退场语、勾停用（划线）、改数字 → 改动计数会动
  D. 保存         —— 提交体里含嵌套键 de_ai.enabled、列表 custom_exit_phrases_short
  E. 不重复       —— 同一个键不会既在面板里、又在下方"其他配置项"里出现两次

用法：python scripts/audit_config_parity.py
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
META = os.path.join(HERE, "_fieldmeta.json")

from playwright.sync_api import sync_playwright

EXE = os.environ.get("PW_CHROME") or os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "ms-playwright", "chromium-1228",
    "chrome-win64", "chrome.exe")
# ★ 必须走 HTTP：Chrome 的 fetch() 对 file:// 地址**在 API 层直接拒绝**（"Failed to fetch"，
#   请求根本不会发出，Playwright 路由也就拦不到）→ 配置页拿不到夹具数据 → 面板渲染不出。
#   （这条曾让审计偶发全崩、只剩两条检查通过。起个本地 http 服务一劳永逸。）
import http.server
import socketserver
import threading

def _serve():
    _h = http.server.SimpleHTTPRequestHandler
    _h.log_message = lambda *a, **k: None
    with socketserver.TCPServer(("127.0.0.1", 0), _h) as _httpd:
        _PORTS.append(_httpd.server_address[1])
        _httpd.serve_forever()

_PORTS = []
_t = threading.Thread(target=_serve, daemon=True)
_t.start()
import time as _time
_time.sleep(0.6)
URL = "http://127.0.0.1:%d/webui/webui_artistic_v4.html" % _PORTS[0]

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + ("" if cond else "  → " + str(detail)[:300]))


def cfg_fixture():
    return {
        "bot_qq": "10003", "bot_group_nick": "小a", "startup_cooldown": 3,
        "target_qqs": ["10003"], "group_ids": ["20001"],
        "ai_temperature": 0.8, "ai_max_tokens": 2048, "ai_top_p": 0.9,
        "ai_frequency_penalty": 0, "ai_presence_penalty": 0,
        "session_ttl": 1800, "session_max_rounds": 30, "private_reply_interval": 5,
        "idle_timeout": 600, "private_reply_max_length": 300, "group_reply_max_length": 120,
        "msg_split_len": 200, "poke_timeout": 600,
        "group_msg_buffer_window": 5, "group_recent_msgs_window": 20,
        "group_session_max_replies": 3, "group_session_idle_timeout": 600,
        "group_last_exit_cooldown": 120, "group_idle_exit_enable": True,
        "group_idle_exit_probability": 0.3, "group_idle_exit_min_silence": 300,
        "group_msg_buffer_window_session": 20, "group_msg_buffer_window_trigger": 5,
        "group_trigger_words": ["小a"],
        "group_trigger_include_bot_nick": True, "group_trigger_include_text_at": True,
        "group_trigger_include_quote": True,
        "group_tool_allowlist": ["get_group_members", "generate_image"],
        "command_permission": "strict", "command_admins": ["123"],
        "admin_qqs": ["123"], "admin_commands_enable": True,
        "de_ai": {"enabled": True, "prompt_inject": True, "post_process": True,
                  "ban_words": True, "ban_dashes": True, "ban_rule_of_three": True,
                  "ban_filler": True, "ban_sycophancy": True, "max_sentence_len": 80},
        "custom_exit_phrases_short": [{"text": "我先下了", "disabled": False},
                                      {"text": "溜了溜了", "disabled": True}],
        "custom_exit_phrases_idle": [{"text": "有人吗", "disabled": False}],
        "recall_feedback_pre_messages": [{"text": "我这条被撤回啦？", "disabled": False}],
        "recall_feedback_post_messages": [{"text": "当我没说", "disabled": False}],
        "recall_private_post_messages": [{"text": "撤回就撤回吧", "disabled": False}],
        "recall_feedback_enable": True, "recall_feedback_cooldown": 30,
        "recall_private_enable": True, "recall_private_cooldown": 60,
        "meme_enable": True, "meme_cooldown": 5, "meme_idle_exit_enable": False,
        "meme_auto_detect_emotion": True, "image_cache_enable": True,
        "private_memory_enable": True, "self_learning_enable": True,
        "slang_injector_enable": True, "nsfw_enabled": False,
        "auto_accept": True, "auto_accept_friend": True, "auto_accept_group_add": True,
        "auto_accept_group_invite": True, "auto_accept_welcome_new": True,
        "auto_accept_add_targets": [], "auto_accept_welcome_friend": "你好",
        "auto_accept_welcome_group": "欢迎", "auto_accept_blacklist": [],
        "llama_driver": "ollama", "llama_port": 8081, "llama_model_name": "qwen",
        "llama_exe_path": "", "llama_model_path": "", "llama_search_root": "",
        "use_local_llm": False, "use_local_image_llm": False,
        "image_llm_driver": "lmstudio", "image_llm_port": 8082,
        "image_llm_model_name": "", "image_llm_exe_path": "", "image_llm_model_path": "",
        "api_url": "https://api.deepseek.com", "model_name": "deepseek-v4-flash",
        "api_provider": "deepseek", "api_key": "sk-***",
        "providers": [{"id": "deepseek", "name": "DeepSeek", "models": []}],
        "unknown_new_key": "保持原样", "some_object": {"a": 1},
    }



# ═══════════════════════════════════════════════════════════════════
# 1.5.10 E 批：有键必有读取点（纯代码静态检查，不启浏览器）
# ═══════════════════════════════════════════════════════════════════
#: 已知 0 读取点、但暂不删的键（写了理由才允许留在这里）
_READPOINT_ALLOW = {
    "refuse_soften_ratio": "1.5.9 遗留，未接进 reply_gate；1.5.11 决定删除或接线",
    "refuse_nag_after_done": "同上",
}


def _config_readpoints():
    """扫 .py 源码，统计 DEFAULT_CONFIG 的每个键有没有"读取点"。

    读取点 = 源码里出现 `get("key"` / `["key"]` / `config.get("key")` 形态。
    只在 config.py 里定义、其它地方一次都没读的键，就是"配了不生效"。
    """
    import config as _cfgmod
    src = ""
    for root, _dirs, files in os.walk(ROOT):
        if any(x in root for x in (".git", "data", "__pycache__", "node_modules", "_fontwalk")):
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(root, f)
            if os.path.abspath(p) == os.path.abspath(os.path.join(ROOT, "config.py")):
                continue
            try:
                src += io.open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
    missing = []
    for key in _cfgmod.DEFAULT_CONFIG:
        # 读取点包括：get("k") / 下标 / 作为函数参数传字面量（_delay_cfg("k", …)）
        pats = (f'get("{key}"', f"get('{key}'", f'["{key}"]', f"['{key}']",
                f'("{key}"', f"('{key}'")
        if not any(p in src for p in pats):
            missing.append(key)
    return missing


def check_config_readpoints():
    missing = _config_readpoints()
    real = [k for k in missing if k not in _READPOINT_ALLOW]
    for k in missing:
        if k in _READPOINT_ALLOW:
            print(f"  [SKIP] {k}：{_READPOINT_ALLOW[k]}")
    check("★ 每个配置键都有读取点（允许清单 %d 条）" % len(_READPOINT_ALLOW),
          not real, real)


def main():
    check_config_readpoints()
    print()
    meta = json.load(io.open(META, encoding="utf-8"))
    want_panels = [p[0] for p in meta["panels"] if any(
        f["panel"] == p[0] for f in meta["fields"].values())]
    want_fields = len(meta["fields"])

    saved = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=EXE if os.path.exists(EXE) else None)
        pg = b.new_page(viewport={"width": 1440, "height": 1100})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        body = json.dumps(cfg_fixture())

        def on_post(route):
            saved.update(json.loads(route.request.post_data or "{}"))
            route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
        # ⚠️ 注册顺序有讲究：Playwright 的路由是**后注册者优先**，
        #    所以通配的 **/api/** 必须先注册，否则它会抢走 /api/config，
        #    页面拿到空配置 —— 表现是"字段渲染了但全是空值"，极难查。
        pg.route("**/api/**", lambda r: r.fulfill(
            status=200, content_type="application/json", body="{}"))
        pg.route("**/api/config", lambda r: (on_post(r) if r.request.method == "POST"
                  else r.fulfill(status=200, content_type="application/json",
                                 body=json.dumps(cfg_fixture(), ensure_ascii=False))))
        pg.goto(URL, wait_until="load")
        pg.wait_for_timeout(1200)
        pg.evaluate("()=>go(7)")           # 配置中心
        pg.wait_for_timeout(1400)

        check("页面无 JS 报错", not errs, errs[:3])
        # ★ 2026-10-24：配置页从 15 个平级标签改成 6 个分组 + 组内小节。
        #   判据跟着改：每个面板要能在**它所属分组**里找到对应的小节标题。
        groups = pg.eval_on_selector_all(".cfg-grp", "els=>els.map(e=>e.dataset.grp)")
        check("★ 配置页有 %d 个分组" % 6, len(groups) == 6, groups)
        seen_panels, total, shapes = [], 0, {}
        for g in groups:
            pg.evaluate("(g)=>{[...document.querySelectorAll('.cfg-grp')]"
                        ".find(t=>t.dataset.grp===g).click()}", g)
            pg.wait_for_timeout(160)
            secs = pg.evaluate("""()=>[...document.querySelectorAll('.cfg-sec[data-sec]')].map(s=>({
              panel: s.dataset.sec,
              title: (s.querySelector('.cfg-sec-hd b')||{}).textContent || '',
            }))""")
            for s in secs:
                if s["panel"] != "ui":
                    seen_panels.append(s["panel"])
                    shapes[s["panel"]] = pg.evaluate("""(p)=>{const s=document.querySelector(
                        '.cfg-sec[data-sec="'+p+'"]'); if(!s) return {};
                      return {
                        sw: s.querySelectorAll('.sw').length,
                        edl: s.querySelectorAll('.edl-row').length,
                        lines: s.querySelectorAll('[data-kind="lines"]').length,
                        sel: s.querySelectorAll('select').length,
                        num: s.querySelectorAll('input[type=number]').length,
                        raw: s.querySelectorAll('textarea[data-k]:not([data-kind])').length
                      }; }""", s["panel"])
                    total += pg.eval_on_selector_all(
                        '.cfg-sec[data-sec="%s"] .cf-fld' % s["panel"], "e=>e.length")
        check("★ v3 的 %d 个面板全部有归属" % len(want_panels),
              all(p in seen_panels for p in want_panels),
              sorted(set(want_panels) - set(seen_panels)))
        check("★ 没有面板被两个分组同时收录",
              len(seen_panels) == len(set(seen_panels)),
              [x for x in seen_panels if seen_panels.count(x) > 1])
        check("★ 字段总数 %d ≥ v3 登记的 %d" % (total, want_fields), total >= want_fields,
              "实际 %d" % total)
        check("布尔项渲染为方块开关（不是下拉）", sum(v["sw"] for v in shapes.values()) >= 30,
              sum(v["sw"] for v in shapes.values()))
        pg.evaluate("()=>window.__cfGo('group')")
        pg.wait_for_timeout(200)
        n_reset = pg.eval_on_selector_all("#cfArea [data-reset]", "e=>e.length")
        check("★ 字段能点回默认值（群聊组 %d 个）" % n_reset, n_reset >= 5, n_reset)
        check("退场语/撤回反馈是划线列表（.edl-row）", sum(v["edl"] for v in shapes.values()) >= 4,
              sum(v["edl"] for v in shapes.values()))
        check("每行一条的文本框存在", sum(v["lines"] for v in shapes.values()) >= 5,
              sum(v["lines"] for v in shapes.values()))
        check("没有字段退回 JSON 裸编辑框",
              sum(v["raw"] for v in shapes.values()) == 0,
              sum(v["raw"] for v in shapes.values()))

        # 划线语义：停用条目带 .off。退场面板有两组列表（短退场语 2 条 + 空闲退场语 1 条），
        # 第一组里第 2 条 fixture 就是 disabled:true
        pg.evaluate("()=>window.__cfGo('exit')")
        pg.wait_for_timeout(300)
        offs = pg.evaluate("()=>[...document.querySelectorAll('.edl-row')].map(r=>r.classList.contains('off'))")
        check("★ 停用的退场语显示为划线（.off）", offs[:2] == [False, True] and len(offs) == 3, offs)
        check("★ 列表行文案读的是配置内容（不是 key）",
              pg.evaluate("()=>[...document.querySelectorAll('.edl-txt')].map(e=>e.textContent)").count("我先下了") == 1,
              pg.evaluate("()=>[...document.querySelectorAll('.edl-txt')].map(e=>e.textContent)"))

        # 交互：加一条 + 勾一个停用 → 改动计数变化
        before = pg.evaluate("()=>document.getElementById('cDirty').textContent")
        pg.fill("[data-edl-add]", "那我走咯")
        pg.click("[data-edl-push]")
        pg.wait_for_timeout(320)
        cnt = pg.evaluate("()=>document.querySelectorAll('.edl-row').length")
        check("从多行文本加入一条退场语（3 → 4）", cnt == 4, cnt)
        pg.evaluate("()=>document.querySelectorAll('.edl-chk')[0].click()")
        pg.wait_for_timeout(220)
        after = pg.evaluate("()=>document.getElementById('cDirty').textContent")
        check("改动计数随列表变化", before != after, "%s → %s" % (before, after))

        # 开关与数字框
        pg.evaluate("()=>window.__cfGo('switches')")
        pg.wait_for_timeout(320)
        sw = pg.evaluate("""()=>{const b=document.querySelector('#cfArea .sw');const k=b.dataset.sw;
            const was=b.getAttribute('aria-checked');b.click();
            return {k,was,now:b.getAttribute('aria-checked'),
                    dirty:document.getElementById('cDirty').textContent};}""")
        check("开关点击翻转 aria-checked 且计入改动", sw["was"] != sw["now"] and "改动" in sw["dirty"], sw)

        # 嵌套键（de_ai.enabled）走的是另一条路径：必须组装成 {de_ai:{enabled:…}}
        pg.evaluate("()=>window.__cfGo('deai')")
        pg.wait_for_timeout(320)
        pg.evaluate("()=>{const b=document.querySelector('#cfArea [data-sw=\\'de_ai.enabled\\']');"
                    "if(b) b.click();}")
        pg.wait_for_timeout(220)
        # 数字框：会话面板的对话超时
        pg.evaluate("()=>window.__cfGo('session')")
        pg.wait_for_timeout(320)
        pg.fill("#cfArea input[type=number]", "4242")
        pg.wait_for_timeout(240)

        pg.click("#cSave")
        pg.wait_for_timeout(900)
        check("★ 保存提交了嵌套键 de_ai.enabled", "de_ai" in saved and isinstance(saved.get("de_ai"), dict)
              and "enabled" in (saved.get("de_ai") or {}), list(saved.keys())[:8])
        check("★ 保存提交了退场语列表（含 disabled）",
              isinstance(saved.get("custom_exit_phrases_short"), list)
              and any(isinstance(x, dict) and "disabled" in x
                      for x in saved["custom_exit_phrases_short"]),
              saved.get("custom_exit_phrases_short"))
        check("保存提交了数字改动", any(v == 4242 for v in saved.values())
              or "session_ttl" in saved, list(saved.items())[:6])

        # 不重复：面板里出现的键，不该在下方"其他配置项"里再出现
        dup = pg.evaluate("""()=>{
          const inPanel = new Set([...document.querySelectorAll('#cfArea [data-k],#cfArea [data-sw],#cfArea [data-edl-push]')]
            .map(e=>e.dataset.k||e.dataset.sw||e.dataset.edlPush).filter(Boolean));
          const other = [...document.querySelectorAll('#cSections [data-k]')].map(e=>e.dataset.k);
          return other.filter(k=>inPanel.has(k));
        }""")
        check("★ 同一个键不会既在面板、又在下方其他配置项里重复出现", not dup, dup[:6])
        pg.screenshot(path=os.path.join(ROOT, "_fontwalk", "cfg_panels.png"), full_page=False)
        b.close()

    print("\n" + "=" * 62)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项:", FAIL)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())