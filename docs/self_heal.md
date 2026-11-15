# 自愈 M1 / M2 / M3（可观测 + 配置自愈 + 代码自修骨架）

> FIXPLAN-1.5.11 §4.5，1.6.0 补 M3。分四级：**R1 配置/内容 → R2 数据/状态 →
> R3 代码 → R4 环境/依赖**。1.5.11 交付 M1（看得见）+ M2 的 R1/R2；
> 1.6.0 交付 M3 / R3 的沙箱 worktree + 失败归因 + patcher + 最小 diff + 熔断，
> **默认关、只做 dry-run 观察，不自动改线上代码**。R4 永远人工确认。

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

## M3 / R3：代码自修（1.6.0，`harness/self_heal_r3.py`）

### 已交付

- **沙箱 worktree + 白名单**：≤40 行、≤3 文件，白名单外一律拒；
- **失败归因**：静态表把 `failure_top()` 的 channel 映射到具体文件 + 文件内定位锚点，
  并用 `log_search()` 拿日志佐证；归因不到就**明确不修**（`skip_reason` 带原因），
  不猜、不产补丁；
- **patcher 注入**：`set_patcher(fn)` / `get_patcher()`；内置确定性 patcher 只产 1 行最小 diff，
  生成前后都能给出人可读的归因依据；
- **`run(issue_key, patcher, ...)`**：改前留痕 → 全量测试 → 绿提交 / 红删 worktree；
  **绝不 push**；2 次失败熔断 30 天，台账 `data/repairs.jsonl` 与
  `trace_tools.record_repair` 的字段已统一并兼容旧行；
- **`watch(..., dry_run=True)` 是安全默认**：只判定「该不该修」并写账本，
  不建 worktree、不提交。

### 边界（写进代码，不是约定）

- `self_heal_r3_enable` 默认 `False`，关着时一次都不跑、零成本；
- `bot_core.py` 的 30 分钟观察块**只调 `watch(dry_run=True)`**，没有把 `run()` 接进主链。
  也就是说：R3 目前是「可被调用、可验收、默认不自动执行」的能力，不是常驻自改代码；
- 模型产补丁没有作为默认 patcher 接进来——默认 patcher 是确定性的、可复现的最小 diff。
  要接模型 patcher 需显式 `set_patcher()`，并自行承担不可复现的风险；
- 自动提交只在隔离 worktree 内发生，不 push；红了的 worktree 直接删。

判据用例：`tests/test_self_heal_r3.py`（41 项）与
`tests/test_self_heal_r3_patcher.py`（51 项）。

## 还没做

| 项 | 归属 |
|---|---|
| R3 接进主链并放开自动执行（默认关 → 默认开） | 未排期；需先跑够 dry-run 观察轮次 |
| 常驻的模型 patcher（读代码 → 自由生成 diff） | 未接；现有注入点可用，但不可复现风险未评估 |
| R4 环境/依赖修复 | 永远人工确认 |
| 插件级自愈（某个插件挂了自动降级/重载） | 未排期 |
| live 模式评测（真调模型判"像不像它"） | 等 prompt 组装收口 |
