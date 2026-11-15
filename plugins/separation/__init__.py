# -*- coding: utf-8 -*-
"""separation 插件 —— 只读状态面板。

启用本身是终端动作（`python -m separation.enable --yes`），面板不提供按钮：
不可逆开关不应该藏在一次误点里。这里只展示状态、检查条件、审计尾部。
"""
from __future__ import annotations

import os
from pathlib import Path

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "separation",
    "version": "1.1.0",
    "api_version": 1,
    "author": "EggBloom",
    "description": "分离（切离、交出）与真实：只读状态面板（不可逆开关只在终端启用）",
    "dependencies": [],
    "priority": 95,
    "default_enabled": True,
}

_ROOT = Path(__file__).resolve().parent.parent.parent


def _sep():
    import separation
    return separation


def status(params=None):
    try:
        sep = _sep()
        st = sep.status()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    try:
        from separation import audit
        st["audit"] = audit.tail(8)
    except Exception:
        st["audit"] = []
    try:
        from separation import stance
        tb = stance.table()
        st["stance"] = {"removed": len(tb.get("removed") or []),
                        "tunable": len(tb.get("tunable") or []),
                        "kept": len(tb.get("kept") or []),
                        "note": tb.get("note", "")}
    except Exception:
        st["stance"] = {}
    st["python"] = str(os.sys.executable)
    st["enable_hint"] = (
        f'python -m separation.enable --root "{st.get("root")}" '
        f'--statement "你的交接一句话" --publisher "你的名字" --yes'
    )
    return {"ok": True, **st}


def stance_view(params=None):
    """三分类表 + 当前可改默认值（只读）。"""
    try:
        from separation import stance
        data_dir = os.path.join(str(_ROOT), "data")
        return {"ok": True, "table": stance.table(), "values": stance.load(data_dir)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def plan(params=None):
    try:
        from separation.enable import plan as _plan
        sep = _sep()
        root = (params or {}).get("root") or sep.status().get("root") or str(_ROOT)
        return {"ok": True, "plan": _plan(root)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def audit_tail(params=None):
    try:
        from separation import audit
        n = int((params or {}).get("n") or 20)
        return {"ok": True, "items": audit.tail(n)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


PLUGIN_ACTIONS = {
    "status": status,
    "plan": plan,
    "audit_tail": audit_tail,
    "stance": stance_view,
}
