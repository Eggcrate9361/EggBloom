# -*- coding: utf-8 -*-
"""
Action Engine - ref 精确动作引擎
================================
把"对 ref=eN 做某动作"翻译成 Playwright Locator 操作，自带：
  1. auto-wait（Playwright Locator 内建可操作性等待，替代固定 sleep）
  2. self-heal：解析/操作失败 -> 重新快照 -> 按语义(role+name)重找 ref -> 重试
  3. action cache：记住 (domain, role, name) -> 稳定 selector，ref 失效时先查缓存
  4. vision grounding 兜底：仍失败且开启时，截图给 VLM 返回坐标 -> page.mouse 点击

依赖注入（避免和 web.py 循环 import）：
  - snapshot_engine: SnapshotEngine 实例
  - page:            Playwright Page
  - config:          插件完整配置
  - screenshot_fn(): 返回截图路径
  - vision_ground_fn(desc) -> {"x":int,"y":int} | None
  - cache:           ActionCache 实例（可为 None）
"""

import time
from typing import Any, Callable, Dict, Optional


class ActionResult:
    def __init__(self, ok, result=None, error=None, logs=None, healed=False, vision=False, cached=False):
        self.ok = ok
        self.result = result or {}
        self.error = error
        self.logs = logs or []
        self.healed = healed
        self.vision = vision
        self.cached = cached

    def as_dict(self):
        d = {"ok": self.ok, "result": self.result, "logs": self.logs}
        if self.error:
            d["error"] = self.error
        if self.healed:
            d["healed"] = True
        if self.vision:
            d["vision_fallback"] = True
        if self.cached:
            d["from_cache"] = True
        return d


# 支持的动作 -> (需要 value, 需要 file)
_ACTION_SPEC = {
    "click": (False, False),
    "dblclick": (False, False),
    "fill": (True, False),
    "select": (True, False),
    "check": (False, False),
    "uncheck": (False, False),
    "press": (True, False),
    "hover": (False, False),
    "upload": (False, True),
    "focus": (False, False),
    "get_attribute": (False, False),
}


class ActionEngine:
    def __init__(self, page, snapshot_engine, config, *,
                 screenshot_fn: Optional[Callable[[], Optional[str]]] = None,
                 vision_ground_fn: Optional[Callable[[str], Optional[Dict[str, int]]]] = None,
                 cache=None):
        self.page = page
        self.snap = snapshot_engine
        self.config = config or {}
        self.web_cfg = self.config.get("web", {})
        self.screenshot_fn = screenshot_fn
        self.vision_ground_fn = vision_ground_fn
        self.cache = cache

    def _timeout_ms(self, params=None):
        params = params or {}
        t = params.get("timeout") or self.web_cfg.get("default_timeout") or self.config.get("default_timeout") or 30
        try:
            return int(float(t) * 1000)
        except Exception:
            return 30000

    # ------------------------------------------------------------------
    def act(self, ref: str, action: str, params: Optional[Dict[str, Any]] = None,
            selector: Optional[str] = None) -> ActionResult:
        params = params or {}
        logs = []
        heal = bool(self.web_cfg.get("self_heal", True))
        retries = int(self.web_cfg.get("self_heal_retries", 1) or 0) if heal else 0

        # 1) 直接按 ref 执行
        res = self._try_ref(ref, action, params, logs)
        if res and res.ok:
            self._cache_learn(ref, action, params)
            return res

        # 2) 显式 selector 兜底（模型仍可直接给 selector）
        if selector:
            sres = self._try_selector(selector, action, params, logs)
            if sres.ok:
                return sres

        # 3) 动作缓存命中
        cached = self._cache_lookup(ref, action, params)
        if cached:
            cres = self._try_selector(cached, action, params, logs)
            if cres.ok:
                cres.cached = True
                cres.logs.append({"level": "info", "text": f"[act] 命中动作缓存 {cached}"})
                return cres

        if not heal:
            return res or ActionResult(False, error=f"action failed: {action} ref={ref}", logs=logs)

        # 4) self-heal：重快照 -> 按语义重找 ref -> 重试
        entry = self.snap.snapshot_for_semantics(ref) if ref else None
        for attempt in range(retries):
            try:
                time.sleep(0.4)
                self.snap.capture()
            except Exception:
                break
            new_ref = self._refine_by_semantics(entry, params)
            if not new_ref:
                break
            hres = self._try_ref(new_ref, action, params, logs)
            if hres and hres.ok:
                hres.healed = True
                hres.logs.append({"level": "info", "text": f"[act] 自愈成功：{ref} -> {new_ref}"})
                self._cache_learn(new_ref, action, params)
                return hres

        # 5) 视觉坐标兜底（仅点击类）
        vres = self._vision_fallback(action, params, logs)
        if vres is not None:
            return vres

        return res or ActionResult(False, error=f"action failed: {action} ref={ref}", logs=logs)

    # ------------------------------------------------------------------
    def _try_ref(self, ref, action, params, logs) -> Optional[ActionResult]:
        if not ref:
            return None
        locator, kind = self.snap.locate(ref)
        if locator is None:
            logs.append({"level": "warn", "text": f"[act] ref {ref} 无法解析"})
            return None
        return self._perform(locator, action, params, logs)

    def _try_selector(self, selector, action, params, logs) -> ActionResult:
        try:
            locator = self.page.locator(selector).first
        except Exception as e:
            return ActionResult(False, error=f"bad selector {selector}: {e}", logs=logs)
        r = self._perform(locator, action, params, logs)
        if r.ok:
            r.logs.append({"level": "info", "text": f"[act] 通过 selector 完成 {action}"})
        return r

    # ------------------------------------------------------------------
    def _perform(self, locator, action, params, logs) -> ActionResult:
        spec = _ACTION_SPEC.get(action)
        if spec is None:
            return ActionResult(False, error=f"unsupported action: {action}", logs=logs)
        tmo = self._timeout_ms(params)
        try:
            if action == "click":
                locator.click(timeout=tmo)
            elif action == "dblclick":
                locator.dblclick(timeout=tmo)
            elif action == "fill":
                val = str(params.get("value", ""))
                # 敏感字段脱敏日志：密码类只记 ***
                shown = "***" if _looks_secret(locator, params) else val[:60]
                locator.fill(val, timeout=tmo)
                logs.append({"level": "info", "text": f"[act] 填入 {shown}"})
            elif action == "select":
                val = params.get("value", "")
                locator.select_option(label=params.get("label") or None, value=str(val) if not params.get("label") else None, timeout=tmo)
            elif action == "check":
                locator.check(timeout=tmo)
            elif action == "uncheck":
                locator.uncheck(timeout=tmo)
            elif action == "press":
                key = str(params.get("value") or params.get("key") or "Enter")
                locator.press(key, timeout=tmo)
            elif action == "hover":
                locator.hover(timeout=tmo)
            elif action == "focus":
                locator.focus(timeout=tmo)
            elif action == "upload":
                path = params.get("file_path") or params.get("path")
                if not path:
                    return ActionResult(False, error="upload 缺少 file_path", logs=logs)
                locator.set_input_files(path, timeout=tmo)
            elif action == "get_attribute":
                name = params.get("name") or "value"
                val = locator.get_attribute(name, timeout=tmo)
                return ActionResult(True, result={"attribute": name, "value": val}, logs=logs)
            else:
                return ActionResult(False, error=f"unsupported action: {action}", logs=logs)
            return ActionResult(True, result={"action": action, "ref": params.get("_ref")}, logs=logs)
        except Exception as e:
            logs.append({"level": "warn", "text": f"[act] {action} 失败: {type(e).__name__}: {str(e)[:120]}"})
            return ActionResult(False, error=str(e)[:300], logs=logs)

    # ------------------------------------------------------------------
    def _refine_by_semantics(self, entry, params) -> Optional[str]:
        if entry is None:
            return None
        want_role = entry.role
        want_name = (entry.name or "").strip()
        desc = (params.get("description") or "").strip()
        for ref, e in self.snap.refs.items():
            if e.kind.startswith("cdp") or e.kind == "frame":
                if want_role and e.role == want_role:
                    if not want_name or (want_name and (e.name or "").strip() == want_name):
                        return ref
        # 名称不完全相等时，做一次包含匹配
        if want_name or desc:
            target = want_name or desc
            for ref, e in self.snap.refs.items():
                if want_role and e.role != want_role:
                    continue
                if target and target in (e.name or ""):
                    return ref
        return None

    # ------------------------------------------------------------------
    def _vision_fallback(self, action, params, logs) -> Optional[ActionResult]:
        if action not in ("click", "dblclick", "fill", "check", "hover"):
            return None
        if not self.web_cfg.get("vision_grounding", True):
            return None
        if not (self.vision_ground_fn and self.screenshot_fn):
            return None
        desc = params.get("description") or params.get("text") or ""
        if not desc:
            return None
        try:
            logs.append({"level": "info", "text": f"[act] ref/selector/缓存均失败，尝试视觉定位：{desc}"})
            pt = self.vision_ground_fn(desc)
        except Exception as e:
            logs.append({"level": "warn", "text": f"[act] 视觉定位异常: {e}"})
            return None
        if not pt:
            return None
        try:
            if action in ("click", "dblclick"):
                self.page.mouse.click(pt["x"], pt["y"], click_count=2 if action == "dblclick" else 1)
            elif action == "hover":
                self.page.mouse.move(pt["x"], pt["y"])
            elif action in ("fill", "check"):
                self.page.mouse.click(pt["x"], pt["y"])
                if action == "fill":
                    self.page.keyboard.type(str(params.get("value", "")))
                else:
                    self.page.keyboard.press("Space")
            logs.append({"level": "success", "text": f"[act] 视觉定位完成 {action} @({pt['x']},{pt['y']})"})
            return ActionResult(True, result={"action": action, "vision": pt}, logs=logs, vision=True)
        except Exception as e:
            logs.append({"level": "error", "text": f"[act] 视觉动作执行失败: {e}"})
            return None

    # ------------------------------------------------------------------
    def _cache_key(self, ref, action, params):
        try:
            domain = ""
            url = self.page.url or ""
            if "://" in url:
                domain = url.split("://", 1)[1].split("/", 1)[0]
        except Exception:
            domain = ""
        entry = self.snap.snapshot_for_semantics(ref) if ref else None
        role = entry.role if entry else ""
        name = (entry.name if entry else "") or params.get("description", "")
        return domain, f"{domain}|{action}|{role}|{name}"

    def _cache_lookup(self, ref, action, params) -> Optional[str]:
        if not self.cache:
            return None
        try:
            _, key = self._cache_key(ref, action, params)
            if not params.get("description") and self.snap.snapshot_for_semantics(ref) is None:
                return None
            return self.cache.get(key)
        except Exception:
            return None

    def _cache_learn(self, ref, action, params):
        if not self.cache:
            return
        try:
            entry = self.snap.snapshot_for_semantics(ref)
            if entry is None:
                return
            _, key = self._cache_key(ref, action, params)
            selector = self._stable_selector(ref)
            if selector:
                self.cache.put(key, selector)
        except Exception:
            pass

    def _stable_selector(self, ref) -> Optional[str]:
        h = self.snap.resolve_handle(ref)
        if h is None:
            return None
        try:
            return h.evaluate(
                """(el) => {
                    const esc = (s)=> (window.CSS && CSS.escape)? CSS.escape(s) : s;
                    if (el.id) return '#' + esc(el.id);
                    const attr = (n)=> el.getAttribute && el.getAttribute(n);
                    if (attr('name')) return `[name="${attr('name')}"]`;
                    if (attr('aria-label')) return `[aria-label="${attr('aria-label')}"]`;
                    if (attr('placeholder')) return `[placeholder="${attr('placeholder')}"]`;
                    const t = (el.innerText||'').trim();
                    const tag = el.tagName.toLowerCase();
                    if (t && t.length<=30 && ['button','a','label','span'].includes(tag))
                        return `${tag}:text-is("${t}")`;
                    return null;
                }"""
            )
        except Exception:
            return None


def _looks_secret(locator, params) -> bool:
    try:
        ty = locator.get_attribute("type")
        al = (locator.get_attribute("aria-label") or "") + (locator.get_attribute("name") or "") + (locator.get_attribute("id") or "")
        if ty == "password":
            return True
        low = al.lower()
        return any(k in low for k in ("password", "pwd", "密码", "token", "secret", "key", "cvv"))
    except Exception:
        return False
