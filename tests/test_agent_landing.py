# -*- coding: utf-8 -*-
"""tests/test_agent_landing.py —— Agent 落地契约（本体记忆 + HTTP 路由 + 前端接线）。

这一套测试回答的是"**功能真的接上了没有**"，不是"函数能不能跑"：
  1. 本体记忆桥 agent_memory：降级不抛、schema 合法、工具名进得了 schema。
  2. bot_core 的 /api/agent/* 路由：端点清单齐全、SSE 分支在 404 之前、群内 session 分桶。
  3. webui_server 的代理：POST 代理 + SSE 透传 + GET 只读端点。
  4. 前端 webui_artistic_v3.html：侧栏项 / 标题 / pageMounts / mountAgent 各恰好一次。

运行：python tests/test_agent_landing.py
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from harness import agent_memory as am

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {extra}")


def _read(rel):
    p = os.path.join(ROOT, rel)
    with io.open(p, "r", encoding="utf-8-sig") as f:
        return f.read()


# ---------------------------------------------------------------- 1. 本体记忆桥
def test_memory_degrade_no_throw():
    """任何依赖缺失时，agent_memory 都必须安静降级，绝不向主循环抛异常。"""
    am.configure(logger=None, enabled=True, expose_tools=True)
    for fn, args in ((am.is_available, ()), (am.status, ()),
                     (am.system_snapshot, ()), (am.prefetch, ("测试查询",)),
                     (am.sync_turn, ("u", "a")), (am.probe, ())):
        try:
            r = fn(*args)
            check(f"记忆: {fn.__name__} 不抛", True)
            check(f"记忆: {fn.__name__} 返回类型合理",
                  isinstance(r, (bool, dict, str)), type(r).__name__)
        except Exception as e:  # noqa: BLE001
            check(f"记忆: {fn.__name__} 不抛", False, repr(e))

    # 关闭开关后，可用性必须为 False 且状态里有原因
    am.configure(enabled=False)
    check("记忆: 关闭开关后不可用", am.is_available(force=True) is False)
    st = am.status()
    check("记忆: 关闭时 status 带 reason", isinstance(st, dict) and "reason" in st, st)
    check("记忆: 关闭时 snapshot 为空串", am.system_snapshot() == "")
    check("记忆: 关闭时 prefetch 为空串", am.prefetch("x") == "")
    am.configure(enabled=True)


def test_memory_tool_schemas():
    """记忆工具的 schema 必须合法：OpenAI function-calling 形状 + 名字唯一。"""
    schemas = am.memory_tool_schemas()
    check("记忆: schema 是列表", isinstance(schemas, list), type(schemas).__name__)
    check("记忆: 恰好 2 个工具", len(schemas) == 2, len(schemas))
    names = []
    for s in schemas:
        ok_shape = (isinstance(s, dict) and s.get("type") == "function"
                    and isinstance(s.get("function"), dict))
        check("记忆: schema 形状合法", ok_shape, s)
        if not ok_shape:
            continue
        f = s["function"]
        names.append(f.get("name"))
        check("记忆: 有 name/description", bool(f.get("name")) and bool(f.get("description")), f)
        params = f.get("parameters") or {}
        check("记忆: parameters 是 object", params.get("type") == "object", params)
        check("记忆: parameters.properties 是 dict",
              isinstance(params.get("properties"), dict), params)
        check("记忆: required 是列表", isinstance(params.get("required"), list), params)
    check("记忆: 工具名唯一", len(names) == len(set(names)), names)
    check("记忆: 名字带 sandglass 前缀",
          all(n and n.startswith("sandglass_") for n in names), names)
    # 函数实现与 schema 一一对应
    fns = am.memory_tools()
    check("记忆: 实现是 dict", isinstance(fns, dict), type(fns).__name__)
    check("记忆: 实现与 schema 同名集合", set(fns.keys()) == set(names),
          (sorted(fns.keys()), sorted(names)))


def test_memory_tool_call_degrades():
    """工具被调用时即使记忆不可用也要返回可读文本（不抛、不返回 None）。"""
    fns = am.memory_tools()
    for name, fn in fns.items():
        try:
            out = fn({"query": "测试", "limit": 3})
            check(f"记忆: {name} 调用返回字符串", isinstance(out, str), type(out).__name__)
        except Exception as e:  # noqa: BLE001
            check(f"记忆: {name} 调用不抛", False, repr(e))
    # 空参 / 非法参也不能炸
    try:
        am.memory_tools()["sandglass_search"]({})
        check("记忆: 搜索空参不抛", True)
    except Exception as e:  # noqa: BLE001
        check("记忆: 搜索空参不抛", False, repr(e))
    try:
        am.memory_tools()["sandglass_recent"]({"n": "不是数字"})
        check("记忆: recent 非法参不抛", True)
    except Exception as e:  # noqa: BLE001
        check("记忆: recent 非法参不抛", False, repr(e))


def test_memory_wired_into_service():
    """记忆工具必须真的能进 agent 的工具表（否则等于没接）。"""
    from harness import agent_service
    check("记忆: agent_service 暴露 memory_status",
          callable(getattr(agent_service, "memory_status", None)))
    st = agent_service.memory_status()
    check("记忆: memory_status 返回 dict", isinstance(st, dict), type(st).__name__)
    check("记忆: memory_status 带 available 字段", "available" in st, st)


def test_project_knowledge_injected():
    """★ Agent 必须"懂 EggBloom 这个项目"，而不只是通用 agent。

    回答"插件怎么写""配置填哪儿"这类问题时，模型要知道：
      · 插件有两种形态、新插件用 ABI v2、不许 import 主程序
      · 配置是多层回退的、写程序目录
      · 文档在哪、该用什么工具查
      · 改源码不生效是因为漏了"同步 + 重启后端"
    这些不能靠模型常识猜 —— 必须显式注入。同时又不能常驻（每轮都付 token），
    所以按话题**按需注入**。
    """
    import tempfile
    from harness import agent_service as svc, agent_project as ap

    st = ap.stats()
    check("项目: 模块可用", st["total_chars"] > 1000, st["total_chars"])
    check("项目: 常驻手册精简（< 4K 字符）", st["total_chars"] < 4000, st["total_chars"])

    # ── 分类正确性 ──
    should = ["eggbloom 的插件怎么写", "配置怎么填", "harness 是什么",
              "bot_core 在哪", "这个报错什么意思", "webui 怎么改",
              "真插件的 manifest 长什么样", "改完源码为什么不生效"]
    shouldnt = ["你今天心情怎么样", "哈哈哈", "在吗", "今天天气真好", "晚安", ""]
    for q in should:
        check(f"项目: 识别「{q}」", ap.is_project_question(q) is True, q)
    for q in shouldnt:
        check(f"项目: 不误判「{q}」", ap.is_project_question(q) is False, q)

    # ── 注入行为 ──
    svc.configure(static_prompt_fn=lambda is_group: "BASE", logger=lambda m: None,
                  memory=False)
    p_proj = svc.build_system_prompt(is_group=False, project_hint="eggbloom插件怎么写")
    p_chat = svc.build_system_prompt(is_group=False, project_hint="你在干嘛")
    for key in ("EggBloom 项目速查", "项目文档索引", "EggBloom 铁律", "工程模式"):
        check(f"项目: 项目问题含「{key}」", key in p_proj, key)
        check(f"项目: 闲聊不含「{key}」", key not in p_chat, key)
    check("项目: 闲聊零成本（明显更短）", len(p_chat) < len(p_proj) * 0.5,
          f"{len(p_chat)} vs {len(p_proj)}")

    # ── 关键知识必须在场（答错会真出事的几条）──
    must_have = [
        ("PLUGIN_SPEC", "真插件标识"), ("PLUGIN_MANIFEST", "manifest"),
        ("ctx.get_config", "插件读配置的唯一姿势"),
        ("不 import 主程序", "插件边界"),
        ("凭据管理器", "配置回退链"),
        ("重启后端", "生效第二步"),
        ("docs/PLUGIN_DEV.md", "插件文档路径"),
        ("docs/CONFIG_PRIORITY.md", "配置文档路径"),
        (":3005", "bot_core 端口"), (":8080", "webui 端口"),
        ("webui_artistic_v3.html", "前端单文件"),
        ("D:\\git\\EggBloom", "源码库路径"),
    ]
    for token, why in must_have:
        check(f"项目: 含知识「{why}」", token in ap.project_block("插件", force=True), token)

    # ── force + 截断安全 ──
    check("项目: force 无条件注入", len(ap.project_block("哈哈哈", force=True)) > 1000)
    tiny = ap.project_block("插件", force=True, max_chars=500)
    check("项目: 极端截断仍保留铁律", "EggBloom 铁律" in tiny, len(tiny))


def test_project_hint_wired_into_build():
    """build_system_prompt / run_chat 必须真的把本轮输入传下去。"""
    import ast
    src = _read("harness/agent_service.py")
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    bsp = fns.get("build_system_prompt")
    check("项目: build_system_prompt 有 project_hint 参数",
          bsp is not None and any(a.arg == "project_hint" for a in bsp.args.args),
          [a.arg for a in bsp.args.args] if bsp else None)
    rc = fns.get("run_chat")
    check("项目: run_chat 存在", rc is not None)
    if rc:
        seg = ast.get_source_segment(src, rc) or ""
        check("项目: run_chat 传 project_hint", "project_hint=" in seg, "未传")


def test_history_records_user_words_not_injections():
    """★ 历史/回放里必须是**用户原话**，不能混进机器注入的前缀。

    回归的真实 bug：run_chat 为了保前缀缓存，会把"时效指令"和"本体记忆
    prefetch"拼到 user_input 前面一起发给模型。但这些脚手架被一并写进了
    session_log → 历史与回放里用户那句话前面挂着一串
    「【当前状态】## 当前 偏移: ...」。用户看到自己的话被改写，回放也不再
    是当时真实发生的事。
    修法：run_agent 增加 display_input，模型收完整版，日志记原话。
    """
    import tempfile
    from harness import agent_service as svc, session_log as slog, agent_loop as al

    import ast
    src = _read("harness/agent_loop.py")
    tree = ast.parse(src)
    fn = None
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "run_agent":
            fn = n
            break
    check("历史: run_agent 接受 display_input", fn is not None and
          any(a.arg == "display_input" for a in fn.args.kwonlyargs),
          [a.arg for a in fn.args.kwonlyargs] if fn else None)

    # 端到端：注入前缀不应污染历史
    td = tempfile.mkdtemp()
    _orig_dir = slog.SESSION_LOG_DIR
    _orig_spill = al._spill.SPILL_DIR
    try:
        slog.SESSION_LOG_DIR = td
        al._spill.SPILL_DIR = os.path.join(td, "spill")
        slog.reset_cache()
        svc.configure(
            chat_fn=lambda m, t=None, max_tokens=None: ("答案", [], {}),
            tool_functions={"t": lambda a: "r"},
            static_prompt_fn=lambda is_group: "SYS",
            is_authorized_fn=lambda s: True,
            logger=lambda m: None,
        )
        svc.run_chat(user_input="用户的原话", session_id="hist-1")
        hist = svc.history_of("hist-1")
        users = [h["content"] for h in hist if h.get("role") == "user"]
        check("历史: 有 user 轮", len(users) >= 1, users)
        if users:
            check("历史: user 轮是原话（无注入前缀）",
                  users[0] == "用户的原话", repr(users[0][:120]))
        tr = svc.trace_of("hist-1")
        ev_user = [e for e in tr if e.get("type") == al.EV_USER]
        check("历史: 轨迹里的 user 事件也是原话",
              bool(ev_user) and ev_user[0].get("payload", {}).get("content") == "用户的原话",
              ev_user[:1])
    finally:
        slog.SESSION_LOG_DIR = _orig_dir
        al._spill.SPILL_DIR = _orig_spill
        svc._SESSIONS.clear()
        slog.reset_cache()


def test_memory_transient_not_cached():
    """★ "正在加载"不能被当成"不可用"缓存住。

    回归的真实 bug：sandglass 的后台加载线程约 0.5s 才完成，而启动瞬间的
    一次探测会把 False 缓存 1.5s。结果是——打开 Agent 页后的头几轮对话里，
    记忆检索工具一律返回"本体记忆当前不可用"，而记忆其实马上就好了。
    修法：探测结果区分 transient（还在加载 → 不缓存）与稳定失败。
    """
    check("记忆: 探测结果带 transient 字段", "transient" in am._PROBE, am._PROBE)
    # 所有取数路径都必须用 _ready_bridge（会等加载），不能直接用 _bridge
    src = _read("harness/agent_memory.py")
    import ast
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    check("记忆: 存在 _ready_bridge 助手", "_ready_bridge" in fns, list(fns))
    if "_ready_bridge" in fns:
        body = ast.get_source_segment(src, fns["_ready_bridge"]) or ""
        check("记忆: _ready_bridge 有等待逻辑", "sleep" in body and "deadline" in body)
        check("记忆: 等待是有界的", "timeout" in body)

    # 四个取数路径必须都调用 _ready_bridge
    for fn in ("system_snapshot", "prefetch", "sync_turn"):
        f = fns.get(fn)
        if not f:
            check(f"记忆: {fn} 存在", False)
            continue
        seg = ast.get_source_segment(src, f) or ""
        check(f"记忆: {fn} 用 _ready_bridge", "_ready_bridge()" in seg,
              "_bridge()" if "_bridge()" in seg else "none")
    # 工具实现也要用
    for fn in ("_tool_search", "_tool_recent"):
        f = fns.get(fn)
        if not f:
            check(f"记忆: {fn} 存在", False)
            continue
        seg = ast.get_source_segment(src, f) or ""
        check(f"记忆: {fn} 用 _ready_bridge", "_ready_bridge()" in seg,
              "_bridge()" if "_bridge()" in seg else "none")


# ---------------------------------------------------------------- 2. bot_core 路由
AGENT_ENDPOINTS = ["chat", "abort", "history", "sessions", "rerun",
                   "delete", "status", "tools", "memory"]


def test_bot_core_routes_present():
    src = _read("bot_core.py")
    for ep in AGENT_ENDPOINTS:
        check(f"路由: bot_core 有 /api/agent/{ep}",
              f'/api/agent/{ep}"' in src, ep)
    check("路由: bot_core 有 SSE /api/agent/stream",
          "/api/agent/stream" in src)


def test_bot_core_sse_before_404():
    """SSE 分支必须在 404 兜底之前 —— 否则流式请求全被打成 404。"""
    src = _read("bot_core.py")
    i_sse = src.index('if self.path.startswith("/api/agent/stream")')
    # do_GET 里的 404 兜底是 send_error/send_response(404) 出现在 SSE 之后
    tail = src[i_sse:]
    m = re.search(r"self\.send_response\(404\)|self\.send_error\(404", tail)
    check("路由: SSE 之后才出现 404 兜底", m is not None, "未找到 404 兜底")
    if m:
        # 404 兜底与 SSE 之间不应再有"结束 SSElse 之外"的早期 404
        head = src[:i_sse]
        m0 = None
        for mm in re.finditer(r"self\.send_response\(404\)|self\.send_error\(404", head):
            m0 = mm
        check("路由: SSE 之前没有 404 兜底（除 POST 分支）",
              m0 is None or "do_POST" not in head[m0.start() - 4000:m0.start()],
              m0.start() if m0 else None)


def test_bot_core_group_session_bucketing():
    """群内 agent 会话必须按群 + 天分桶，保证同群同天连续。"""
    src = _read("bot_core.py")
    check("路由: 群内 session 按天分桶",
          "grp-{group_id}-{_day}" in src or 'grp-{group_id}-' in src,
          "未找到按天分桶")
    check("路由: 群内上下文带发起人",
          "发起人" in src, "未找到发起人上下文")


def test_bot_core_configure_wired():
    """_agent.configure 必须把 tool_picker / memory 真的传进去。"""
    src = _read("bot_core.py")
    check("路由: configure 传 tool_picker", "tool_picker=" in src)
    check("路由: configure 传 memory", re.search(r"\bmemory=", src) is not None)
    check("路由: 启动时探测本体记忆", "agent_memory" in src and "probe()" in src)


def test_bot_core_get_post_parity():
    """★ GET 与 POST 必须共用同一套 agent 路由。

    回归的真实 bug：read 端点（status/tools/memory/sessions）只在 do_POST 里
    实现，而 webui 的 GET 读取走 do_GET → 前端状态灯永远"检测中"，
    `/api/agent/status` 拿到 404。修法是抽出 `_agent_route` 两边共用。
    """
    import ast
    src = _read("bot_core.py")
    tree = ast.parse(src)

    handler = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "start_event_server":
            for sub in ast.walk(node):
                if isinstance(sub, ast.ClassDef) and sub.name == "EventHandler":
                    handler = sub
                    break
    check("路由: 找到 EventHandler", handler is not None)
    if handler is None:
        return

    methods = {m.name: m for m in handler.body if isinstance(m, ast.FunctionDef)}
    check("路由: 有 _agent_route 方法", "_agent_route" in methods, list(methods))

    # do_GET / do_POST 都必须调用 _agent_route
    for mname in ("do_GET", "do_POST"):
        m = methods.get(mname)
        check(f"路由: {mname} 存在", m is not None)
        if m is None:
            continue
        calls = [n.func.attr for n in ast.walk(m)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        check(f"路由: {mname} 调用 _agent_route", "_agent_route" in calls, calls[:20])

    # 只读端点在 _agent_route 里齐全
    route_src = ast.get_source_segment(src, methods.get("_agent_route")) or ""
    for ep in ("/api/agent/status", "/api/agent/tools", "/api/agent/memory",
               "/api/agent/sessions", "/api/agent/history"):
        check(f"路由: _agent_route 覆盖 {ep}", ep in route_src, ep)

    # do_GET 里不能重复实现（避免两处漂移）
    get_src = ast.get_source_segment(src, methods.get("do_GET")) or ""
    check("路由: do_GET 未重复实现 status 分支",
          '"/api/agent/status"' not in get_src, "do_GET 里出现重复实现")


# ---------------------------------------------------------------- 3. webui 代理
def test_webui_proxy_present():
    src = _read("webui/webui_server.py")
    check("代理: 有 POST 代理方法", "_proxy_agent_post" in src)
    check("代理: 有 SSE 透传方法", "_proxy_agent_stream" in src)
    check("代理: do_POST 分派 /api/agent/", 'path.startswith("/api/agent/")' in src)
    check("代理: do_GET 分派 SSE", 'path == "/api/agent/stream"' in src)
    for ep in ("status", "sessions", "tools", "memory"):
        check(f"代理: GET 只读端点 /api/agent/{ep}",
              f'"/api/agent/{ep}"' in src, ep)
    check("代理: 落库前先探活", "_is_bot_core_alive" in src)
    check("代理: 用到 urllib.error", "urllib.error" in src or "URLError" in src)


def test_webui_proxy_streaming_not_buffered():
    """SSE 透传必须是分块 read + flush，不能把整段吞进内存再写。"""
    src = _read("webui/webui_server.py")
    i = src.index("_proxy_agent_stream")
    body = src[i:i + 2600]
    check("代理: SSE 循环读取", "read(2048)" in body or "read(" in body, "未见分块读")
    check("代理: SSE 边读边 flush", "flush()" in body, "未见 flush")
    check("代理: SSE 声明 ndjson", "ndjson" in body, "未见 content-type")


# ---------------------------------------------------------------- 4. 前端接线
def test_frontend_wiring():
    html = _read("webui/webui_artistic_v3.html")
    check("前端: 侧栏有 agent 导航项",
          html.count('data-page="agent"') == 1, html.count('data-page="agent"'))
    check("前端: pageTitles 注册 agent",
          re.search(r"agent:\s*\[\s*'Agent'", html) is not None)
    check("前端: pageMounts 注册 mountAgent",
          re.search(r"agent:\s*mountAgent", html) is not None)
    check("前端: mountAgent 定义恰好一次",
          html.count("function mountAgent(") == 1, html.count("function mountAgent("))
    check("前端: 有 ag-* 样式系统", ".ag-shell" in html and ".ag-stream" in html)


def test_frontend_endpoints_used():
    html = _read("webui/webui_artistic_v3.html")
    for ep in ("/agent/chat", "/agent/status", "/agent/sessions", "/agent/memory",
               "/agent/stream"):
        check(f"前端: 调用 {ep}", ep in html, ep)


def test_frontend_no_halfbaked():
    html = _read("webui/webui_artistic_v3.html")
    for bad in ("TODO", "FIXME", "XXX", "待补充", "占位符"):
        # TODO 出现在注释里也算半成品，直接报
        check(f"前端: 无 {bad} 残留", bad not in html, bad)
    check("前端: 无 console.log 调试残留",
          "console.log(" not in html, html.count("console.log("))


def test_frontend_stream_lifecycle():
    html = _read("webui/webui_artistic_v3.html")
    check("前端: 有 AbortController（可中止）", "AbortController" in html)
    check("前端: 有 ReadableStream 解析", "ReadableStream" in html
          or "getReader(" in html)
    check("前端: 离开页面断开流", "MutationObserver" in html)
    check("前端: 有关键盘交互", "Shift+Enter" in html or "shiftKey" in html)


def main():
    for fn in (test_memory_degrade_no_throw, test_memory_tool_schemas,
               test_memory_tool_call_degrades, test_memory_wired_into_service,
               test_memory_transient_not_cached,
               test_history_records_user_words_not_injections,
               test_project_knowledge_injected, test_project_hint_wired_into_build,
               test_bot_core_routes_present, test_bot_core_sse_before_404,
               test_bot_core_group_session_bucketing, test_bot_core_configure_wired,
               test_bot_core_get_post_parity,
               test_webui_proxy_present, test_webui_proxy_streaming_not_buffered,
               test_frontend_wiring, test_frontend_endpoints_used,
               test_frontend_no_halfbaked, test_frontend_stream_lifecycle):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            global FAIL
            FAIL += 1
            FAILURES.append(f"{fn.__name__} 抛异常: {e}\n{traceback.format_exc()}")
    print(f"\n{'='*60}")
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("-" * 60)
        for f in FAILURES:
            print("  ✗ " + f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
