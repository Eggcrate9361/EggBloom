# -*- coding: utf-8 -*-
"""capabilities 面板自测（1.5 任务 4 验收）

覆盖：
1. ABI 完整性
2. 动作清单与分组（含 del_group_album_media 真名 / 无 delete_ 错误名）
3. 语音不收录（voice_skipped）
4. 分组门禁 + 头像二次确认
5. 参数校验（可读错误）：longNick 必须 str、status 非0、todo 必须先取 id、删除需 confirm
6. ★ 群待办：未取列表 → 拒绝；取列表后 → 缓存 id 并可用；乱填 id → 拒绝
7. 原始 JSON 透传
8. 未注入 api_call → 可读错误
9. 状态持久化
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


class FakeCtx:
    def __init__(self, host=None):
        self.host = host or {}
        self.logs = []

    def log(self, msg, level="info"):
        self.logs.append((level, msg))

    def api_call(self, action, params=None):
        if action not in self.host:
            return None
        r = self.host[action]
        return r(action, params) if callable(r) else r


def fresh(mod, ctx=None, all_on=True):
    mod._ctx = ctx
    mod._state = mod._fresh()
    if all_on:
        mod._state["enabled"] = True
        for g in mod.GROUPS:
            mod._state["groups"][g] = True
    mod._todo_cache.clear()
    mod._save_state()


def main():
    global PASS, FAIL
    print("=" * 62)
    print("capabilities 面板自测")
    print("=" * 62)

    import plugins.capabilities as C

    # ── 1. ABI ──
    print("\n[1] ABI 完整性")
    check("PLUGIN_SPEC == 1", getattr(C, "PLUGIN_SPEC", None) == 1)
    mf = C.PLUGIN_MANIFEST
    check("manifest.api_version == 2", int(mf.get("api_version", 0)) == 2)
    check("默认不启用", mf.get("default_enabled") is False)
    from plugins.abi import validate_manifest
    check("manifest 通过校验", validate_manifest(mf) is None, validate_manifest(mf))
    check("execute_action 存在", callable(C.execute_action))
    check("execute_action 未知 → error", "error" in C.execute_action("__x__", {}))

    # ── 2. 动作清单与分组 ──
    print("\n[2] 动作清单与分组")
    expected = {
        "set_self_longnick", "set_online_status", "set_diy_online_status",
        "set_qq_profile", "set_qq_avatar",
        "get_group_signed_list",
        "get_group_todo_list", "set_group_todo", "complete_group_todo", "cancel_group_todo",
        "get_group_album_list", "get_group_album_media_list",
        "upload_image_to_qun_album", "del_group_album_media",
    }
    check("动作集合完全一致", set(C.ALL_ACTIONS) == expected,
          f"差={set(C.ALL_ACTIONS) ^ expected}")
    check("共 14 个动作", len(C.ALL_ACTIONS) == 14, len(C.ALL_ACTIONS))
    check("★ del_group_album_media 是真名（del_）", "del_group_album_media" in C.ACTIONS)
    check("★ 没有 delete_group_album_media 错误名", "delete_group_album_media" not in C.ACTIONS)
    check("set_qq_avatar 属 avatar 组", C.ACTIONS["set_qq_avatar"][0] == "avatar")
    check("四个只读动作归 safe 组",
          C.ACTIONS["get_group_todo_list"][0] == "safe"
          and C.ACTIONS["get_group_album_list"][0] == "safe"
          and C.ACTIONS["get_group_signed_list"][0] == "safe"
          and C.ACTIONS["get_group_album_media_list"][0] == "safe")
    check("四档分组齐全", set(C.GROUPS) == {"safe", "profile", "group", "avatar"})
    check("默认四档全关", all(v is False for v in C._DEFAULT_STATE["groups"].values()))

    # ── 3. 语音不收录 ──
    print("\n[3] 语音按决定不收录")
    st = C.execute_action("status", {})
    check("status.voice_skipped == True", st.get("voice_skipped") is True)
    check("send_group_ai_record 不在动作表", "send_group_ai_record" not in C.ACTIONS)
    check("get_ai_characters 不在动作表", "get_ai_characters" not in C.ACTIONS)

    # ── 4. 门禁 ──
    print("\n[4] 分组门禁")
    ctx = FakeCtx(host={"set_self_longnick": {"status": "ok", "data": {}},
                        "get_group_signed_list": {"status": "ok", "data": []}})
    fresh(C, ctx, all_on=False)
    r = C.call("set_self_longnick", {"longNick": "hi"})
    check("总开关关 → 拒绝", r.get("ok") is False and "未启用" in r.get("error", ""), r)
    C._state["enabled"] = True
    C._save_state()
    r = C.call("set_self_longnick", {"longNick": "hi"})
    check("profile 组关 → 拒绝并点名分组", r.get("ok") is False and "profile" in r.get("error", ""), r)
    C._state["groups"]["profile"] = True
    C._save_state()
    r = C.call("set_self_longnick", {"longNick": "hi"})
    check("profile 组开 → 放行", r.get("ok") is True, r)
    r = C.call("set_qq_avatar", {"file": "x.jpg", "confirm": True})
    check("avatar 组未开 → 拒绝", r.get("ok") is False and "头像" in r.get("error", ""), r)

    # ── 5. 参数校验 ──
    print("\n[5] 参数校验")
    fresh(C, ctx)
    C._ctx.host["set_online_status"] = {"status": "ok"}
    C._ctx.host["set_diy_online_status"] = {"status": "ok"}
    C._ctx.host["set_qq_profile"] = {"status": "ok"}
    C._ctx.host["set_qq_avatar"] = {"status": "ok"}
    r = C.call("set_self_longnick", {"long_nick": 123})
    check("longNick 非字符串 → 拒绝", r.get("ok") is False and "字符串" in r.get("error", ""), r)
    r = C.call("set_self_longnick", {"longNick": "ok"})
    check("longNick 为 str → 通过", r.get("ok") is True, r)
    r = C.call("set_self_longnick", {"long_nick": "别名也可"})
    check("long_nick 别名可用", r.get("ok") is True, r)
    r = C.call("set_online_status", {"status": 0})
    check("status=0 → 拒绝", r.get("ok") is False and "0" in r.get("error", ""), r)
    r = C.call("set_online_status", {"status": 11})
    check("status=11 → 通过（带无枚举表提醒）", r.get("ok") is True and "枚举" in r.get("note", ""), r)
    r = C.call("set_qq_profile", {})
    check("set_qq_profile 空参 → 拒绝", r.get("ok") is False, r)
    r = C.call("set_qq_avatar", {"file": "x.jpg"})
    check("改头像未 confirm → 拒绝", r.get("ok") is False and "confirm" in r.get("error", ""), r)
    r = C.call("set_qq_avatar", {"file": "x.jpg", "confirm": True})
    check("改头像 confirm → 通过", r.get("ok") is True, r)
    r = C.call("get_group_album_list", {})
    check("缺 group_id → 拒绝", r.get("ok") is False and "group_id" in r.get("error", ""), r)
    r = C.call("get_group_album_list", {"group_id": "abc"})
    check("group_id 非整数 → 拒绝", r.get("ok") is False and "整数" in r.get("error", ""), r)

    # ── 6. ★ 群待办必须先取 id ──
    print("\n[6] ★ 群待办必须先取 id")
    todo_payload = {"status": "ok", "data": [
        {"message_id": 111, "text": "交作业"},
        {"message_id": -222, "text": "值日"},
    ]}
    ctx2 = FakeCtx(host={
        "get_group_todo_list": todo_payload,
        "set_group_todo": {"status": "ok"},
        "complete_group_todo": {"status": "ok"},
        "cancel_group_todo": {"status": "ok"},
    })
    fresh(C, ctx2)
    r = C.call("set_group_todo", {"group_id": 123, "message_id": 111})
    check("★ 未先取列表就操作 → 拒绝", r.get("ok") is False and "get_group_todo_list" in r.get("error", ""), r)
    r = C.call("set_group_todo", {"group_id": 123, "message_id": ""})
    check("★ 缺 message_id → 拒绝", r.get("ok") is False and "message_id" in r.get("error", ""), r)
    r = C.call("get_group_todo_list", {"group_id": 123})
    check("取列表 → 成功并缓存 id", r.get("ok") is True and r.get("cached_ids") == 2, r)
    r = C.call("set_group_todo", {"group_id": 123, "message_id": 111})
    check("★ 取列表后操作 → 通过", r.get("ok") is True, r)
    r = C.call("complete_group_todo", {"group_id": 123, "message_id": -222})
    check("★ 负数 id（int32 hash）也可用", r.get("ok") is True, r)
    r = C.call("cancel_group_todo", {"group_id": 123, "message_id": 999})
    check("★ 乱填 id → 拒绝并列出可用 id", r.get("ok") is False and "111" in r.get("error", ""), r)
    r = C.call("set_group_todo", {"group_id": 999, "message_id": 111})
    check("★ 换个群（没取过列表）→ 拒绝", r.get("ok") is False, r)

    # ── 7. 原始 JSON 透传 ──
    print("\n[7] 原始 JSON 透传")
    raw = {"status": "ok", "retcode": 0, "data": [{"album_id": "a1", "name": "日常"}]}
    ctx3 = FakeCtx(host={"get_group_album_list": raw})
    fresh(C, ctx3)
    r = C.call("get_group_album_list", {"group_id": 123})
    check("返回含原始 raw", r.get("ok") is True and r.get("raw") == raw, r)
    r2 = C.execute_action("run", {"action": "get_group_album_list", "params": {"group_id": 123}})
    check("execute_action('run') 透传 raw", r2.get("raw") == raw, r2)
    check("run 非法 JSON → 可读错误",
          "error" in C.execute_action("run", {"action": "get_group_album_list", "params": "{bad"}))

    # ── 8. 未注入 ──
    print("\n[8] 宿主未注入")
    ctx4 = FakeCtx(host={})
    fresh(C, ctx4)
    r = C.call("set_self_longnick", {"longNick": "x"})
    check("api_call 未注入 → 可读错误", r.get("ok") is False and r.get("error"), r)

    # ── 9. 持久化 ──
    print("\n[9] 持久化与 status")
    fresh(C, ctx4)
    C.execute_action("set_config", {"groups": {"profile": True, "avatar": False}})
    st = C.execute_action("status", {})
    check("set_config 后 profile 读回", st["groups"].get("profile") is True, st["groups"])
    check("avatar 仍关", st["groups"].get("avatar") is False, st["groups"])
    # 头像二次确认：不带 confirm_avatar 不允许开
    C.execute_action("set_config", {"groups": {"avatar": True}})
    st = C.execute_action("status", {})
    check("★ 未带 confirm_avatar → avatar 仍关", st["groups"].get("avatar") is False, st["groups"])
    C.execute_action("set_config", {"groups": {"avatar": True}, "confirm_avatar": True})
    st = C.execute_action("status", {})
    check("★ 带 confirm_avatar → avatar 开", st["groups"].get("avatar") is True, st["groups"])
    check("status 列出 14 个动作", len(st.get("actions") or []) == 14)
    check("status 含 groups_meta", len(st.get("groups_meta") or []) == 4)

    # 清理
    fresh(C, None, all_on=False)
    C._save_state()

    print("\n" + "=" * 62)
    print(f"结果: {PASS} passed, {FAIL} failed")
    print("=" * 62)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
