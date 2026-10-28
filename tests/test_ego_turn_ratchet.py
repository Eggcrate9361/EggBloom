# -*- coding: utf-8 -*-
"""连续追问计数不能是棘轮（2026-10-28）

## 线上症状

ego 插件注入的上下文长这样：

```
当前对话状态：话题=[(/ω＼)] ，连续追问=93
```

两个问题同时出现，而且互相掩盖：

1. **连续追问=93** —— 一段对话不可能追问 93 轮；
2. **话题=[(/ω＼)]** —— 话题停在一个昵称上，永远不更新。

## 根因

```python
def _is_follow_up(self, msg):
    return any(...) or self.turn_count > 0     # ← 自锁
```

一旦追问过一次，`turn_count > 0` 就恒成立，于是**之后每一句都被判成追问**：
计数只升不降（棘轮），而且永远走不到 `else` 分支 →
`current_topic = self._extract_topic(msg)` 再也不执行。

也就是说：只要聊过一次"然后呢"，这个用户的"话题"就冻结在那一刻，
此后每一轮都在告诉模型"你们在聊那个"，即使已经换了十个话题。

## 修法

1. `_is_follow_up` 只看**这一句**（判据不能引用被它自己更新的状态）；
2. 空闲超过 `_IDLE_RESET_SEC`（30 分钟）→ 计数归零 + 话题重取；
3. 计数封顶 `_TURN_CAP`：它是给提示词的氛围数字，不是审计数据。

运行：python tests/test_ego_turn_ratchet.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import plugins.ego.personality_engine as pe  # noqa: E402
from plugins.ego.personality_engine import ConversationState  # noqa: E402

_fail = []
_uid = ["_test_ratchet_0"]


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def _fresh(turn=0, topic=None, idle=0.0):
    """干净状态；idle>0 表示"上一次对话是 idle 秒前"。"""
    _uid[0] += "x"
    st = ConversationState(_uid[0])
    st.turn_count = turn
    st.current_topic = topic
    st.last_update = time.time() - idle if idle else time.time()
    return st


def test_follow_up_is_message_only():
    """追问链必须能被普通消息打断。"""
    print("\n[1] 追问链：能建立，也能被打断（原实现只进不出）")
    st = _fresh()
    for m in ("那你呢", "那这个呢", "然后呢"):
        st.update(m, "…")
    check("连续三句追问 → turn=3", st.turn_count == 3, st.turn_count)
    st.update("讲个笑话", "…")
    check("★ 普通消息把计数清零（原实现会继续涨）",
          st.turn_count == 0, st.turn_count)
    check("★ 话题跟着换（原实现冻结在旧值）",
          st.current_topic == "讲个笑话", repr(st.current_topic))


def test_topic_keeps_updating():
    """换话题时 current_topic 必须更新 —— 这是棘轮最隐蔽的伤害。"""
    print("\n[2] 话题不再冻结")
    st = _fresh()
    st.update("那你呢", "…")          # 先建立追问链，制造老实现的死锁条件
    st.update("明日方舟今年半周年限定是谁", "…")
    check("★ 换话题后 current_topic 更新",
          st.current_topic and "明日方舟" in st.current_topic,
          repr(st.current_topic))
    check("计数回到 0", st.turn_count == 0, st.turn_count)


def test_idle_reset():
    """隔了半小时以上，上一段对话接不上了。"""
    print("\n[3] 空闲超时 → 计数与话题一起重置")
    st = _fresh(turn=5, topic="旧话题", idle=pe._IDLE_RESET_SEC + 60)
    st.update("今天好热", "…")
    check("★ 计数归零", st.turn_count == 0, st.turn_count)
    check("★ 话题重取（不是旧话题）",
          st.current_topic == "今天好热", repr(st.current_topic))

    # 反面：刚说完一句（不超时）不该重置
    st2 = _fresh(turn=5, topic="旧话题", idle=10)
    st2.update("然后呢", "…")
    check("短间隔内继续累计", st2.turn_count == 6, st2.turn_count)


def test_cap():
    """计数封顶，不能涨到 93。"""
    print("\n[4] 计数封顶")
    st = _fresh()
    for _ in range(30):
        st.update("然后呢", "…")
    check(f"★ 最多 {pe._TURN_CAP}",
          st.turn_count == pe._TURN_CAP, st.turn_count)
    check("to_prompt 不会出现三位数",
          "连续追问=30" not in st.to_prompt(), st.to_prompt())


def test_prompt_threshold():
    """单轮不写进提示词；两轮以上才写。"""
    print("\n[5] 提示词只在 >=2 时提到连续追问")
    st = _fresh()
    st.update("那你呢", "…")
    check("1 轮不写", "连续追问" not in st.to_prompt(), st.to_prompt())
    st.update("然后呢", "…")
    check("2 轮写入", "连续追问=2" in st.to_prompt(), st.to_prompt())


def test_no_self_reference():
    """源码护栏：_is_follow_up 不许再引用 turn_count（用 AST 判，避开注释误报）。"""
    print("\n[6] 源码里 _is_follow_up 不再读 self.turn_count")
    import ast
    import io
    src = io.open(os.path.join(ROOT, "plugins", "ego", "personality_engine",
                               "__init__.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_is_follow_up":
            fn = node
            break
    check("找到函数", fn is not None)
    if fn is None:
        return
    # 只看真正的代码：Attribute(attr='turn_count') 或 Name('turn_count')
    hits = [n for n in ast.walk(fn)
            if (isinstance(n, ast.Attribute) and n.attr == "turn_count")
            or (isinstance(n, ast.Name) and n.id == "turn_count")]
    check("★ 函数体内没有 turn_count 的读写（含注释不算）",
          not hits, f"命中 {len(hits)} 处")


def test_stale_state_clamped_on_load():
    """老状态文件里的 93 必须在**载入时**就夹住，不能等下一轮自愈。"""
    print("\n[7] 旧状态文件里的超大计数在载入时就被夹住")
    import json
    _uid[0] += "x"
    uid = _uid[0]
    path = os.path.join(pe.STATE_DIR, f"{uid}.json")
    os.makedirs(pe.STATE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"turn_count": 93, "current_topic": "旧昵称",
                   "last_update": time.time()}, f, ensure_ascii=False)
    try:
        st = ConversationState(uid)
        check(f"★ 载入后 <= {pe._TURN_CAP}", st.turn_count <= pe._TURN_CAP,
              st.turn_count)
        check("提示词里不会出现 93", "连续追问=93" not in st.to_prompt(),
              st.to_prompt())
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def main():
    test_follow_up_is_message_only()
    test_topic_keeps_updating()
    test_idle_reset()
    test_cap()
    test_prompt_threshold()
    test_no_self_reference()
    test_stale_state_clamped_on_load()
    print("\n" + "=" * 56)
    if _fail:
        print(f"FAILED ({len(_fail)}): " + ", ".join(_fail))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()