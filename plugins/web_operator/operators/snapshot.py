# -*- coding: utf-8 -*-
"""
Snapshot Engine - 结构化感知层
==============================
用 Chrome DevTools Protocol 的 Accessibility Tree 把页面"可交互部分"序列化成
带 ref 编号的紧凑文本快照，供 LLM 通过 ref 精确引用元素，而不是靠猜 CSS 选择器。

已在本项目捆绑的 Playwright 1.62 + Edge/Chromium 上实测：
  - Accessibility.getFullAXTree 返回浏览器内核算好的 computed role / accname
  - AX 树天然穿透 open shadow DOM（无需自己递归）
  - DOM.resolveNode(backendNodeId) + Runtime.callFunctionOn 能给真实 DOM 节点打标记
  - page.query_selector("[data-eb-ref=eN]") 能解析到 shadow DOM 内的元素

局限（诚实标注）：
  - 主 frame 的 AX 树不包含 iframe 内容（实测跨/同源都不在内），因此 iframe 单独用
    JS 启发式枚举（role 由标签/aria 推断，name 由 aria-label/文本/alt 推断），
    保真度低于主 frame 的 CDP 路径。
"""

import json
from typing import Any, Dict, List, Optional

REF_ATTR = "data-eb-ref"

# 交互性角色白名单（AX computed role）
INTERACTIVE_ROLES = {
    "button", "link", "textbox", "searchbox", "combobox", "listbox",
    "menuitem", "menuitemcheckbox", "menuitemradio", "option",
    "checkbox", "radio", "slider", "spinbutton", "tab", "treeitem",
    "switch", "tree", "grid", "menu", "menubar",
}

# 值得展示的语义结构角色（非交互，但帮助模型理解页面）
STRUCTURAL_ROLES = {
    "heading", "form", "dialog", "alertdialog", "main", "navigation",
    "article", "region", "table", "row", "cell", "columnheader", "rowheader",
    "img", "paragraph", "text", "list", "listitem", "status", "alert",
}

_USEFUL_PROPS = {
    "disabled", "checked", "expanded", "selected", "required",
    "readonly", "pressed", "valuetext", "focusable", "hidden", "invalid",
}


def _ax_value(node: Dict[str, Any], key: str) -> str:
    v = node.get(key) or {}
    if isinstance(v, dict):
        val = v.get("value")
        return "" if val is None else str(val)
    return str(v or "")


def _ax_props(node: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for p in node.get("properties") or []:
        name = p.get("name")
        if name in _USEFUL_PROPS:
            val = (p.get("value") or {})
            out[name] = val.get("value") if isinstance(val, dict) else val
    return out


class RefEntry:
    """一个可引用元素的记录。"""
    __slots__ = ("ref", "role", "name", "kind", "depth", "props", "frame_url", "value_hint")

    def __init__(self, ref, role, name, kind, depth=0, props=None, frame_url="", value_hint=""):
        self.ref = ref
        self.role = role
        self.name = name
        self.kind = kind          # "cdp" (主 frame) 或 "frame" (iframe 内容)
        self.depth = depth
        self.props = props or {}
        self.frame_url = frame_url
        self.value_hint = value_hint


# ------------------------------------------------------------------
# iframe / 无 CDP 时的 JS 枚举器：递归穿透 open shadow DOM
# 给每个可交互元素打 data-eb-ref，并返回结构化清单
# ------------------------------------------------------------------
_JS_ENUMERATE = r"""
(cfg) => {
  const attr = cfg.attr;
  const roles = new Set(cfg.interactiveRoles);
  const startSeq = cfg.startSeq || 1;
  const attrPrefix = cfg.attrPrefix || 'e';

  const STYLE_INT = new Set(['button','a','input','select','textarea','summary','details']);
  function inferRole(el){
    const aria = el.getAttribute && el.getAttribute('role');
    if (aria) return aria;
    const tag = el.tagName ? el.tagName.toLowerCase() : '';
    const type = (el.getAttribute && (el.getAttribute('type')||'')).toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'a' && el.getAttribute && el.getAttribute('href')) return 'link';
    if (tag === 'select') return (el.multiple ? 'listbox' : 'combobox');
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input'){
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'range') return 'slider';
      if (type === 'number') return 'spinbutton';
      if (['submit','button','reset','image'].includes(type)) return 'button';
      if (['text','search','email','tel','url','password',''].includes(type)) return 'textbox';
      return 'textbox';
    }
    if (el.isContentEditable) return 'textbox';
    const tabindex = el.getAttribute && el.getAttribute('tabindex');
    if (tabindex !== null && tabindex !== 'undefined') return 'generic';
    return '';
  }
  function accName(el, role){
    const g = (n)=> el.getAttribute ? (el.getAttribute(n)||'') : '';
    let lbl = g('aria-label'); if (lbl) return lbl.trim();
    const lb = g('aria-labelledby');
    if (lb){ try { return lb.split(/\s+/).map(id=>{const n=document.getElementById(id);return n?n.innerText:'';}).join(' ').trim(); } catch(e){} }
    if (role==='textbox' || role==='searchbox'){
      const ph = g('placeholder'); if (ph) return ph.trim();
      const nm = g('name'); if (nm) return nm.trim();
    }
    const alt = g('alt'); if (alt) return alt.trim();
    const title = g('title'); if (title) return title.trim();
    const vt = (el.value && typeof el.value==='string') ? el.value.trim() : '';
    let txt = '';
    try { txt = (el.innerText || el.textContent || '').trim(); } catch(e){}
    return (txt || vt || '').slice(0,120);
  }
  function visible(el){
    try {
      const st = window.getComputedStyle(el);
      if (st && (st.display==='none' || st.visibility==='hidden' || st.opacity==='0')) return false;
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) return false;
    } catch(e){ return false; }
    if (el.getAttribute && el.getAttribute('aria-hidden')==='true') return false;
    return true;
  }
  function stateProps(el, role){
    const p = {};
    if (el.disabled) p.disabled = true;
    if (role==='checkbox' || role==='radio'){ if (el.checked) p.checked = true; }
    if (el.getAttribute){
      const ex = el.getAttribute('aria-expanded'); if (ex) p.expanded = (ex==='true');
      const sel = el.getAttribute('aria-selected'); if (sel) p.selected = (sel==='true');
      if (el.required) p.required = true;
      if (el.readOnly) p.readonly = true;
    }
    return p;
  }

  const out = [];
  let seq = startSeq;
  const seen = new WeakSet();

  function walk(root){
    let all;
    try { all = root.querySelectorAll('*'); } catch(e){ return; }
    for (const el of all){
      if (!el || el.tagName === 'HTML' || el.tagName === 'BODY' || el.tagName === 'HEAD') {
        // still descend into body's shadow? handled below via loop; skip tagging body/html
      }
      const role = inferRole(el);
      const isInteractive = roles.has(role) || STYLE_INT.has((el.tagName||'').toLowerCase());
      if (isInteractive && role && visible(el)){
        const ref = attrPrefix + seq; seq += 1;
        try { el.setAttribute(attr, ref); } catch(e){}
        const name = accName(el, role);
        let href=''; try { href = el.getAttribute && (el.getAttribute('href')||''); } catch(e){}
        out.push({ ref, role, name, props: stateProps(el, role), href: href||'' });
      }
      // 递归进 shadowRoot（穿透 open shadow DOM）
      if (el.shadowRoot && !seen.has(el.shadowRoot)){
        seen.add(el.shadowRoot);
        walk(el.shadowRoot);
      }
    }
  }
  walk(document);
  return out;
}
"""

_JS_CLEAR_REFS = r"""
(attr) => {
  const seen = new WeakSet();
  function clr(root){
    try { root.querySelectorAll('['+attr+']').forEach(e=>e.removeAttribute(attr)); } catch(e){}
    let all; try { all = root.querySelectorAll('*'); } catch(e){ all = []; }
    for (const el of all){
      if (el.shadowRoot && !seen.has(el.shadowRoot)){ seen.add(el.shadowRoot); clr(el.shadowRoot); }
    }
  }
  clr(document);
  return true;
}
"""


class SnapshotEngine:
    def __init__(self, page, cdp_session, config: Optional[Dict[str, Any]] = None):
        self.page = page
        self.cdp = cdp_session
        self.config = config or {}
        self.refs: Dict[str, RefEntry] = {}
        self._enabled = False
        self._global_seq = 0  # 跨 capture 递增，保证 data-eb-ref 值全局唯一不冲突

    # ------------------------------------------------------------------
    def capture(self, interactive_only: bool = True) -> str:
        """重新采集快照，刷新 self.refs，返回紧凑文本快照。"""
        self.refs = {}
        self._global_seq = 0  # 每次 capture 前清空旧标记，编号从 e1 重新开始
        self._ensure_cdp()
        lines: List[str] = []
        try:
            lines.extend(self._capture_main(interactive_only))
        except Exception as e:
            lines.append(f"# main-frame snapshot error: {type(e).__name__}: {e}")
        try:
            lines.extend(self._capture_frames(interactive_only))
        except Exception:
            pass
        if not lines:
            return "(空快照：页面没有可交互元素或尚未加载)"
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def _ensure_cdp(self):
        if self._enabled or self.cdp is None:
            return
        try:
            self.cdp.send("Accessibility.enable")
            self.cdp.send("DOM.enable")
            self.cdp.send("Runtime.enable")
            self._enabled = True
        except Exception:
            self._enabled = False

    def _next_ref(self) -> str:
        self._global_seq += 1
        return f"e{self._global_seq}"

    # ------------------------------------------------------------------
    def _capture_main(self, interactive_only: bool) -> List[str]:
        """主 frame：CDP AX 树（computed role/name，穿透 shadow DOM）。"""
        # 先清掉旧的标记（含 shadow），避免 query_selector 命中残留
        try:
            self.page.evaluate(_JS_CLEAR_REFS, REF_ATTR)
        except Exception:
            pass
        raw = self.cdp.send("Accessibility.getFullAXTree")
        nodes = raw.get("nodes") or []
        by_id = {n.get("nodeId"): n for n in nodes}
        # 计算 parent 关系以得到缩进深度
        parent_of: Dict[str, str] = {}
        for n in nodes:
            for cid in n.get("childIds") or []:
                parent_of[cid] = n.get("nodeId")

        def depth_of(node_id: str) -> int:
            d = 0
            cur = node_id
            guard = 0
            while cur in parent_of and guard < 20:
                cur = parent_of[cur]
                d += 1
                guard += 1
            return d

        lines: List[str] = []
        for n in nodes:
            if n.get("ignored"):
                continue
            role = _ax_value(n, "role")
            name = _ax_value(n, "name")
            interactive = role in INTERACTIVE_ROLES
            if not interactive:
                if interactive_only:
                    continue
                if role not in STRUCTURAL_ROLES:
                    continue
            bnid = n.get("backendDOMNodeId")
            props = _ax_props(n)
            ref = self._next_ref()
            tagged = False
            if bnid is not None:
                tagged = self._tag_cdp_node(bnid, ref)
            if not tagged and interactive:
                # 打不上标记的交互节点无法被动作引擎引用，跳过以免给出无效 ref
                continue
            entry = RefEntry(
                ref=ref, role=role, name=name, kind="cdp" if tagged else "cdp_untagged",
                depth=depth_of(n.get("nodeId")), props=props,
            )
            self.refs[ref] = entry
            lines.append(self._format_line(entry))
        if interactive_only:
            lines.insert(0, "# 可交互元素（用 ref 引用操作）")
        return lines

    def _tag_cdp_node(self, backend_node_id: int, ref: str) -> bool:
        try:
            r = self.cdp.send("DOM.resolveNode", {"backendNodeId": int(backend_node_id)})
            obj_id = (r.get("object") or {}).get("objectId")
            if not obj_id:
                return False
            self.cdp.send("Runtime.callFunctionOn", {
                "objectId": obj_id,
                "functionDeclaration": f"function(v){{ this.setAttribute('{REF_ATTR}', v); }}",
                "arguments": [{"value": ref}],
            })
            return True
        except Exception:
            return False

    def _capture_frames(self, interactive_only: bool) -> List[str]:
        """iframe：Playwright frames + JS 枚举（含 shadow 递归）。主 frame 跳过。"""
        lines: List[str] = []
        try:
            frames = list(self.page.frames)
        except Exception:
            return lines
        for fr in frames:
            try:
                if fr == self.page.main_frame:
                    continue
                fr.evaluate(_JS_CLEAR_REFS, REF_ATTR)
                items = fr.evaluate(_JS_ENUMERATE, {
                    "attr": REF_ATTR,
                    "interactiveRoles": sorted(INTERACTIVE_ROLES),
                    "startSeq": self._global_seq + 1,
                    "attrPrefix": "e",
                })
            except Exception:
                continue
            url = ""
            try:
                url = fr.url or ""
            except Exception:
                pass
            for it in (items or []):
                ref = it.get("ref")
                # JS 内部已递增 seq，与全局 seq 对齐
                try:
                    num = int(ref[1:])
                    if num > self._global_seq:
                        self._global_seq = num
                except Exception:
                    pass
                entry = RefEntry(
                    ref=ref, role=it.get("role", ""), name=it.get("name", ""),
                    kind="frame", frame_url=url, props=it.get("props") or {},
                )
                self.refs[ref] = entry
                lines.append(self._format_line(entry, prefix=f"(iframe {url[:40]}) " if url else "(iframe) "))
        if lines:
            lines.insert(0, "# iframe 元素")
        return lines

    def _format_line(self, e: RefEntry, prefix: str = "") -> str:
        indent = "  " * min(e.depth, 12)
        name = (e.name or "").replace("\n", " ").strip()
        if len(name) > 80:
            name = name[:80] + "…"
        seg = f"{indent}- {prefix}{e.role}"
        if name:
            seg += f' "{name}"'
        flags = []
        p = e.props
        if p.get("disabled"):
            flags.append("disabled")
        if p.get("checked"):
            flags.append("checked")
        if p.get("expanded") is True:
            flags.append("expanded")
        if p.get("selected"):
            flags.append("selected")
        if p.get("required"):
            flags.append("required")
        if e.value_hint:
            flags.append(f"value={e.value_hint}")
        seg += f" [ref={e.ref}]"
        if flags:
            seg += " (" + ",".join(flags) + ")"
        return seg

    # ------------------------------------------------------------------
    def resolve_handle(self, ref: str):
        """ref -> Playwright ElementHandle（穿透 shadow；iframe 走对应 frame）。"""
        entry = self.refs.get(ref)
        if entry is None:
            return None
        selector = f'[{REF_ATTR}={ref}]'
        if entry.kind == "frame":
            for fr in self.page.frames:
                try:
                    if fr.url == entry.frame_url or (entry.frame_url and fr.url and entry.frame_url in fr.url):
                        h = fr.query_selector(selector)
                        if h:
                            return h
                except Exception:
                    continue
            # url 变了导致找不到，退回全 frame 扫描
            for fr in self.page.frames:
                try:
                    h = fr.query_selector(selector)
                    if h:
                        return h
                except Exception:
                    continue
            return None
        try:
            return self.page.query_selector(selector)
        except Exception:
            return None

    def locate(self, ref: str):
        """返回 (locator_or_handle, kind)。供动作引擎执行带 auto-wait 的操作。"""
        entry = self.refs.get(ref)
        if entry is None:
            return None, None
        selector = f'[{REF_ATTR}={ref}]'
        if entry.kind == "frame":
            for fr in self.page.frames:
                try:
                    if (fr.url == entry.frame_url) or (entry.frame_url and fr.url and entry.frame_url in fr.url):
                        if fr.query_selector(selector):
                            return fr.locator(selector), "frame"
                except Exception:
                    continue
            return None, None
        return self.page.locator(f'css={selector}'), "cdp"

    def snapshot_for_semantics(self, ref: str) -> Optional[RefEntry]:
        return self.refs.get(ref)

    def invalidate(self):
        self.refs = {}
