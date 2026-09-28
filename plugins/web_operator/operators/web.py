# -*- coding: utf-8 -*-
"""
Web Operator v2 - 网页操作调度器
================================
主路径：Playwright + Chromium/Edge + CDP Accessibility Tree。

架构（v2 破坏性重写）：
  - 感知：snapshot.SnapshotEngine 用 CDP AX 树产出带 ref 的结构化快照（穿透 shadow DOM + iframe）
  - 动作：action_engine.ActionEngine 按 ref 精确定位，auto-wait + self-heal + 动作缓存 + 视觉兜底
  - 自主：autonomous.run_autonomous 自包含 observe-act-observe 闭环
  - 每个动作成功后自动回传最新快照（observe-act-observe），模型不再盲打

已移除：web.survey 特例、spa_settle_ms 固定 sleep、硬编码 Chrome/116 UA。
"""

import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from .snapshot import SnapshotEngine
from .action_engine import ActionEngine
from .cache import ActionCache

PLUGIN_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
STORAGE_STATE_PATH = PLUGIN_DATA_DIR / "storage_state.json"
ACTION_CACHE_PATH = PLUGIN_DATA_DIR / "action_cache.json"

# task_type -> action_engine 动作名
_ACTION_TASKS = {
    "web.click": "click",
    "web.dblclick": "dblclick",
    "web.fill": "fill",
    "web.select": "select",
    "web.check": "check",
    "web.uncheck": "uncheck",
    "web.press": "press",
    "web.hover": "hover",
    "web.upload": "upload",
    "web.focus": "focus",
    "web.get_attribute": "get_attribute",
}

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
"""


def _safe_json(value):
    try:
        import json as _json
        return _json.loads(_json.dumps(value))
    except Exception:
        return {"_type": type(value).__name__, "_value": str(value)[:2000]}


def _file_to_data_url(path):
    try:
        data = Path(path).read_bytes()
        ext = Path(path).suffix.lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "application/octet-stream")
        b64 = __import__("base64").b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return ""


class WebOperator:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.web_cfg = config.get("web", {}) or {}
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._cdp = None
        self._snap: Optional[SnapshotEngine] = None
        self._actions: Optional[ActionEngine] = None
        self._cache: Optional[ActionCache] = None
        self._dialog_policy = "dismiss"

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------
    def _logs(self, *texts):
        return [{"level": "info", "text": t} for t in texts]

    def _ok(self, result, logs=None, meta=None):
        payload = {"ok": True, "result": result}
        if logs:
            payload["logs"] = logs
        if meta:
            payload["meta"] = meta
        return payload

    def _fail(self, error, logs=None):
        payload = {"ok": False, "error": error}
        if logs:
            payload["logs"] = logs
        return payload

    def _timeout_s(self, params=None):
        params = params or {}
        return int(params.get("timeout") or self.config.get("default_timeout", 30) or 30)

    def _vision_enabled(self):
        va = self.config.get("vision_assist") or {}
        if not va.get("enabled", False):
            return False
        from .vision import _resolve_api_key
        return bool(_resolve_api_key(va))

    # ------------------------------------------------------------------
    # 浏览器生命周期
    # ------------------------------------------------------------------
    def _get_page(self):
        if self._page is not None:
            try:
                self._page.evaluate("1")
                return self._page
            except Exception:
                self._page = None
                self._cdp = None
                self._snap = None
        return None

    def _launch_browser(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("Playwright 未安装：pip install playwright && playwright install chromium")

        headless = bool(self.web_cfg.get("headless", True))
        channel = (self.web_cfg.get("channel") or "").strip()
        pw = sync_playwright().start()
        browser = None
        last_err = None
        candidates = []
        if channel:
            candidates.append({"channel": channel})
        candidates.append({})
        if channel != "msedge":
            candidates.append({"channel": "msedge"})
        for kw in candidates:
            try:
                browser = pw.chromium.launch(headless=headless, **kw)
                break
            except Exception as e:
                last_err = e
        if browser is None:
            pw.stop()
            raise RuntimeError(f"无法启动浏览器（{last_err}）。可配置 web.channel 或用系统 Edge")

        ctx_kwargs = {"viewport": {"width": 1280, "height": 800}}
        # 不再硬编码 UA：仅当配置显式给出才覆盖，否则跟随引擎真实 UA
        ua = (self.web_cfg.get("user_agent") or "").strip()
        if ua:
            ctx_kwargs["user_agent"] = ua
        # 登录态持久化：存在 storage_state 则恢复
        try:
            if self.web_cfg.get("storage_state", True) and STORAGE_STATE_PATH.exists():
                ctx_kwargs["storage_state"] = str(STORAGE_STATE_PATH)
        except Exception:
            pass
        context = browser.new_context(**ctx_kwargs)
        if self.web_cfg.get("stealth", True):
            try:
                context.add_init_script(_STEALTH_JS)
            except Exception:
                pass
        page = context.new_page()
        self._attach_dialog(page)

        self._playwright = pw
        self._browser = browser
        self._context = context
        self._page = page
        self._bind_engines(page)
        return page

    def _bind_engines(self, page):
        """为给定 page 绑定 CDP + 快照 + 动作引擎（tab 切换时重建）。"""
        try:
            self._cdp = self._context.new_cdp_session(page)
        except Exception:
            self._cdp = None
        self._page = page
        self._snap = SnapshotEngine(page, self._cdp, self.config)
        if self._cache is None:
            self._cache = ActionCache(ACTION_CACHE_PATH, enabled=bool(self.web_cfg.get("action_cache", True)))
        self._actions = ActionEngine(
            page, self._snap, self.config,
            screenshot_fn=lambda: self._screenshot(page, full_page=False),
            vision_ground_fn=self._vision_ground,
            cache=self._cache,
        )
        self._attach_dialog(page)

    def _attach_dialog(self, page):
        try:
            def _on_dialog(dialog):
                try:
                    if self._dialog_policy == "accept":
                        dialog.accept()
                    else:
                        dialog.dismiss()
                except Exception:
                    pass
            page.on("dialog", _on_dialog)
        except Exception:
            pass

    def _vision_ground(self, description: str):
        if not self._vision_enabled():
            return None
        shot = self._screenshot(self._page, full_page=False)
        if not shot:
            return None
        from .vision import ground_element
        res = ground_element(shot, description, self.config)
        return res.get("point") if res.get("ok") else None

    def _close_browser(self):
        # 关闭前保存登录态
        try:
            if self.web_cfg.get("storage_state", True) and self._context is not None:
                self._context.storage_state(path=str(STORAGE_STATE_PATH))
        except Exception:
            pass
        for attr in ("_page", "_context", "_browser"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._cdp = None
        self._snap = None
        self._actions = None

    # ------------------------------------------------------------------
    # 截图
    # ------------------------------------------------------------------
    def _screenshot(self, page, full_page: bool = False) -> Optional[str]:
        try:
            path = str(Path(os.environ.get("TEMP", ".")) / f"web_operator_{uuid.uuid4().hex}.png")
            page.screenshot(path=path, full_page=full_page)
            return path
        except Exception:
            return None

    def _should_screenshot(self):
        return bool(self.web_cfg.get("auto_screenshot", True))

    def _ensure_ready(self, logs):
        page = self._get_page()
        if page is None:
            logs.extend(self._logs("正在启动浏览器..."))
            page = self._launch_browser()
            logs.extend(self._logs("浏览器已启动"))
        return page

    # ------------------------------------------------------------------
    # 动作成功后自动回传快照（observe-act-observe 闭环）
    # ------------------------------------------------------------------
    def _with_snapshot(self, result: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params = params or {}
        if params.get("no_snapshot"):
            return result
        if not self.web_cfg.get("snapshot_enabled", True):
            return result
        try:
            snap = self._snap.capture()
            result["snapshot"] = snap[:4000]
        except Exception as e:
            result["snapshot_error"] = str(e)[:120]
        return result

    # ------------------------------------------------------------------
    # 主路由
    # ------------------------------------------------------------------
    def execute(self, task: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        task_type = task.get("type", "")
        params = task.get("params", {}) or {}
        context = context or {}
        logs = []

        # survey 已移除
        if task_type == "web.survey":
            return self._fail("survey 已合并到通用 snapshot+click 路径，请改用 web.autonomous 或 web.snapshot + web.click", logs)

        try:
            if task_type == "web.autonomous":
                page = self._ensure_ready(logs)
                return self._run_autonomous(params, context, logs)

            page = self._ensure_ready(logs)

            if task_type == "web.snapshot":
                return self._do_snapshot(params, logs)
            if task_type == "web.open_url":
                return self._open_url(page, params, logs)
            if task_type == "web.get_page":
                return self._get_page_text(page, params, logs)
            if task_type in _ACTION_TASKS:
                return self._do_action(page, _ACTION_TASKS[task_type], params, logs)
            if task_type == "web.fill_form":
                return self._fill_form(page, params, logs)
            if task_type == "web.extract":
                return self._extract(page, params, logs)
            if task_type == "web.scroll":
                return self._scroll(page, params, logs)
            if task_type == "web.execute_js":
                return self._execute_js(page, params, logs)
            if task_type == "web.press_key":
                return self._press_key(page, params, logs)
            if task_type == "web.wait":
                return self._wait(page, params, logs)
            if task_type == "web.screenshot":
                return self._screenshot_action(page, params, logs)
            if task_type == "web.api_call":
                return self._api_call(params, logs)
            if task_type == "web.tab_list":
                return self._tab_list(params, logs)
            if task_type == "web.tab_switch":
                return self._tab_switch(params, logs)
            if task_type == "web.tab_new":
                return self._tab_new(params, logs)
            if task_type == "web.handle_dialog":
                return self._handle_dialog(params, logs)
            if task_type == "web.download":
                return self._download(page, params, logs)
            if task_type == "web.save_state":
                return self._save_state(params, logs)
            if task_type == "web.load_state":
                return self._load_state(params, logs)

            return self._fail(f"unsupported web task: {task_type}", logs)
        except Exception as e:
            logs.append({"level": "error", "text": f"{type(e).__name__}: {e}"})
            return self._fail(str(e), logs)

    # ------------------------------------------------------------------
    # 具体操作
    # ------------------------------------------------------------------
    def _do_snapshot(self, params, logs):
        snap = self._snap.capture(interactive_only=bool(params.get("interactive_only", True)))
        logs.append({"level": "success", "text": f"快照完成，{len(self._snap.refs)} 个可引用元素"})
        return self._ok({"snapshot": snap, "ref_count": len(self._snap.refs)}, logs)

    def _open_url(self, page, params, logs):
        url = (params.get("url") or "").strip()
        if not url:
            return self._fail("missing url", logs)
        timeout = self._timeout_s(params)
        logs.append({"level": "info", "text": f"打开 {url}"})
        try:
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
        except Exception:
            page.goto(url, timeout=timeout * 1000, wait_until="load")
        # networkidle 替代固定 sleep；失败(长连接/轮询站点)不致命
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout, 12) * 1000)
        except Exception:
            pass
        title = page.title()
        logs.append({"level": "success", "text": f"页面加载完成：{title}"})
        result = {"url": page.url, "title": title}
        if self._should_screenshot():
            shot = self._screenshot(page, full_page=False)
            if shot:
                result["screenshot_path"] = shot
                result["data_url"] = _file_to_data_url(shot)
        result = self._with_snapshot(result, params)
        return self._ok(result, logs, {"web_engine": "playwright"})

    def _get_page_text(self, page, params, logs):
        url = (params.get("url") or "").strip()
        if url:
            page.goto(url, timeout=self._timeout_s(params) * 1000, wait_until="domcontentloaded")
        text = (page.inner_text("body") or "").strip()
        logs.append({"level": "success", "text": f"获取页面文本，长度 {len(text)}"})
        result = {"text": text[:8000], "length": len(text)}
        result = self._with_snapshot(result, params)
        return self._ok(result, logs)

    def _do_action(self, page, action, params, logs):
        ref = (params.get("ref") or "").strip() or None
        selector = (params.get("selector") or "").strip() or None
        if not ref and not selector:
            # text= 兜底（旧用法）
            text = params.get("text")
            if text:
                selector = f"text={text}"
            else:
                return self._fail("missing ref/selector", logs)
        res = self._actions.act(ref, action, params, selector=selector)
        logs.extend(res.logs)
        if not res.ok:
            return self._fail(res.error or f"{action} failed", logs)
        out = res.as_dict()
        out["result"] = self._with_snapshot(out.get("result", {}), params)
        return out

    def _fill_form(self, page, params, logs):
        fields = params.get("fields") or []
        if not fields:
            return self._fail("missing fields", logs)
        filled = []
        failed = []
        for field in fields:
            ref = (field.get("ref") or "").strip() or None
            selector = (field.get("selector") or field.get("name") or field.get("id") or "").strip() or None
            value = field.get("value", "")
            if not ref and not selector:
                continue
            res = self._actions.act(ref, "fill", {**field, "value": value}, selector=selector)
            logs.extend(res.logs)
            if res.ok:
                filled.append({"ref": ref, "selector": selector, "ok": True})
            else:
                failed.append({"ref": ref, "selector": selector, "error": res.error})
        logs.append({"level": "success" if not failed else "warn",
                     "text": f"表单：成功 {len(filled)}，失败 {len(failed)}"})
        result = self._with_snapshot({"filled": filled, "failed": failed}, params)
        return self._ok(result, logs)

    def _extract(self, page, params, logs):
        """结构化提取：给定 ref/selector 限定范围 + fields 映射 {key: selector} 或整段文本。"""
        scope_ref = (params.get("ref") or "").strip()
        scope_sel = (params.get("selector") or "").strip()
        fields = params.get("fields") or {}
        result = {}
        try:
            if scope_ref:
                h = self._snap.resolve_handle(scope_ref)
            elif scope_sel:
                h = page.query_selector(scope_sel)
            else:
                h = None
            base = h or page.query_selector("body")
            result["text"] = ((base.inner_text() if base else "") or "").strip()[:6000]
            field_vals = {}
            if isinstance(fields, dict):
                for key, sel in fields.items():
                    try:
                        el = (h or page).query_selector(sel) if isinstance(sel, str) else None
                        if el is not None:
                            field_vals[key] = (el.inner_text() or el.get_attribute("value") or "").strip()
                    except Exception:
                        pass
                result["fields"] = field_vals
            logs.append({"level": "success", "text": f"提取完成，字段 {len(result.get('fields', {}))}"})
        except Exception as e:
            return self._fail(f"extract failed: {e}", logs)
        if params.get("schema"):
            result["schema_hint"] = params["schema"]
        return self._ok(result, logs)

    def _scroll(self, page, params, logs):
        direction = params.get("direction", "down")
        amount = int(params.get("amount", 400))
        if direction == "up":
            amount = -amount
        elif direction == "top":
            amount = -10 ** 9
        elif direction == "bottom":
            amount = 10 ** 9
        page.evaluate(f"window.scrollBy(0, {amount})")
        try:
            page.wait_for_timeout(250)
        except Exception:
            pass
        logs.append({"level": "success", "text": f"滚动 {direction}"})
        result = self._with_snapshot({"direction": direction, "amount": amount}, params)
        return self._ok(result, logs)

    def _execute_js(self, page, params, logs):
        script = params.get("script")
        if not script:
            return self._fail("missing script", logs)
        result = page.evaluate(script)
        logs.append({"level": "success", "text": "JS 执行完成"})
        return self._ok({"result": _safe_json(result)}, logs)

    def _press_key(self, page, params, logs):
        """页面级按键（无焦点元素），适配键盘驱动的网页（如某些答题页按 1/2/方向键）。"""
        key = (params.get("key") or params.get("value") or "").strip()
        if not key:
            return self._fail("missing key", logs)
        try:
            combo = params.get("interval")
            page.keyboard.press(key)
        except Exception as e:
            return self._fail(f"press_key failed: {e}", logs)
        logs.append({"level": "success", "text": f"已按下页面级按键 {key}"})
        result = self._with_snapshot({"pressed": key}, params)
        return self._ok(result, logs)

    def _wait(self, page, params, logs):
        selector = params.get("selector")
        ref = params.get("ref")
        timeout = self._timeout_s(params) * 1000
        state = params.get("state", "visible")
        if ref:
            loc, _ = self._snap.locate(ref)
            if loc is not None:
                loc.wait_for(state=state, timeout=timeout)
                logs.append({"level": "success", "text": f"等待元素完成：ref={ref}"})
                return self._ok({"ref": ref, "state": state}, logs)
        if selector:
            page.wait_for_selector(selector, state=state, timeout=timeout)
            logs.append({"level": "success", "text": f"等待元素完成：{selector}"})
            return self._ok({"selector": selector, "state": state}, logs)
        if params.get("load_state"):
            page.wait_for_load_state(params["load_state"], timeout=timeout)
            return self._ok({"load_state": params["load_state"]}, logs)
        if params.get("ms"):
            page.wait_for_timeout(int(params["ms"]))
            return self._ok({"waited_ms": params["ms"]}, logs)
        return self._fail("missing ref/selector/load_state/ms", logs)

    def _screenshot_action(self, page, params, logs):
        full_page = bool(params.get("full_page", False))
        path = self._screenshot(page, full_page=full_page)
        if not path:
            return self._fail("screenshot failed", logs)
        result = {"path": path, "data_url": _file_to_data_url(path)}
        if params.get("vision") and self._vision_enabled():
            from .vision import analyze_image
            prompt = params.get("prompt") or "请描述这张网页截图的当前状态，并建议下一个操作。"
            vr = analyze_image(path, prompt, self.config)
            if vr.get("ok"):
                result["vision_summary"] = vr["result"]["summary"]
                logs.append({"level": "success", "text": f"[vision] {vr['result'].get('vision_model')} 分析完成"})
            else:
                logs.append({"level": "warn", "text": f"[vision] {vr.get('error')}"})
        logs.append({"level": "success", "text": f"截图已保存：{path}"})
        return self._ok(result, logs)

    def _api_call(self, params, logs):
        import requests as req
        url = params.get("url")
        method = (params.get("method") or "GET").upper()
        if not url:
            return self._fail("missing url", logs)
        body = params.get("body")
        headers = params.get("headers") or {}
        timeout = self._timeout_s(params)
        kwargs = {"headers": headers, "timeout": timeout}
        if method != "GET" and body is not None:
            kwargs["json"] = body
        r = req.request(method, url, **kwargs)
        logs.append({"level": "success", "text": f"HTTP {method} {url} -> {r.status_code}"})
        try:
            data = r.json()
        except Exception:
            data = r.text
        return self._ok({"status_code": r.status_code, "body": data}, logs)

    # ------------------------------------------------------------------
    # 标签页 / dialog / 下载 / 登录态
    # ------------------------------------------------------------------
    def _tab_list(self, params, logs):
        pages = list(self._context.pages) if self._context else []
        tabs = [{"index": i, "url": p.url, "title": (p.title() or "")} for i, p in enumerate(pages)]
        active = -1
        for i, p in enumerate(pages):
            if p is self._page:
                active = i
                break
        logs.append({"level": "success", "text": f"共 {len(tabs)} 个标签页"})
        return self._ok({"tabs": tabs, "active": active}, logs)

    def _tab_switch(self, params, logs):
        idx = params.get("index")
        pages = list(self._context.pages) if self._context else []
        if idx is None or not (0 <= int(idx) < len(pages)):
            return self._fail(f"tab index 越界：{idx}（共 {len(pages)}）", logs)
        target = pages[int(idx)]
        try:
            target.bring_to_front()
        except Exception:
            pass
        self._bind_engines(target)
        logs.append({"level": "success", "text": f"已切换到标签页 {idx}：{target.url}"})
        result = self._with_snapshot({"index": int(idx), "url": target.url, "title": target.title()}, params)
        return self._ok(result, logs)

    def _tab_new(self, params, logs):
        url = (params.get("url") or "").strip()
        page = self._context.new_page()
        self._bind_engines(page)
        if url:
            timeout = self._timeout_s(params) * 1000
            page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=min(self._timeout_s(params), 12) * 1000)
            except Exception:
                pass
        logs.append({"level": "success", "text": f"新建标签页：{page.url}"})
        result = self._with_snapshot({"url": page.url, "title": page.title()}, params)
        return self._ok(result, logs)

    def _handle_dialog(self, params, logs):
        policy = (params.get("policy") or "dismiss").lower()
        if policy not in ("accept", "dismiss"):
            return self._fail("policy 需为 accept / dismiss", logs)
        self._dialog_policy = policy
        logs.append({"level": "success", "text": f"弹窗处理策略：{policy}"})
        return self._ok({"dialog_policy": policy}, logs)

    def _download(self, page, params, logs):
        ref = (params.get("ref") or "").strip()
        selector = (params.get("selector") or "").strip()
        save_path = params.get("save_path") or str(Path(os.environ.get("TEMP", ".")) / f"web_operator_dl_{uuid.uuid4().hex}.bin")
        timeout = self._timeout_s(params) * 1000
        try:
            with page.expect_download(timeout=timeout) as dl_info:
                if ref:
                    self._actions.act(ref, "click", params)
                elif selector:
                    page.click(selector, timeout=timeout)
                else:
                    return self._fail("download 需要 ref 或 selector 触发", logs)
            download = dl_info.value
            download.save_as(save_path)
            logs.append({"level": "success", "text": f"已下载：{download.suggested_filename} -> {save_path}"})
            return self._ok({"save_path": save_path, "filename": download.suggested_filename}, logs)
        except Exception as e:
            return self._fail(f"download failed: {e}", logs)

    def _save_state(self, params, logs):
        try:
            path = params.get("path") or str(STORAGE_STATE_PATH)
            self._context.storage_state(path=path)
            logs.append({"level": "success", "text": f"登录态已保存：{path}"})
            return self._ok({"path": path}, logs)
        except Exception as e:
            return self._fail(f"save_state failed: {e}", logs)

    def _load_state(self, params, logs):
        """重载登录态：关闭当前 context 并以 storage_state 重新打开。"""
        try:
            path = params.get("path") or str(STORAGE_STATE_PATH)
            if not Path(path).exists():
                return self._fail(f"无已保存登录态：{path}", logs)
            url = self._page.url if self._page else "about:blank"
            self._close_browser()
            page = self._launch_browser()
            page.goto(url, timeout=self._timeout_s(params) * 1000, wait_until="domcontentloaded")
            logs.append({"level": "success", "text": "已以保存的登录态重载浏览器"})
            result = self._with_snapshot({"url": page.url, "title": page.title()}, params)
            return self._ok(result, logs)
        except Exception as e:
            return self._fail(f"load_state failed: {e}", logs)

    # ------------------------------------------------------------------
    # 自主 Agent
    # ------------------------------------------------------------------
    def _run_autonomous(self, params, context, logs):
        goal = (params.get("goal") or params.get("url_goal") or "").strip()
        url = (params.get("url") or "").strip()
        if url:
            self._page.goto(url, timeout=self._timeout_s(params) * 1000, wait_until="domcontentloaded")
            try:
                self._page.wait_for_load_state("networkidle", timeout=min(self._timeout_s(params), 12) * 1000)
            except Exception:
                pass
        if not goal:
            return self._fail("autonomous 需要 goal", logs)
        chat_fn = context.get("chat")
        if chat_fn is None:
            return self._fail("autonomous 需要宿主注入 chat 回调（bot_core 未提供 context['chat']）", logs)
        from .autonomous import run_autonomous
        auto_cfg = self.config.get("autonomous") or {}
        max_steps = int(params.get("max_steps") or auto_cfg.get("max_steps", 25) or 25)
        return run_autonomous(
            page=self._page, snap=self._snap, actions=self._actions,
            goal=goal, max_steps=max_steps, chat_fn=chat_fn, config=self.config, logs=logs,
        )
