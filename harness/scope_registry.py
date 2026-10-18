# -*- coding: utf-8 -*-
"""harness/scope_registry.py —— 作用域能力注册表（T7 · P4 · S1 骨架）。

═══════════════════════════════════════════════════════════════════════════
参考源（照搬其机制，不自行发明）：
  D:\\git\\_eggbloom-backup-20261003\\dsh-src\\

  · dsh-sandbox-policy
      - 三级解析：`生效 = 显式授予 ?? fold(事件) ?? 部署默认`
      - 状态是**会话日志里的事件**，任何机制都不带外修改；跨重启靠**回放**
      - 无效配置**在加载时拒绝**（拼错要显式报错，不静默改策略）
  · dsh-permission-presets
      - **预设把多个旋钮捆成一组**（用户选档位，不逐个开关）
      - **执行方各自保留自己的值** —— 本模块只拥有「选择与默认值」
        ⇒ 撤掉本模块，最后一次取值依然生效（**软依赖**）
      - 保留名 `custom`：可看不可选；切换**只改有差异的**，重选当前**零变化**（幂等）
  · dsh-agent-preset
      - 变更**只影响之后**的判定（不追溯已开始的会话）

本 S1 范围：**模块自足，尚未接任何调用点** —— 对现网零行为变化。
═══════════════════════════════════════════════════════════════════════════
"""
import json
import os
import threading
import time

# ── 封闭词汇（不变量：词汇外的能力名一律拒绝；见 validate_event）──────────
RISK_LOW = "low"
RISK_MID = "mid"
RISK_HIGH = "high"
RISK_TIERS = (RISK_LOW, RISK_MID, RISK_HIGH)

# 词汇表 = TOOL_FUNCTIONS 的键 + 群聊默认工具 + 行为能力。
# 风险档来源：设计稿 3.2 的默认表；表里未列的三个（take_screenshot / get_system_info /
# agent / proactive）由本模块补档并在提交说明里标出，请复核。
DEFAULT_VOCABULARY = {
    # 行为能力
    "reply": RISK_LOW,          # 能否在该作用域发言
    "meme": RISK_LOW,           # 发表情包
    "agent": RISK_MID,          # 群内 agent 模式
    "proactive": RISK_MID,      # 主动搭话
    # 工具能力 —— 低
    "get_group_members": RISK_LOW,
    "search_web": RISK_LOW,
    # 工具能力 —— 中
    "generate_image": RISK_MID,
    "web_operate": RISK_MID,
    "get_system_info": RISK_MID,      # ← 表未列，本模块补档（只读系统信息）
    # 工具能力 —— 高（碰本机 / 外发 / 执行）
    "take_screenshot": RISK_HIGH,     # ← 表未列，本模块补档（截图会把屏幕发出去）
    "open_app": RISK_HIGH,
    "control_volume": RISK_HIGH,
    "control_power": RISK_HIGH,
    "manage_process": RISK_HIGH,
    "search_files": RISK_HIGH,
    "send_user_file": RISK_HIGH,
}

# 预设 = 一组捆绑。
#   - `max_risk` 是**上限**（收紧方向）：档位高于它的能力一律不放行
#   - `override_allowlist` 是**放宽方向**：True 时不再受既有「全局名单」约束
#
# ★ S2 实现中发现的设计修正（S1 写的是「standard 拒绝高风险」，那是错的）：
#   现状里 `group_tool_allowlist` 是**用户自己列的名单**，可能包含高风险工具
#   （如 `web_operate`，甚至有人会放 `open_app`）。如果 `standard` 一律拒高风险，
#   就会**改变这些人的现网行为**，违反"上线零行为变化"的承诺。
#   所以 `standard` 的定义改为「**注册表不额外收紧**」（全部档位放行），
#   真正的收紧交给既有的全局名单去管；
#   要收紧用 `readonly`，要放宽（解除名单约束）用 `full` —— 两个方向都显式可选。
DEFAULT_PRESETS = {
    # = 今天的行为：不额外收紧（原名单说了算）
    "standard": {"max_risk": RISK_HIGH, "override_allowlist": False},
    # 收紧：只到低风险
    "readonly": {"max_risk": RISK_LOW, "override_allowlist": False},
    # 放宽：解除原名单约束（仅在你信得过的群显式选）
    "full": {"max_risk": RISK_HIGH, "override_allowlist": True},
}
DEFAULT_PRESET = "standard"

_RISK_ORDER = {RISK_LOW: 0, RISK_MID: 1, RISK_HIGH: 2}

# 保留名（照搬 permission-presets）：可看不可选
RESERVED_PRESETS = ("custom",)

# ★ 降级兜底白名单：**必须是静态常量**，不能从内部状态推导。
# 缘由（S6 的测试抓出来的真问题）：降级路径若去读 `self._vocab` 来挑低风险能力，
# 那么"词汇表被破坏"这种故障会让**降级本身也失败** → 能力集为空 → 连"回话"都被否掉
# → 永久在线的群机器人直接变哑巴。兜底表独立存在才能保证"低风险一定放行"。
_LOW_RISK_FALLBACK = frozenset(("reply", "meme", "get_group_members", "search_web"))

SCOPE_KINDS = ("group", "private", "web")

_DEFAULT_EVENTS_FILE = os.environ.get(
    "EGGBLOOM_SCOPE_EVENTS",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "logs", "scope_policy_events.jsonl"))


class ScopeConfigError(ValueError):
    """非法配置 —— 在**加载时**抛出（照搬 sandbox-policy：拼错要显式报错）。"""


def scope_key(kind: str, sid) -> str:
    """作用域键。两两作用域绝不共享状态（照搬 sandbox-policy 的会话隔离）。"""
    return "%s:%s" % (str(kind or "").strip(), str(sid or "").strip())


class ScopeRegistry:
    """作用域能力注册表。只拥有「选择与默认值」；执行方各自保留取值。"""

    def __init__(self, *, events_path: str = None, default_preset: str = DEFAULT_PRESET,
                 vocabulary: dict = None, presets: dict = None, on_warn=None):
        self._vocab = dict(vocabulary or DEFAULT_VOCABULARY)
        self._presets = {k: dict(v) for k, v in (presets or DEFAULT_PRESETS).items()}
        self._events_path = events_path or _DEFAULT_EVENTS_FILE
        self._lock = threading.Lock()
        self._events = []          # 全部事件（内存镜像；回放用）
        self._states = {}          # scope_key -> {"preset": str|None, "overrides": {cap: bool}}
        self._fold_cache = {}      # 兼容旧字段名：scope_key -> preset（fold 出来的具名预设）
        self._warn = on_warn or (lambda msg: print("[scope] " + str(msg)))
        self._degraded = 0

        # ── 加载时校验（非法配置直接报错，不静默改策略）──
        if default_preset in RESERVED_PRESETS:
            raise ScopeConfigError("默认预设不能用保留名: %r" % default_preset)
        if default_preset not in self._presets:
            raise ScopeConfigError(
                "默认预设 %r 不在预设表里（可选：%s）" % (default_preset, ", ".join(sorted(self._presets))))
        for pname, table in self._presets.items():
            if pname in RESERVED_PRESETS:
                raise ScopeConfigError("预设表不得包含保留名: %r" % pname)
            mr = table.get("max_risk")
            if mr not in RISK_TIERS:
                raise ScopeConfigError("预设 %r 的 max_risk 非法: %r" % (pname, mr))
            for k in table:
                if k not in ("max_risk", "override_allowlist"):
                    raise ScopeConfigError("预设 %r 含未知字段: %r" % (pname, k))
        self._default_preset = default_preset

        self._replay()

    # ── 词汇与预设（只读）────────────────────────────────────────────
    def capabilities(self) -> dict:
        return dict(self._vocab)

    def presets(self) -> dict:
        """可选项（含保留名，供 UI 展示为「可看不可选」）。"""
        out = {k: dict(v) for k, v in self._presets.items()}
        for name in RESERVED_PRESETS:
            out[name] = {"reserved": True}
        return out

    def default_preset(self) -> str:
        return self._default_preset

    @property
    def events_path(self) -> str:
        return self._events_path

    def _preset_caps(self, preset: str) -> frozenset:
        table = self._presets.get(preset) or {}
        cap = _RISK_ORDER.get(table.get("max_risk", RISK_HIGH), 2)
        return frozenset(n for n, risk in self._vocab.items()
                         if _RISK_ORDER.get(risk, 1) <= cap)

    # ── 逐能力覆盖（让 `custom` 真的可达，也让"让某个群闭嘴"成为可能）──
    def set_capability(self, kind: str, sid, capability: str, allow: bool = True,
                       by: str = "") -> dict:
        """在预设之上对**单个能力**做覆盖。幂等：值没变则不追加事件。"""
        key = scope_key(kind, sid)
        if capability not in self._vocab:
            return {"changed": False, "scope": key,
                    "reason": "词汇外的能力: %r" % capability}
        cur = dict(self._fold_state(key).get("overrides") or {})
        if bool(cur.get(capability)) == bool(allow) and capability in cur:
            return {"changed": False, "scope": key, "reason": "已是该值（幂等）"}
        ev = {"ts": time.time(), "scope": key, "kind": str(kind), "sid": str(sid),
              "by": str(by or ""), "overrides": {capability: bool(allow)}}
        ok, why = self.validate_event(ev)
        if not ok:
            return {"changed": False, "scope": key, "reason": why}
        self._apply_event(ev)
        self._append_disk(ev)
        return {"changed": True, "scope": key, "capability": capability, "allow": bool(allow)}

    def clear_overrides(self, kind: str, sid, by: str = "") -> dict:
        """离开 custom：清空覆盖，回到具名预设（照搬 permission-presets「用户可以离开该状态」）。"""
        key = scope_key(kind, sid)
        if not self._fold_state(key).get("overrides"):
            return {"changed": False, "scope": key, "reason": "本就没有覆盖（幂等）"}
        preset = self._fold(key) or self._default_preset
        ev = {"ts": time.time(), "scope": key, "kind": str(kind), "sid": str(sid),
              "by": str(by or ""), "to": preset, "clear_overrides": True}
        ok, why = self.validate_event(ev)
        if not ok:
            return {"changed": False, "scope": key, "reason": why}
        self._apply_event(ev)
        self._append_disk(ev)
        return {"changed": True, "scope": key, "preset": preset}

    def overrides(self, kind: str, sid) -> dict:
        return dict(self._fold_state(scope_key(kind, sid)).get("overrides") or {})

    def _apply_event(self, ev: dict):
        """把一条事件折进状态（内存）并记入事件列表。调用方负责先校验。"""
        key = ev["scope"]
        with self._lock:
            self._events.append(ev)
            st = self._states.setdefault(key, {"preset": None, "overrides": {}})
            if ev.get("clear_overrides"):
                st["overrides"] = {}
            if ev.get("overrides"):
                st["overrides"].update(ev["overrides"])
            to = ev.get("to")
            if to:
                st["preset"] = to
                if not ev.get("overrides"):
                    st["overrides"] = {}      # 切到具名预设 = 离开 custom
            self._fold_cache[key] = st["preset"]

    def declare_capability(self, name: str, risk: str = RISK_MID) -> bool:
        """插件声明能力（封闭词汇的扩充入口）。风险档非法则拒绝声明。"""
        name = str(name or "").strip()
        if not name or risk not in RISK_TIERS:
            return False
        with self._lock:
            self._vocab[name] = risk
        return True

    # ── 三级解析（照搬 sandbox-policy）──────────────────────────────
    def resolve(self, kind: str, sid, explicit: str = None) -> dict:
        """返回该作用域的生效策略。

        `生效 = 显式授予 ?? fold(事件) ?? 部署默认`

        本方法**绝不抛异常**（它在调用路径上）：任何异常都退回安全默认并记一条告警。
        """
        try:
            preset = explicit or self._fold(scope_key(kind, sid)) or self._default_preset
            src = "explicit" if explicit else ("events" if self._fold(scope_key(kind, sid)) else "default")
            if preset not in self._presets:          # 不变式：词汇外的预设名不受理
                self._degraded += 1
                self._warn("拒绝词汇外的预设名 %r（作用域 %s），退回部署默认"
                           % (preset, scope_key(kind, sid)))
                preset, src = self._default_preset, "default"
            table = self._presets[preset]
            _cap_risk = _RISK_ORDER.get(table.get("max_risk", RISK_HIGH), 2)
            caps = set(n for n, risk in self._vocab.items()
                       if _RISK_ORDER.get(risk, 1) <= _cap_risk)
            # ── 逐能力覆盖（不变量：词汇外的一律忽略并告警）──
            overrides = dict(self._fold_state(scope_key(kind, sid)).get("overrides") or {})
            if overrides:
                for cap, allow in overrides.items():
                    if cap not in self._vocab:
                        self._warn("忽略词汇外的覆盖项 %r（作用域 %s）" % (cap, scope_key(kind, sid)))
                        continue
                    if allow:
                        caps.add(cap)
                    else:
                        caps.discard(cap)
            # `custom`：不匹配任何预设的组合 —— **推导出来、不可主动选**（照搬 permission-presets）
            eff_preset = preset
            if overrides and frozenset(caps) != self._preset_caps(preset):
                eff_preset = "custom"
            return {"scope": scope_key(kind, sid), "preset": eff_preset,
                    "named_preset": preset, "source": src,
                    "capabilities": frozenset(caps),
                    "overrides": overrides,
                    "override_allowlist": bool(table.get("override_allowlist")),
                    "degraded": False}
        except Exception as e:
            # 软依赖：注册表自身坏了也不能让主流程崩 —— 低风险放行、高风险拒绝
            self._degraded += 1
            self._warn("注册表解析异常，降级（低风险放行 / 高风险拒绝）: %s" % e)
            # ★ 用**静态兜底表**，绝不再读可能已损坏的内部状态（否则降级自己也会失败）
            caps = set(_LOW_RISK_FALLBACK)
            try:
                caps |= {n for n, risk in (self._vocab or {}).items() if risk == RISK_LOW}
            except Exception:
                pass
            return {"scope": scope_key(kind, sid), "preset": self._default_preset,
                    "named_preset": self._default_preset, "source": "degraded",
                    "capabilities": frozenset(caps), "overrides": {},
                    "override_allowlist": False, "degraded": True}

    def is_allowed(self, kind: str, sid, capability: str, explicit: str = None) -> bool:
        """单一判定点：某个作用域能不能做某件事。词汇外的能力一律拒绝。"""
        if capability not in self._vocab:
            return False
        return capability in self.resolve(kind, sid, explicit=explicit)["capabilities"]

    # ── 变更：事件追加 + 幂等 + 不追溯 ───────────────────────────────
    def set_preset(self, kind: str, sid, preset: str, by: str = "") -> dict:
        """切换该作用域的预设。

        - 保留名 / 词汇外预设 → 拒绝（不落事件）
        - **幂等**：已是该预设 → 不追加任何事件（照搬 permission-presets）
        - **不追溯**：只影响之后的解析，不改变已开始的会话（照搬 agent-preset）
        """
        key = scope_key(kind, sid)
        if preset in RESERVED_PRESETS or preset not in self._presets:
            return {"changed": False, "scope": key, "reason": "非法预设名: %r" % preset}
        cur = self._fold(key)
        if cur == preset:
            return {"changed": False, "scope": key, "preset": preset, "reason": "已是该预设（幂等）"}
        ev = {"ts": time.time(), "scope": key, "kind": str(kind), "sid": str(sid),
              "from": cur, "to": preset, "by": str(by or "")}
        ok, why = self.validate_event(ev)
        if not ok:
            return {"changed": False, "scope": key, "reason": why}
        self._apply_event(ev)
        self._append_disk(ev)
        return {"changed": True, "scope": key, "preset": preset, "from": cur}

    def fold(self, kind: str, sid) -> str:
        """该作用域当前预设（无事件 → None）。"""
        return self._fold(scope_key(kind, sid))

    def _fold(self, key: str):
        with self._lock:
            return self._fold_cache.get(key)

    def _fold_state(self, key: str) -> dict:
        with self._lock:
            return dict(self._states.get(key) or {})

    def events(self, kind: str = None, sid=None, limit: int = 50) -> list:
        k = scope_key(kind, sid) if kind is not None else None
        with self._lock:
            rows = [e for e in self._events if (k is None or e.get("scope") == k)]
        return rows[-int(limit):] if limit else rows

    def audit(self, limit: int = 50) -> list:
        """审计留痕（谁、何时、把哪个作用域从什么切到什么）。"""
        return self.events(limit=limit)

    def stats(self) -> dict:
        """概览：作用域数 / 事件数 / 覆盖数 / **降级次数**（P10 的审计配对半）。

        防御式：内部状态被破坏时也要给出可读结果（它是接口面，不该抛）。
        """
        try:
            with self._lock:
                n_ov = sum(len((s or {}).get("overrides") or {}) for s in self._states.values())
                return {"scopes": len(self._states), "events": len(self._events),
                        "overrides": n_ov, "degraded": self._degraded,
                        "default_preset": self._default_preset,
                        "caps": len(self._vocab or {}), "presets": len(self._presets or {})}
        except Exception as e:
            return {"error": str(e), "degraded": -1}

    def degraded(self) -> int:
        return int(self._degraded)

    # ── 不变式校验（照搬各包的 invariant.js：拒绝词汇外的伪造值）──────
    def validate_event(self, ev) -> tuple:
        if not isinstance(ev, dict):
            return False, "事件不是字典"
        if not str(ev.get("scope") or "").strip():
            return False, "事件缺少字段 scope"
        to = ev.get("to")
        ov = ev.get("overrides")
        clr = bool(ev.get("clear_overrides"))
        if to in (None, "") and not ov and not clr:
            return False, "事件既没有 to 也没有 overrides（空事件）"
        if to not in (None, ""):
            if to in RESERVED_PRESETS:
                return False, "预设名 %r 是保留名，不可作为切换目标" % to
            if to not in self._presets:
                return False, "预设名 %r 不在封闭词汇里" % to
        if ov is not None:
            if not isinstance(ov, dict) or not ov:
                return False, "overrides 必须是非空字典"
            for cap, allow in ov.items():
                if not isinstance(allow, bool):
                    return False, "overrides[%r] 必须是布尔值" % cap
                if cap not in self._vocab:
                    return False, "覆盖项 %r 不在封闭词汇里" % cap
        frm = ev.get("from")
        if frm not in (None, "") and frm not in self._presets:
            return False, "from 字段 %r 不在封闭词汇里" % frm
        return True, ""

    # ── 回放（照搬 sandbox-policy：状态可从事件流重建）───────────────
    def _replay(self, path: str = None):
        """从事件流重建 fold。非法事件**跳过并告警**（不静默接受，也不因此崩）。"""
        p = path or self._events_path
        if not p or not os.path.isfile(p):
            return 0
        n_ok = n_bad = 0
        try:
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        n_bad += 1
                        continue
                    ok, why = self.validate_event(ev)
                    if not ok:
                        n_bad += 1
                        self._warn("回放跳过非法事件（%s）: %s" % (why, line[:120]))
                        continue
                    self._apply_event(ev)
                    n_ok += 1
        except Exception as e:
            self._warn("回放失败（按空状态继续）: %s" % e)
        if n_bad:
            self._warn("回放完成：接受 %d 条，跳过 %d 条非法事件" % (n_ok, n_bad))
        return n_ok

    def reload(self) -> int:
        """重新从磁盘回放（测试与热加载用）。"""
        with self._lock:
            self._events.clear()
            self._fold_cache.clear()
            self._states.clear()
        return self._replay()

    def _append_disk(self, ev: dict):
        """只追加，从不改写（照搬 sandbox-policy：任何机制都不带外修改状态）。"""
        try:
            os.makedirs(os.path.dirname(self._events_path), exist_ok=True)
            with open(self._events_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        except Exception as e:
            self._warn("事件落盘失败（内存状态已生效）: %s" % e)
