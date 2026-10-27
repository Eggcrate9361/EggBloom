# -*- coding: utf-8 -*-
"""群名改动：不能「没改好却说成功」（2026-10-27 真机）

## 真机

    20:33:35  → group_admin: action=set_group_name (第1轮) → ✅ 群名改了
    20:34:31  群[829854136] 文本: '额，把群名改回去'
    20:34:42  [观察] 暂不插话，25s 后再看一眼 · …bot 刚插过话且没有改群名权限…
    20:37:54  → group_admin: action=set_group_name (第1轮) → ✅ 群名改了

用户要求「高浓度神金**矿区**」，实际群名成了「高浓度神金**矿产区**」，
而机器人两次都报「✅ 群名改了」。

## 三层根因

1. **成功判定只看 retcode==0**（`bot_core.py:_call`）
   —— OneBot 返回 0 只代表请求被接受，不代表群名真改成了那个值
2. **value 是模型自己填的参数**，模型顺手润色（多一个"产"字）
3. **「改回去」没有目标值**，且这种请求会被决策器 observe 掉 → 整批结束

## 契约

1. 改群名前必须记住原名，「改回去」才有目标
2. 「改回去」这类指代要能还原成原名；没有记录时明说不知道
3. 执行前：新群名必须出自用户原话（拦住模型润色）
4. 执行后：回读校验，不一致就说「没改成，现在还叫 X」
5. 「把群名改成 X」走正则路径 —— 不经模型填参数、不经决策器

运行：python tests/test_group_name_change.py
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_FAIL = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _FAIL.append(name)


# ── 桩：把 api_call 换掉，不碰真群 ────────────────────────────────

class _FakeApi:
    """记录调用，返回可编排的结果。

    必须是**类**而不是实例 —— 实例上挂 `__call__` 属性不生效
    （Python 走的是类型查找），那样测出来的"回读校验"是假的通过。
    """

    def __init__(self):
        self.calls = []
        self.name = "旧群名"
        self.set_result = {"retcode": 0}
        #: set 成功但实际不生效（模拟 QQ 侧静默拒绝）
        self.set_is_noop = False
        #: get_group_info 直接失败
        self.read_fails = False

    def __call__(self, endpoint, data=None, timeout=15):
        self.calls.append((endpoint, dict(data or {})))
        if endpoint == "get_group_info":
            if self.read_fails:
                return {"retcode": -1}
            return {"retcode": 0, "data": {"group_id": (data or {}).get("group_id"),
                                          "group_name": self.name}}
        if endpoint == "set_group_name":
            if self.set_result.get("retcode") == 0 and not self.set_is_noop:
                self.name = (data or {}).get("group_name", "")
            return self.set_result
        return {"retcode": 0}


def _install(monkey_name="高浓度神金矿区"):
    """装桩并返回 (bot_core, fake_api)。"""
    import bot_core as bc
    fake = _FakeApi()
    bc.api_call = fake
    # 权限：把发话人当成主人
    bc._get_admin_qqs = lambda: {bc.BOT_QQ, "3150007406"}
    bc._GROUP_NAME_HISTORY.clear()
    return bc, fake


# ── ① 原名记忆 + 「改回去」────────────────────────────────────────

def test_remembers_original_name():
    bc, fake = _install()
    fake.name = "原群名"
    ok, msg = bc._do_group_admin("set_group_name", value="新群名",
                                 sender="3150007406", group_id="829854136",
                                 user_msg="把群名改成新群名")
    check("改名为成功", ok, msg)
    check("记住了原名", bc._last_group_name("829854136") == "原群名",
          bc._last_group_name("829854136"))

    # 「改回去」应该还原成原名
    fake.name = "新群名"
    ok2, msg2 = bc._do_group_admin("set_group_name", value="改回去",
                                   sender="3150007406", group_id="829854136",
                                   user_msg="把群名改回去")
    check("「改回去」执行成功", ok2, msg2)
    check("「改回去」还原成原名", fake.name == "原群名", fake.name)


def test_history_is_list_semantics():
    """★ 「改回去」= 改成**上一个**群名（列表[1]），不是"我改之前那个"。

    真机：bot 把「高浓度神金矿区」改成了「高浓度神金矿产区」（模型多一个字），
    然后**用户自己**改回了「高浓度神金矿区」。
    这时「原来的名字」= 高浓度神金矿产区 —— 正是列表[1]。
    """
    bc, fake = _install()
    # 记忆是「读到就插到头」→ 顺序 = 读到的先后（倒序存）
    # 真实经过：bot 先改成了「高浓度神金矿产区」，用户又自己改回「高浓度神金矿区」
    bc._remember_group_name("829854136", "高浓度神金矿产区")   # 先读到的
    bc._remember_group_name("829854136", "高浓度神金矿区")     # 后读到的 = [0] 当前
    check("列表[1] 是上一个群名",
          bc._last_group_name("829854136") == "高浓度神金矿产区",
          bc._last_group_name("829854136"))

    # 「改回去」应还原成「高浓度神金矿产区」
    fake.name = "高浓度神金矿区"
    ok, msg = bc._do_group_admin("set_group_name", value="改回去",
                                 sender="3150007406", group_id="829854136",
                                 user_msg="把群名改回去")
    check("「改回去」执行成功", ok, msg)
    check("还原成上一个群名", fake.name == "高浓度神金矿产区", fake.name)


def test_history_persists():
    """★ 群名历史要落盘 —— 内存版重启就失忆。"""
    bc, fake = _install()
    bc._remember_group_name("829854136", "名字甲")
    bc._remember_group_name("829854136", "名字乙")
    import os
    check("state 文件写出来了", os.path.isfile(bc._GROUP_NAME_STATE),
          bc._GROUP_NAME_STATE)
    # 重新加载能恢复
    bc._GROUP_NAME_HISTORY.clear()
    bc._load_group_names()
    check("重载后历史还在",
          bc._last_group_name("829854136") == "名字甲",
          bc._last_group_name("829854136"))


def test_backref_without_record():
    """没有记录时要说清楚不知道，别瞎改。"""
    bc, fake = _install()
    fake.name = "当前群名"
    ok, msg = bc._do_group_admin("set_group_name", value="改回去",
                                 sender="3150007406", group_id="829854136",
                                 user_msg="把群名改回去")
    check("没记录 → 失败", not ok, msg)
    check("说了不知道原来叫什么", "原来叫什么" in msg or "不记得" in msg, msg)
    check("群名没被动过", fake.name == "当前群名", fake.name)


def test_backref_detection():
    bc, _ = _install()
    for v in ("回去", "改回去", "原来", "原来的名字", "之前那个", "旧名"):
        check(f"指代词 {v!r}", bc._is_name_backref(v))
    for v in ("高浓度神金矿区", "外莱塔尼亚", "测试群"):
        check(f"具体名 {v!r} 不是指代", not bc._is_name_backref(v))


# ── ② 执行前：拦住模型润色 ───────────────────────────────────────

def test_value_must_come_from_user_msg():
    """★ 真机那个多一个"产"字的名字，必须被拦住。"""
    bc, fake = _install()
    fake.name = "当前群名"
    ok, msg = bc._do_group_admin("set_group_name", value="高浓度神金矿产区",
                                 sender="3150007406", group_id="829854136",
                                 user_msg="把群名改成高浓度神金矿区")
    check("模型润色的名字被拦下", not ok, msg)
    check("回执说清了原话里没有", "没这么写" in msg, msg)
    check("群名没被动过", fake.name == "当前群名", fake.name)
    check("set_group_name 没被调用",
          not any(c[0] == "set_group_name" for c in fake.calls), fake.calls)


def test_value_in_user_msg_passes():
    bc, fake = _install()
    fake.name = "当前群名"
    ok, msg = bc._do_group_admin("set_group_name", value="高浓度神金矿区",
                                 sender="3150007406", group_id="829854136",
                                 user_msg="把群名改成高浓度神金矿区")
    check("原话里的名字放行", ok, msg)
    check("群名真改成了", fake.name == "高浓度神金矿区", fake.name)


# ── ③ 执行后：回读校验 ───────────────────────────────────────────

def test_readback_detects_silent_noop():
    """retcode=0 但实际没改 → 不能报成功。"""
    bc, fake = _install()
    fake.name = "还是老名字"
    fake.set_result = {"retcode": 0}
    fake.set_is_noop = True          # API 返回成功但实际没生效
    ok, msg = bc._do_group_admin("set_group_name", value="新名字",
                                 sender="3150007406", group_id="829854136",
                                 user_msg="把群名改成新名字")
    check("没真改 → 不报成功", not ok, msg)
    check("回执说了现在叫什么", "还是老名字" in msg, msg)


def test_readback_failure_not_success():
    """回读失败也不能说成功 —— 不确定就是不确定。"""
    bc, fake = _install()
    fake.read_fails = True
    ok, msg = bc._do_group_admin("set_group_name", value="新名字",
                                 sender="3150007406", group_id="829854136",
                                 user_msg="把群名改成新名字")
    check("回读失败 → 不报成功", not ok, msg)
    check("回执说了不确定", "确认" in msg or "不确定" in msg, msg)


# ── ④ 正则路径：不经模型、不经决策器 ─────────────────────────────

def test_readback_uses_no_cache():
    """★ 回读必须带 no_cache。

    实测（2026-10-27）：改名**成功**了（群里已是新名），
    但 `get_group_info` 带缓存仍返回旧名 —— 用它做回读校验会把成功误判成失败。
    """
    bc, fake = _install()
    fake.name = "当前群名"
    bc._read_group_name(829854136)
    _reads = [c for c in fake.calls if c[0] == "get_group_info"]
    check("读了群信息", bool(_reads), fake.calls)
    check("带 no_cache", _reads and _reads[0][1].get("no_cache") is True,
          _reads[0][1] if _reads else None)


def test_nl_regex_captures():
    bc, _ = _install()
    for t, want in (("把群名改成高浓度神金矿区", "高浓度神金矿区"),
                    ("群名改成外莱塔尼亚", "外莱塔尼亚"),
                    ("请把群名改成测试群", "测试群"),
                    ("群名换成X", "X")):
        m = bc._ADMIN_NAME_NL_RE.match(t)
        check(f"句式 {t!r}", bool(m) and m.group(1).strip() == want,
              m.group(1) if m else "不匹配")
    # 指代式走**另一个**模式（它没有具体名字，不能被切成「去」）
    for t in ("把群名改回去", "群名换回来", "把群名恢复原名"):
        check(f"指代句式 {t!r}", bool(bc._ADMIN_NAME_BACKREF_RE.match(t)),
              "不匹配")
    check("具体名不被当成指代",
          not bc._ADMIN_NAME_BACKREF_RE.match("把群名改成高浓度神金矿区"))


def test_nl_path_not_through_model():
    """★ 自然语言句式必须走正则路径，不经模型填参数。

    真机：模型把「高浓度神金矿区」填成「高浓度神金矿产区」。
    群名的参数就是那句话本身，正则够用。
    """
    bc, fake = _install()
    fake.name = "当前群名"
    handled = bc._handle_group_admin_command(
        "把群名改成高浓度神金矿区", {"message": []},
        "3150007406", "829854136")
    check("句式被接住", handled)
    check("群名改成了原话里的名字", fake.name == "高浓度神金矿区", fake.name)
    check("没有经过模型参数",
          any(c[0] == "set_group_name" and
              c[1].get("group_name") == "高浓度神金矿区" for c in fake.calls),
          fake.calls)


def test_nl_path_backref_works():
    """正则路径的「改回去」也要能用。"""
    bc, fake = _install()
    fake.name = "原群名"
    bc._handle_group_admin_command("把群名改成新群名", {"message": []},
                                   "3150007406", "829854136")
    fake.name = "新群名"
    handled = bc._handle_group_admin_command(
        "把群名改回去", {"message": []}, "3150007406", "829854136")
    check("「改回去」被接住", handled)
    check("还原成原群名", fake.name == "原群名", fake.name)


def main():
    print("=" * 58)
    print("群名改动：不说假成功")
    print("=" * 58)
    for fn in (test_remembers_original_name,
               test_history_is_list_semantics,
               test_history_persists,
               test_backref_without_record,
               test_backref_detection,
               test_value_must_come_from_user_msg,
               test_value_in_user_msg_passes,
               test_readback_uses_no_cache,
               test_readback_detects_silent_noop,
               test_readback_failure_not_success,
               test_nl_regex_captures,
               test_nl_path_not_through_model,
               test_nl_path_backref_works):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()