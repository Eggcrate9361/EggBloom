# -*- coding: utf-8 -*-
"""
Web Operator - 网页操作器
主路径：Playwright + Chromium
截图：每次操作前后自动截图，作为视觉辅助基础设施
拟人化：随机延迟 / 抖动滚动 / 防连点，降低被站点防机器人判定的概率
"""

import os
import random
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


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
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(ext, "application/octet-stream")
        b64 = __import__("base64").b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return ""


class WebOperator:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.web_cfg = config.get("web", {})
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def _logs(self, *texts):
        return [{"level": "info", "text": t} for t in texts]

    def _ok(self, result: Dict[str, Any], logs=None, meta=None):
        payload = {"ok": True, "result": result}
        if logs:
            payload["logs"] = logs
        if meta:
            payload["meta"] = meta
        return payload

    def _fail(self, error: str, logs=None):
        payload = {"ok": False, "error": error}
        if logs:
            payload["logs"] = logs
        return payload

    def _ensure_playwright(self):
        try:
            from playwright.sync_api import sync_playwright
            return sync_playwright
        except ImportError:
            return None

    def _get_page(self):
        if self._page is not None:
            try:
                self._page.evaluate("1")
                return self._page
            except Exception:
                self._page = None
        return None

    def _launch_browser(self):
        sync_playwright_cls = self._ensure_playwright()
        if sync_playwright_cls is None:
            raise RuntimeError(
                "Playwright 未安装，请运行: pip install playwright && playwright install chromium"
            )

        headless = bool(self.web_cfg.get("headless", True))
        channel = (self.web_cfg.get("channel") or "").strip()
        pw = sync_playwright_cls().start()
        browser = None
        last_err = None
        candidates = []
        if channel:
            candidates.append({"channel": channel})
        candidates.append({})  # 默认 chromium
        if channel != "msedge":
            candidates.append({"channel": "msedge"})  # 兜底系统 Edge
        for kw in candidates:
            try:
                browser = pw.chromium.launch(headless=headless, **kw)
                break
            except Exception as e:
                last_err = e
        if browser is None:
            pw.stop()
            raise RuntimeError(f"无法启动浏览器（{last_err}）。可配置 web.channel 或用系统 Edge")
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        self._playwright = pw
        self._browser = browser
        self._context = context
        self._page = page
        return page

    def _close_browser(self):
        try:
            if self._page:
                self._page.close()
        except Exception:
            pass
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
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

    def _screenshot(self, page, full_page: bool = False) -> Optional[str]:
        try:
            path = str(Path(os.environ.get("TEMP", ".")) / f"web_operator_{uuid.uuid4().hex}.png")
            page.screenshot(path=path, full_page=full_page)
            return path
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 拟人化（humanize）
    # ------------------------------------------------------------------
    def _human_cfg(self, params):
        """合并 配置级 humanize + 本次 params 级覆盖"""
        cfg = self.web_cfg.get("humanize") or {}
        override = params.get("humanize")
        out = dict(cfg)
        if isinstance(override, dict):
            out.update(override)
        return out

    def _human_pause(self, params=None, rng=None):
        """随机等待 delay_min~delay_max ms（默认 800~1800ms）"""
        cfg = self._human_cfg(params or {})
        if not cfg.get("enabled", True):
            return
        lo = int(cfg.get("delay_min_ms", 800) or 800)
        hi = int(cfg.get("delay_max_ms", 1800) or 1800)
        rng = rng or random
        time.sleep(max(0, rng.uniform(lo, hi)) / 1000.0)

    def _human_jitter(self, page, params=None, rng=None):
        """小范围滚动抖动 / 鼠标移动，模拟真人浏览"""
        cfg = self._human_cfg(params or {})
        if not cfg.get("enabled", True):
            return
        if not cfg.get("jitter_scroll", True):
            return
        rng = rng or random
        try:
            if rng.random() < 0.35:
                dy = rng.randint(20, 120) * (1 if rng.random() < 0.5 else -1)
                page.evaluate(f"window.scrollBy(0, {dy})")
            page.mouse.move(rng.randint(200, 1000), rng.randint(150, 650))
        except Exception:
            pass

    def _human_pick(self, question_text: str, rng=None) -> int:
        """
        拟人化答案选择：避免每次都纯随机，模拟"倾向于某档、偶尔极端"的作答波形。
        返回选项下标（0=完全符合 ... 4=完全不符合）。
        """
        rng = rng or random
        # 以"比较符合/中立"为中位，低概率飘向两端，避免单调重复
        weights = [0.16, 0.30, 0.28, 0.18, 0.08]
        total = sum(weights)
        r = rng.random() * total
        acc = 0.0
        for i, w in enumerate(weights):
            acc += w
            if r <= acc:
                return i
        return 2

    def _human_opt_out(self, params=None):
        cfg = self._human_cfg(params or {})
        return not cfg.get("enabled", True)

    # ------------------------------------------------------------------
    # 视觉辅助（vision_assist）
    # ------------------------------------------------------------------
    def _vision_enabled(self):
        va = self.config.get("vision_assist") or {}
        if not va.get("enabled", False):
            return False
        from .vision import _resolve_api_key
        return bool(_resolve_api_key(va))

    def _analyze_screenshot(self, page, prompt="请描述这张网页截图的当前状态，并建议下一个操作。", params=None):
        """截图 -> 调 VLM -> 返回分析结果 dict（失败则 ok=False）"""
        from .vision import analyze_image
        shot = self._screenshot(page, full_page=False)
        if not shot:
            return {"ok": False, "error": "screenshot failed"}
        va_cfg = {"vision_assist": self.config.get("vision_assist") or {}}
        return analyze_image(shot, prompt, va_cfg)

    def _screenshot_action(self, page, params, logs, vision=False):
        full_page = bool(params.get("full_page", False))
        path = self._screenshot(page, full_page=full_page)
        if not path:
            return self._fail("screenshot failed", logs)
        logs.append({"level": "success", "text": f"截图已保存：{path}"})
        result = {"path": path, "data_url": _file_to_data_url(path)}
        if vision and self._vision_enabled():
            prompt = params.get("prompt") or "请描述这张网页截图的当前状态，并建议下一个操作。"
            vr = self._analyze_screenshot(page, prompt=prompt, params=params)
            if vr.get("ok"):
                result["vision_summary"] = vr["result"]["summary"]
                result["vision_model"] = vr["result"].get("vision_model")
                logs.append({"level": "success", "text": f"[vision] {vr['result'].get('vision_model')} 分析完成"})
            else:
                logs.append({"level": "warn", "text": f"[vision] {vr.get('error')}"})
        return self._ok(result, logs)

    def execute(self, task: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        task_type = task.get("type", "")
        params = task.get("params", {}) or {}
        logs = []

        try:
            page = self._get_page()
            if page is None:
                logs.extend(self._logs("正在启动 Chromium..."))
                page = self._launch_browser()
                logs.extend(self._logs("Chromium 已启动"))

            if task_type == "web.open_url":
                return self._open_url(page, params, logs)
            if task_type == "web.get_page":
                return self._get_page_text(page, params, logs)
            if task_type == "web.fill_form":
                return self._fill_form(page, params, logs)
            if task_type == "web.click":
                return self._click(page, params, logs)
            if task_type == "web.select":
                return self._select(page, params, logs)
            if task_type == "web.upload":
                return self._upload(page, params, logs)
            if task_type == "web.wait":
                return self._wait(page, params, logs)
            if task_type == "web.execute_js":
                return self._execute_js(page, params, logs)
            if task_type == "web.screenshot":
                return self._screenshot_action(page, params, logs, vision=params.get("vision", False))
            if task_type == "web.vision":
                return self._screenshot_action(page, params, logs, vision=True)
            if task_type == "web.get_attribute":
                return self._get_attribute(page, params, logs)
            if task_type == "web.scroll":
                return self._scroll(page, params, logs)
            if task_type == "web.api_call":
                return self._api_call(params, logs)
            if task_type == "web.survey":
                return self._survey(page, params, logs)

            return self._fail(f"unsupported web task: {task_type}", logs)
        except Exception as e:
            logs.append({"level": "error", "text": str(e)})
            return self._fail(str(e), logs)
        finally:
            # Keep browser warm for next task; caller may close explicitly if needed.
            pass

    def _open_url(self, page, params, logs):
        url = (params.get("url") or "").strip()
        if not url:
            return self._fail("missing url", logs)
        timeout = self._timeout(params)
        logs.append({"level": "info", "text": f"打开 {url}"})
        try:
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
        except Exception:
            # last-chance fallback
            page.goto(url, timeout=timeout * 1000, wait_until="load")
        # SPA 需要一点时间挂载事件，避免后续点击落空
        settle_ms = int(params.get("spa_settle_ms") or self.web_cfg.get("spa_settle_ms", 2200) or 2200)
        if settle_ms > 0:
            page.wait_for_timeout(settle_ms)
        title = page.title()
        logs.append({"level": "success", "text": f"页面加载完成，标题：{title}"})
        screenshot_path = None
        if self._should_screenshot():
            screenshot_path = self._screenshot(page, full_page=False)
        result = {
            "url": page.url,
            "title": title,
            "screenshot_path": screenshot_path,
            "data_url": _file_to_data_url(screenshot_path) if screenshot_path else None,
        }
        meta = {"web_engine": "playwright", "screenshot": bool(screenshot_path)}
        return self._ok(result, logs, meta)

    def _get_page_text(self, page, params, logs):
        url = (params.get("url") or "").strip()
        if url:
            timeout = self._timeout(params)
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
        text = page.inner_text("body")
        text = (text or "").strip()
        logs.append({"level": "success", "text": f"获取页面文本，长度 {len(text)}"})
        return self._ok({"text": text[:8000], "length": len(text)}, logs)

    def _fill_form(self, page, params, logs):
        fields = params.get("fields") or []
        if not fields:
            return self._fail("missing fields", logs)
        filled = []
        for field in fields:
            selector = field.get("selector") or field.get("name") or field.get("id")
            value = field.get("value", "")
            if not selector:
                continue
            try:
                page.fill(selector, str(value))
                filled.append({"selector": selector, "value": value})
            except Exception as e:
                logs.append({"level": "warn", "text": f"填写失败 {selector}: {e}"})
        logs.append({"level": "success", "text": f"已填写 {len(filled)} 个字段"})
        screenshot_path = None
        if self._should_screenshot():
            screenshot_path = self._screenshot(page, full_page=False)
        return self._ok({
            "filled": filled,
            "screenshot_path": screenshot_path,
            "data_url": _file_to_data_url(screenshot_path) if screenshot_path else None,
        }, logs)

    def _click(self, page, params, logs):
        selector = params.get("selector")
        text = params.get("text")
        if not selector and not text:
            return self._fail("missing selector/text", logs)
        if text:
            selector = f"text={text}"
        retries = int(params.get("retries", 1) or 1)
        logs.append({"level": "info", "text": f"点击 {selector}"})
        last_err = None
        for attempt in range(retries + 1):
            try:
                page.click(selector, timeout=10000)
                last_err = None
                break
            except Exception as e:
                last_err = e
                # 可能是 SPA 仍在挂载/动画中，等一下再点
                page.wait_for_timeout(800)
        if last_err is not None:
            logs.append({"level": "warn", "text": f"点击 {selector} 失败（重试后）：{last_err}"})
            return self._fail(f"click failed: {last_err}", logs)
        logs.append({"level": "success", "text": "点击完成"})
        screenshot_path = None
        if self._should_screenshot():
            screenshot_path = self._screenshot(page, full_page=False)
        return self._ok({"selector": selector, "screenshot_path": screenshot_path}, logs)

    def _select(self, page, params, logs):
        selector = params.get("selector")
        value = params.get("value")
        if not selector or value is None:
            return self._fail("missing selector/value", logs)
        page.select_option(selector, str(value))
        logs.append({"level": "success", "text": f"已选择 {selector} = {value}"})
        screenshot_path = None
        if self._should_screenshot():
            screenshot_path = self._screenshot(page, full_page=False)
        return self._ok({"selector": selector, "value": value, "screenshot_path": screenshot_path}, logs)

    def _upload(self, page, params, logs):
        selector = params.get("selector")
        file_path = params.get("file_path")
        if not selector or not file_path:
            return self._fail("missing selector/file_path", logs)
        page.set_input_files(selector, file_path)
        logs.append({"level": "success", "text": f"已上传文件到 {selector}"})
        return self._ok({"selector": selector, "file_path": file_path}, logs)

    def _wait(self, page, params, logs):
        selector = params.get("selector")
        state = params.get("state", "visible")
        timeout = self._timeout(params)
        if selector:
            if state == "hidden":
                page.wait_for_selector(selector, state="hidden", timeout=timeout * 1000)
            else:
                page.wait_for_selector(selector, state="visible", timeout=timeout * 1000)
            logs.append({"level": "success", "text": f"等待元素完成：{selector}"})
            return self._ok({"selector": selector, "state": state}, logs)
        if params.get("load_state"):
            page.wait_for_load_state(params["load_state"], timeout=timeout * 1000)
            logs.append({"level": "success", "text": f"等待页面状态：{params['load_state']}"})
            return self._ok({"load_state": params["load_state"]}, logs)
        return self._fail("missing selector/load_state", logs)

    def _execute_js(self, page, params, logs):
        script = params.get("script")
        if not script:
            return self._fail("missing script", logs)
        result = page.evaluate(script)
        logs.append({"level": "success", "text": "JS 执行完成"})
        return self._ok({"result": _safe_json(result)}, logs)

    def _get_attribute(self, page, params, logs):
        selector = params.get("selector")
        name = params.get("name")
        if not selector or not name:
            return self._fail("missing selector/name", logs)
        value = page.get_attribute(selector, name)
        logs.append({"level": "success", "text": f"获取属性 {selector}@{name}"})
        return self._ok({"selector": selector, "name": name, "value": value}, logs)

    def _scroll(self, page, params, logs):
        direction = params.get("direction", "down")
        amount = int(params.get("amount", 300))
        if direction == "up":
            amount = -amount
        page.evaluate(f"window.scrollBy(0, {amount})")
        logs.append({"level": "success", "text": f"滚动 {direction} {abs(amount)}"})
        return self._ok({"direction": direction, "amount": amount}, logs)

    def _api_call(self, params, logs):
        import requests as req
        url = params.get("url")
        method = (params.get("method") or "GET").upper()
        if not url:
            return self._fail("missing url", logs)
        body = params.get("body")
        headers = params.get("headers") or {}
        timeout = self._timeout(params)
        try:
            if method == "GET":
                r = req.get(url, headers=headers, timeout=timeout)
            elif method == "POST":
                r = req.post(url, headers=headers, json=body, timeout=timeout)
            elif method == "PUT":
                r = req.put(url, headers=headers, json=body, timeout=timeout)
            elif method == "DELETE":
                r = req.delete(url, headers=headers, timeout=timeout)
            else:
                return self._fail(f"unsupported method: {method}", logs)
            logs.append({"level": "success", "text": f"HTTP {method} {url} -> {r.status_code}"})
            try:
                data = r.json()
            except Exception:
                data = r.text
            return self._ok({"status_code": r.status_code, "body": data}, logs)
        except Exception as e:
            logs.append({"level": "error", "text": str(e)})
            return self._fail(str(e), logs)

    def _timeout(self, params):
        return int(params.get("timeout") or self.web_cfg.get("default_timeout", 30) or 30)

    def _should_screenshot(self):
        mode = self.web_cfg.get("screenshot_mode", "on_demand")
        if mode == "off":
            return False
        if mode == "on_error":
            return False
        return bool(self.web_cfg.get("auto_screenshot", True))

    # ------------------------------------------------------------------
    # web.survey - 问卷自动填写（单会话 + 拟人化）
    # ------------------------------------------------------------------
    def _survey(self, page, params, logs):
        """
        自动填写问卷/答题页面：重复点击选项直到完成。
          params:
            option_selector: 选项按钮选择器，默认 ".option-btn"
            question_selector: 题目文本选择器，默认 ".question-text"（可选）
            total: 总题数（没有进度文本时的兜底，默认 50）
            pick_mode: "human"（拟人加权,默认）/ "random" / "first" / "middle"
            check_text: 页面出现该文本就视为结束（可选）
            max_polls: 单题最长的等待切换轮询，默认 50（单位 120ms）
            humanize: {enabled, delay_min_ms, delay_max_ms, jitter_scroll}
        """
        rng = random.Random(int(id(params)) % (2 ** 32)) if params else random
        opt_sel = params.get("option_selector") or ".option-btn"
        q_sel = params.get("question_selector") or ".question-text"
        total = int(params.get("total", 50) or 50)
        pick_mode = params.get("pick_mode", "human")
        check_text = params.get("check_text")
        logs.append({"level": "info", "text": f"[survey] 开始问卷，预计 {total} 题，选项 {opt_sel}"})

        try:
            page.wait_for_selector(opt_sel, timeout=30000)
        except Exception:
            return self._fail("问卷选项未出现（等待超时）", logs)

        answered = 0
        no_progress = 0
        last_q = ""
        start = time.time()
        while answered < total:
            # 结果页判定：选项消失
            if page.query_selector(opt_sel) is None:
                logs.append({"level": "success", "text": f"[survey] 选项消失，判定进入结果页（{answered} 题）"})
                break

            # 当前题目文本（用于拟人 pick 与切换检测）
            try:
                cur_q = (page.evaluate("(s)=>((document.querySelector(s)||{}).innerText||'')", q_sel) or "").strip()
            except Exception:
                cur_q = ""

            # 拟人化节奏
            self._human_pause(params, rng)
            self._human_jitter(page, params, rng)

            # 选答案
            if pick_mode in ("human", "auto"):
                idx = self._human_pick(cur_q, rng)
            elif pick_mode == "random":
                idx = rng.randrange(5)
            elif pick_mode == "middle":
                idx = 2
            else:
                idx = 0

            # 点选（配重试，兼容转场吞点击）
            clicked = False
            for attempt in range(4):
                try:
                    page.click(f"{opt_sel} >> nth={idx}", timeout=6000)
                    clicked = True
                    break
                except Exception:
                    page.wait_for_timeout(300)
            if not clicked:
                try:
                    done = page.evaluate(
                        "(i) => { const b = document.querySelectorAll('.option-btn'); if(!b[i]) return false; b[i].click(); return true; }",
                        idx,
                    )
                    clicked = bool(done)
                except Exception:
                    clicked = False
            if not clicked:
                logs.append({"level": "warn", "text": f"[survey] 第 {answered + 1} 题点选失败，跳过"})
                page.wait_for_timeout(500)
                continue

            answered += 1
            if answered % 5 == 0:
                logs.append({"level": "info", "text": f"[survey] 已完成 {answered}/{total}"})

            # 等题目切换（轮询，最多 max_polls * 120ms），期间点空了也不算完成
            npoll = int(params.get("max_polls", 40) or 40)
            switched = False
            for _ in range(npoll):
                try:
                    now = (page.evaluate("(q) => ((document.querySelector(q)||{}).innerText||'')", q_sel) or "").strip()
                except Exception:
                    now = ""
                if (now and now != cur_q) or page.query_selector(opt_sel) is None:
                    switched = True
                    break
                page.wait_for_timeout(120)
            if not switched:
                no_progress += 1
                if no_progress >= 3:
                    logs.append({"level": "warn", "text": "[survey] 连续多次未切换，可能已到结果页，结束"})
                    break
            else:
                no_progress = 0

            # 显式完成文本
            if check_text:
                try:
                    body = page.evaluate("() => document.body.innerText") or ""
                except Exception:
                    body = ""
                if check_text in body:
                    logs.append({"level": "success", "text": f"[survey] 检测到结果文本，结束（{answered} 题）"})
                    break

            last_q = cur_q

        end_wait = time.time() - start
        logs.append({"level": "success", "text": f"[survey] 完成 {answered} 题，耗时 {end_wait:.1f}s"})

        shot = None
        if params.get("screenshot", True):
            try:
                shot = self._screenshot(page, full_page=True)
            except Exception:
                pass
        return self._ok({
            "answered": answered,
            "total": total,
            "elapsed_ms": int(end_wait * 1000),
            "screenshot_path": shot,
            "done": answered >= total,
        }, logs)
