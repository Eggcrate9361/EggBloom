# 自愈 M1 / M2（可观测 + 配置自愈）

> FIXPLAN-1.5.11 §4.5。用户主线：「让 agent 强到能自己修自己」。
> 分四级：**R1 配置/内容 → R2 数据/状态 → R3 代码 → R4 环境/依赖**。
> 1.5.11 只做 M1（看得见）+ M2 的 R1/R2；R3 留给 1.6.0（沙箱 worktree + 最小 diff），
> R4 永远人工确认。

## M1：先看得见（`harness/trace_tools.py`）

四个**只读**工具，注册在 `work` 组（常驻，零副作用）：

| 工具 | 回答什么问题 |
|---|---|
| `failure_top(hours=24, n=3)` | 今天最常失败的是哪几条链路（带计数 + 样例日志行） |
| `log_search(pattern, hours, limit)` | 具体报错长什么样；不是合法正则时按字面量搜 |
| `trace_get(trace_id="")` | 某一次对话/任务的全链路；不传 id 用日志里最后一个 |
| `repairs_list(hours=168)` | 最近自动修复了什么、成没成 |

分类口径有两个坑，已经踩过并锁在测试里：

- **`[工具]` 要先于「搜索」判定** —— `search_web 失败` 是工具链没跑通，不是网断了；
- **retcode 非零也算失败** —— 协议断开日志里没有"失败"两个字，但就是没成。

人工入口（不需要起 Bot）：

```bash
python -m harness.trace_tools
# 今天最常失败的链路：
#   1. 工具调用 × 2
#      - [工具] search_web 失败: timeout trace=abc123…
```

## M2：能改回（`harness/self_heal.py`）

### R1 配置体检 + 回滚

- **白名单 15 个键**：评测开关、记忆共享、私聊记忆、图片缓存、工具轮数、两条回复长度。
  这几项一坏，表现是"它突然不记得我了/突然不说话了"，最难自查。
- **只修"明显坏了"的值**：类型不对、超出硬范围。**缺键不修**（有 `DEFAULT_CONFIG` 兜底）。
- 回滚依据是 `data/config_snapshot.json` —— 只在**体检通过**时写；坏值优先按快照改回，
  没快照才退回默认值。
- 每次自动修复写一行 `data/repairs.jsonl`：

```json
{"ts": 1793426150.3, "kind": "config", "target": "eval_max_chars",
 "action": "改回合法值", "ok": true, "detail": "eval_max_chars: 999 → 16"}
```

### R2 快照自愈

`data/config_snapshot.json` 的 JSON 断了/文件丢了 → 用当前有效配置重建，同样记一行台账。

### 安全边界（写死在白名单里，测试会查）

`api_key` / `target_qqs` / `group_ids` / `admin_qqs` **永远不在白名单**。
也就是说：它能自己把"被改坏的开关"改回来，但碰不到你的凭据、目标号、群列表。

### 怎么关

配置中心 → 功能开关 → **配置自愈**（`self_heal_enable`，默认开）。
关掉后启动**只报告不写盘**。

## 验收（FIXPLAN 原话："人为改坏一个开关 → 它发现并改回"）

```bash
# 1. 人工造病：把评测上限改成 999
#    （配置文件里 "eval_max_chars": 999）
python -m harness.self_heal            # 体检：应报"大于上限 80"
python -m harness.self_heal --fix      # 应改回 16，并写 data/repairs.jsonl
python -m harness.trace_tools          # 台账里能看到这次修复

# 2. 自动路径：直接重启 Bot，启动日志里应出现
#    [自愈] 修正了 eval_max_chars
```

机器版在 `tests/test_self_heal.py`（28 项）与 `tests/test_trace_tools.py`（24 项）。

## 还没做

| 项 | 归属 |
|---|---|
| R3 代码自修（读代码 → 最小 diff → 全量测试 → 自动回滚） | 1.6.0 的 M3 |
| R4 环境/依赖修复 | 永远人工确认 |
| 插件级自愈（某个插件挂了自动降级/重载） | 未排期 |
| live 模式评测（真调模型判"像不像它"） | 等 prompt 组装收口 |
