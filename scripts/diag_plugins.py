# -*- coding: utf-8 -*-
"""插件接管自检：判断「插件开着」还是「插件真的在起作用」。

区别很重要：
  · 开着 = PLUGIN_META.enabled / load_all 成功 / 挂着 before_reply 钩子
  · 起作用 = 钩子真的被派发、返回值真的进了提示词或真的改了回复

这个脚本逐层实测，最后给一张表。只读，不写业务数据
（唯一的写是触发 before_reply 时 mood 可能落盘，用全新 group_id 规避）。

用法：
    python scripts/diag_plugins.py
"""
import os
import sys
import json

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
os.chdir(APP)

out = []


def p(s=""):
    out.append(str(s))


TEST_GID = "_diag_probe_group_"      # 不存在的群，避免污染真实数据
TEST_MSG = "这新番真好看啊，你们看了吗"

# ── 沙箱：把会写盘的库重定向到临时目录 ──────────────────
# before_reply 不是纯函数：super_memory 会插 messages/affection，
# 上一次跑完就在生产库里留下了 13 条假群记录。诊断脚本绝不能有这个副作用，
# 所以先把 DB_PATH 指到 tempfile 目录，跑完即弃。
import tempfile  # noqa: E402
_SANDBOX = tempfile.mkdtemp(prefix="eggbloom_diag_")
try:
    import plugins.ego.super_memory as _sm
    _sm.DB_PATH = os.path.join(_SANDBOX, "super_memory.db")
    if hasattr(_sm, "_memory") and hasattr(_sm._memory, "_init_db"):
        _sm._memory._init_db()
    import plugins.ego.chat_style as _cs
    _cs.DB_PATH = os.path.join(_SANDBOX, "chat_style.db")
    if hasattr(_cs, "_learner") and hasattr(_cs._learner, "_init_db"):
        _cs._learner._init_db()
except Exception as _e:
    print(f"[warn] 沙箱重定向不完整: {_e}")


def _short(o, n=160):
    if isinstance(o, dict):
        return json.dumps(o, ensure_ascii=False)[:n]
    return str(o)[:n]


# ── 0. 依赖 ─────────────────────────────────────────────
p("=" * 68)
p("插件接管自检")
p("=" * 68)
p(f"解释器: {sys.executable}")
p()

from plugins.plugin_manager import init_plugin_manager  # noqa: E402

pm = init_plugin_manager()
n = pm.load_all()
plugins = pm.list_plugins()
p(f"load_all() → 成功 {n} / 共发现 {len(plugins)} 个")
loaded_bad = [x for x in plugins if not x.get("_loaded") or x.get("_error")]
if loaded_bad:
    p(f"⚠ 加载有问题的: {[(x['name'], x.get('_error')) for x in loaded_bad]}")
p()

# ── 1. 每个插件的实际状态 ────────────────────────────────
p("─" * 68)
p("一、插件状态（开着 ≠ 起作用）")
p("─" * 68)
p(f"{'插件':<20} {'启用':<6} {'钩子':<28} {'健康'}")
for x in plugins:
    hooks = ",".join(x.get("_hooks") or []) or "-"
    api = x.get("_api_status")
    health = "-"
    if api:
        if api.get("ok"):
            health = "OK"
        else:
            # detail 可能为空（插件只填了 status），两级兜底，别印 "None"
            health = str(api.get("status") or "未就绪")
            d = api.get("detail")
            if d:
                health += f":{str(d)[:24]}"
    p(f"{x['name']:<20} {str(bool(x.get('enabled'))):<6} {hooks[:27]:<28} {health}")
p()

# ── 2. before_reply 实际派发 ────────────────────────────
p("─" * 68)
p("二、before_reply 实测派发")
p("─" * 68)
try:
    res = pm.execute_hooks("before_reply", message=TEST_MSG,
                           group_id=TEST_GID, context={"sender": "12345"})
except Exception as e:
    res = []
    p(f"派发抛异常: {e}")
p(f"返回 {len(res)} 条（返回 None 的插件不出现，属正常）")
for name, r in res or []:
    act = r.get("action") if isinstance(r, dict) else "?"
    body = r.get("content") or r.get("reason") or (r.get("prompt_blocks") and "|".join(r["prompt_blocks"]))
    p(f"  · {name:<20} action={act}")
    p(f"    {_short(body, 200)}")
p()

# ── 3. EGO 五个子模块逐个探 ─────────────────────────────
p("─" * 68)
p("三、EGO 子模块（聚合器只报一个 ego，内部可能半瘫）")
p("─" * 68)
try:
    import plugins.ego as eg
    subs = [
        ("mood（情绪）", "_mood_before_reply", "mood_enabled"),
        ("personality（人格）", "_personality_before_reply", "personality_enabled"),
        ("memory（记忆）", "_memory_before_reply", "memory_enabled"),
        ("chat_style（说话风格）", "_style_before_reply", "style_enabled"),
        ("rhythm（节奏）", "_rhythm_before_reply", "rhythm_enabled"),
    ]
    for label, fn_name, flag in subs:
        try:
            fn = getattr(eg, fn_name)
            flag_on = eg._flag(flag)
            r = fn(message=TEST_MSG, group_id=TEST_GID,
                   context={"group_id": TEST_GID, "sender": "12345"})
            if r is None:
                verdict = "无输出（数据不足或条件不满足）"
            elif isinstance(r, dict):
                verdict = f"{r.get('action')} · {_short(r.get('content') or r.get('prompt_blocks'), 90)}"
            else:
                verdict = f"非预期类型 {type(r).__name__}"
            p(f"  {label:<22} 开关={str(flag_on):<6} → {verdict}")
        except Exception as e:
            p(f"  {label:<22} 异常: {type(e).__name__}: {e}")
except Exception as e:
    p(f"EGO 探针失败: {e}")
p()

# ── 4. after_reply 链式改写 ─────────────────────────────
p("─" * 68)
p("四、after_reply 链式改写（多个插件能否叠加，而不是互相顶掉）")
p("─" * 68)
try:
    cands = [(n_, f) for _pr, n_, f in pm._hook_candidates("after_reply")]
    p(f"候选顺序（priority 升序）: {[c[0] for c in cands]}")
    probe = "作为AI，我无法回答这个问题。总的来说，建议你多喝水。" + "啊" * 260
    cur = probe
    for name, fn in cands:
        try:
            r = fn(message=TEST_MSG, group_id=TEST_GID, reply=cur,
                   context={"group_id": TEST_GID, "sender": "12345"})
        except Exception as e:
            p(f"  · {name:<20} 异常: {e}")
            continue
        if isinstance(r, dict) and r.get("action") == "override":
            nxt = r.get("reply", "")
            p(f"  · {name:<20} override  {len(cur)} → {len(nxt)} 字")
            cur = nxt
        else:
            p(f"  · {name:<20} 无改写")
    p(f"  最终: {cur[:100]}")
    p(f"  违规词残留: 作为AI={'作为AI' in cur} 总的来说={'总的来说' in cur} 建议你={'建议你' in cur}")
except Exception as e:
    p(f"after_reply 探针失败: {e}")
p()

# ── 5. 数据量（"功能在，但没数据"是常态）────────────────
p("─" * 68)
p("五、数据存量（很多「插件没用」其实是数据为 0）")
p("─" * 68)
def _cnt(db, sql):
    try:
        import sqlite3
        c = sqlite3.connect(db)
        v = c.execute(sql).fetchone()[0]
        c.close()
        return v
    except Exception as e:
        return f"ERR {str(e)[:30]}"

smdb = os.path.join(APP, "plugins", "ego", "super_memory", "data", "super_memory.db")
csdb = os.path.join(APP, "plugins", "ego", "chat_style", "data", "chat_style.db")
p(f"记忆  messages(全部)      {_cnt(smdb, 'select count(*) from messages')}")
_real_sql = "select count(*) from messages where scope_id not like '\\_%' escape '\\'"
p(f"记忆  messages(真实群)    {_cnt(smdb, _real_sql)}")
p(f"记忆  facts               {_cnt(smdb, 'select count(*) from facts')}")
p(f"记忆  affection           {_cnt(smdb, 'select count(*) from affection')}")
p(f"风格  style_samples       {_cnt(csdb, 'select count(*) from style_samples')}")
p(f"风格  style_features      {_cnt(csdb, 'select count(*) from style_features')}")

ex_dir = os.path.join(APP, "plugins", "personality", "examples")
try:
    n_ex = sum(len(json.load(open(os.path.join(ex_dir, f), encoding="utf-8")))
               for f in os.listdir(ex_dir) if f.endswith(".json"))
except Exception:
    n_ex = 0
p(f"人格  few-shot 示例       {n_ex}")

prohib = os.path.join(APP, "plugins", "personality", "prohibited_patterns.txt")
try:
    n_pr = len([l for l in open(prohib, encoding="utf-8") if l.strip()])
except Exception:
    n_pr = 0
p(f"人格  违禁句式           {n_pr}")

sd = os.path.join(APP, "slang_dict.json")
try:
    n_sl = len(json.load(open(sd, encoding="utf-8")).get("entries", {}))
except Exception:
    n_sl = 0
p(f"黑话  slang_dict 词条     {n_sl}（手动学习，默认空是设计如此）")
p()

txt = "\n".join(out)
dst = os.path.join(APP, "_ref", "plan", "_diag_plugins.txt")
os.makedirs(os.path.dirname(dst), exist_ok=True)
with open(dst, "w", encoding="utf-8") as f:
    f.write(txt)
print(txt)
print(f"\n[已写入] {dst}")