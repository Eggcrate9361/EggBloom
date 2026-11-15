# -*- coding: utf-8 -*-
"""
web_meme_finder：用「DeepSeek 网页版 + 强制联网」查指定游戏的「热且出圈」梗，替代原 _llm_enrich 的离线生成。

设计要点（对应需求）：
1. 多浏览器环境：自动探测 CDP Edge(9222) / CDP Chrome(9223) / 系统 Edge / 系统 Chrome / 自带 playwright chromium。
2. 环境自备：优先复用已启动的 CDP 浏览器（保留机主登录态）；否则自动拉起独立浏览器窗口，
   登录态保存在插件 data/browser_profile 下，机主首次登录一次即可，后续自动复用，无需手动配置。
3. 操作提示：enrich() 失败时抛 WebMemeFinderError，message 是给机主看的中文提示（如「DeepSeek 未登录…」）；
   status() 返回环境 + 登录态，UI 据此显示提示与登录提醒。

用法：
    库调用：from web_meme_finder import enrich, status
    命令行：python web_meme_finder.py --game 明日方舟 --batch 5
            python web_meme_finder.py --status
"""
import json
import os
import re
import subprocess
import sys
import time

SITE = os.environ.get("DS_SITE", "https://chat.deepseek.com/")
QUERY_TIMEOUT = int(os.environ.get("DS_QUERY_TIMEOUT", "150"))  # 秒

def _browser_candidates(rel_subpaths):
    """由 ProgramFiles/ProgramFiles(x86)/LOCALAPPDATA 环境变量推导浏览器可执行路径，不写死本机路径。"""
    bases = []
    for v in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        if os.environ.get(v):
            bases.append(os.environ[v])
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        bases.append(local)
    return [os.path.join(b, *r.split("\\")) for b in bases for r in rel_subpaths]


_EDGE_CANDIDATES = _browser_candidates([
    "Microsoft\\Edge\\Application\\msedge.exe",
])
_CHROME_CANDIDATES = _browser_candidates([
    "Google\\Chrome\\Application\\chrome.exe",
])


class WebMemeFinderError(RuntimeError):
    pass


def _profile_dir() -> str:
    """浏览器独立配置目录（保留登录态）。位于 插件/data/browser_profile。"""
    here = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(here, "data", "browser_profile")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


# ---------------------------------------------------------------- CDP 探测
def _cdp_alive(port) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _find_exe(candidates) -> str:
    for c in candidates:
        if os.path.exists(c):
            return c
    return ""


def _launch_debug_browser(exe: str, port: int) -> None:
    """以调试模式拉起指定浏览器（独立 profile），并等待 CDP 就绪。"""
    profile = os.path.join(_profile_dir(), f"cdp-{port}")
    try:
        subprocess.Popen(
            [
                exe,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                SITE,
            ],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for _ in range(15):
            time.sleep(2)
            if _cdp_alive(port):
                return
    except Exception as e:
        raise WebMemeFinderError(f"启动调试浏览器失败: {e}")
    raise WebMemeFinderError(f"CDP 端口 {port} 无法连接（浏览器未成功启动）")


# ---------------------------------------------------------------- 环境检测
def detect_browsers() -> dict:
    """返回本机可用浏览器清单。"""
    edge = _find_exe(_EDGE_CANDIDATES)
    chrome = _find_exe(_CHROME_CANDIDATES)
    chromium = ""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            chromium = p.chromium.executable_path
    except Exception:
        chromium = ""
    return {
        "cdp_edge": _cdp_alive(9222),
        "cdp_chrome": _cdp_alive(9223),
        "edge": edge,
        "chrome": chrome,
        "chromium": chromium,
    }


def status() -> dict:
    """环境 + DeepSeek 登录态检查，供 UI 显示操作提示与登录提醒。"""
    browsers = detect_browsers()
    hints = []

    cdp_ready = browsers.get("cdp_edge") or browsers.get("cdp_chrome")
    if cdp_ready:
        hints.append("检测到已启动的调试浏览器（CDP），将复用其登录态，无需重复登录。")
    else:
        any_exe = browsers.get("edge") or browsers.get("chrome") or browsers.get("chromium")
        if any_exe:
            hints.append("将自动拉起独立浏览器窗口。首次使用请在窗口里登录 chat.deepseek.com，登录态会自动保存，下次直接使用。")
        else:
            hints.append("未找到 Edge / Chrome / 自带 Chromium，联网查梗功能暂不可用。")

    login_state = None
    if cdp_ready:
        try:
            login_state = _check_login_via_cdp(browsers)
        except Exception:
            login_state = None
        if login_state is False:
            hints.append("DeepSeek 未登录：请在已打开的浏览器窗口登录 chat.deepseek.com 后重试。")
        elif login_state is True:
            hints.append("DeepSeek 已登录，可以直接联网查梗。")

    return {"browsers": browsers, "login": login_state, "hints": hints}


def _check_login_via_cdp(browsers) -> bool | None:
    """连 CDP 检查 DeepSeek 登录态。True=已登录, False=未登录, None=无法判断。"""
    from playwright.sync_api import sync_playwright

    ports = []
    if browsers.get("cdp_edge"):
        ports.append(9222)
    if browsers.get("cdp_chrome"):
        ports.append(9223)
    for port in ports:
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                pg = None
                for ctx in browser.contexts:
                    for _p in ctx.pages:
                        if "deepseek" in _p.url:
                            pg = _p
                            break
                    if pg:
                        break
                if pg is None:
                    continue
                body = pg.evaluate("()=>document.body.innerText") or ""
                url = pg.url or ""
                if "/sign_in" in url or "登录" in body[:400]:
                    return False
                return True
        except Exception:
            continue
    return None


# ---------------------------------------------------------------- 选择浏览器
def _pick_browser(browsers):
    """选择策略：CDP Edge > CDP Chrome > 自带 chromium（环境自备）。"""
    if browsers.get("cdp_edge"):
        return {"mode": "cdp", "port": 9222}
    if browsers.get("cdp_chrome"):
        return {"mode": "cdp", "port": 9223}
    return {"mode": "launch"}


# ---------------------------------------------------------------- 页面操作
def _get_deepseek_page(browser):
    """在浏览器里找到或新建一个 DeepSeek 页面。"""
    pg = None
    for ctx in browser.contexts:
        for _p in ctx.pages:
            if "deepseek" in _p.url:
                pg = _p
                break
        if pg:
            break
    if pg is None:
        ctx = browser.contexts[0] if browser.contexts else None
        if ctx is None:
            raise WebMemeFinderError("浏览器上下文不可用")
        pg = ctx.new_page()
        pg.goto(SITE, timeout=60000)
        time.sleep(5)
    return pg


def _ensure_online(pg):
    """确保「智能搜索」开关开启（首次点击会误关，需检查选中态再点）。"""
    js_switch = (
        "() => {"
        "const els=[...document.querySelectorAll('.ds-toggle-button')];"
        "const e=els.find(x=>x.innerText.trim()==='智能搜索');"
        "if(!e) return null;"
        "const r=e.getBoundingClientRect();"
        "return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};"
        "}"
    )
    js_state = (
        "() => {"
        "const els=[...document.querySelectorAll('.ds-toggle-button')];"
        "const e=els.find(x=>x.innerText.trim()==='智能搜索');"
        "return e?e.className.includes('selected'):false;"
        "}"
    )
    try:
        on = pg.evaluate(js_state)
        if not on:
            pos = pg.evaluate(js_switch)
            if pos:
                pg.mouse.click(pos["x"], pos["y"])
                time.sleep(1)
    except Exception:
        pass


def _check_login_on_page(pg):
    """检查当前页面是否已登录 DeepSeek。返回 True/False。"""
    try:
        body = pg.evaluate("()=>document.body.innerText") or ""
        url = pg.url or ""
        if "/sign_in" in url or "登录" in body[:400]:
            return False
        return True
    except Exception:
        return True


DEFAULT_TEMPLATE = (
    "今天2026年8月。请开启联网搜索，查询游戏【{game}】里最热且最出圈的梗/黑话/名场面 "
    "{want} 个。\n"
    "标准：必须是这个游戏里真实存在、够火、而且出圈的（圈外普通网友也常拿来开玩笑/刷弹幕/当表情包的），"
    "不要求最新，够热够出圈就行；不要编造，不要那种只有老玩家才知道的小众梗；避开以下已收录词：{banned}\n"
    "直接输出严格 JSON 数组，不要任何其他文字。每个元素："
    '{"id":"梗词","meaning":"意思。含使用场景、来源平台、在对局/社区里的用法、文化影响。",'
    '"reply":"对方说出这个词时群友通常怎么接话？给1-3个一句话接话示例，用/分隔，口语、带梗、直接能发，禁止解释句式。例：真的假的？/我靠真开播了/别催了他开着呢"}\n'
    "请务必只输出一个纯 JSON 数组，不要 markdown 代码块，不要在 JSON 内添加任何编号、引用标记、换行符。"
)


def _ask(pg, game, batch, existing_terms, template=""):
    tb = pg.query_selector("textarea")
    if not tb:
        raise WebMemeFinderError("DeepSeek 输入框未找到（页面可能未加载或已改版）")

    if not _check_login_on_page(pg):
        raise WebMemeFinderError(
            "DeepSeek 未登录，无法联网查梗。请在浏览器窗口里登录 chat.deepseek.com 后，重新点击「联网查新梗」。"
        )

    want = max(int(batch), 1)
    banned = "、".join(existing_terms[:60]) if existing_terms else "（无）"
    tpl = (template or "").strip() or DEFAULT_TEMPLATE
    try:
        prompt = tpl.format(game=game, batch=batch, want=want, banned=banned)
    except (KeyError, IndexError, ValueError) as e:
        raise WebMemeFinderError(f"自定义模板格式错误（占位符需为 {{game}}/{{batch}}/{{want}}/{{banned}}）：{e}")

    _ensure_online(pg)
    tb.fill("")
    time.sleep(0.5)
    tb.fill(prompt)
    time.sleep(0.5)
    tb.press("Enter")

    deadline = time.time() + QUERY_TIMEOUT
    last = ""
    text = ""
    while time.time() < deadline:
        time.sleep(3)
        try:
            text = pg.evaluate("()=>document.body.innerText")
        except Exception:
            text = last
        if "停止生成" in text:
            last = text
            continue
        if text != last and "停止生成" not in text:
            last = text
            continue
        else:
            break
    return text


def _parse(text):
    """从 DeepSeek 回复里提取 JSON 数组，返回 [{id, meaning}]。"""
    if not text:
        return []
    idx = text.rfind('"id"')
    if idx < 0:
        idx = text.rfind('"term"')
    if idx < 0:
        return []
    start = text.rfind("[", 0, idx)
    if start < 0:
        return []
    items = None
    end = text.find("]", start)
    while end != -1:
        try:
            items = json.loads(text[start:end + 1])
            break
        except Exception:
            end = text.find("]", end + 1)
    if not isinstance(items, list):
        # 容错：去掉 DeepSeek 联网回复里混入的脚注标记（-1- / -23- 等）再试
        seg = text[start:]
        seg = re.sub(r"-(\d{1,3})-", "", seg)
        seg = re.sub(r"\s+", " ", seg)
        end = seg.find("]")
        if end < 0:
            return []
        for _bound in range(end, len(seg)):
            try:
                items = json.loads(seg[: _bound + 1])
                if isinstance(items, list):
                    break
            except Exception:
                continue
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        term = (it.get("id") or it.get("term") or "").strip()
        meaning = (it.get("meaning") or it.get("desc") or "").strip()
        if not term or not meaning:
            continue
        if term.isdigit():
            continue
        if len(term) < 2:
            continue
        if term in ("词", "梗", "id", "无", "None"):
            continue
        out.append({"id": term, "meaning": meaning,
                    "reply": (it.get("reply") or "").strip()})
    return out


def _to_items(game, parsed):
    return [
        {"id": it["id"], "meaning": it["meaning"], "reply": it.get("reply", ""),
         "source": game, "topic": game}
        for it in parsed
    ]


def enrich(game: str, batch: int = 5, existing_terms=None, template="") -> list:
    """用 DeepSeek 网页版联网查游戏「热且出圈」的梗，返回 [{id, meaning, source, topic}]。失败抛异常。"""
    from playwright.sync_api import sync_playwright

    browsers = detect_browsers()
    pick = _pick_browser(browsers)

    with sync_playwright() as p:
        if pick["mode"] == "cdp":
            port = pick["port"]
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            try:
                pg = _get_deepseek_page(browser)
                text = _ask(pg, game, batch, existing_terms or [], template)
            finally:
                try:
                    pg.close()
                except Exception:
                    pass
        else:
            # 环境自备：自带 chromium 新实例，用独立 profile 保留登录态
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=os.path.join(_profile_dir(), "chromium"),
                headless=False,
                args=["--no-first-run"],
            )
            try:
                pg = ctx.new_page()
                pg.goto(SITE, timeout=60000)
                time.sleep(5)
                text = _ask(pg, game, batch, existing_terms or [], template)
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass

    return _to_items(game, _parse(text))


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="明日方舟")
    ap.add_argument("--batch", type=int, default=5)
    ap.add_argument("--status", action="store_true", help="只输出环境状态")
    args = ap.parse_args()
    if args.status:
        print(json.dumps(status(), ensure_ascii=False, indent=1))
        return
    try:
        items = enrich(args.game, args.batch)
        print(json.dumps(items, ensure_ascii=False, indent=1))
    except WebMemeFinderError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
