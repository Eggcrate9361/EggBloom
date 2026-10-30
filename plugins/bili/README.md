# bili —— B 站能力插件

> 一句话：**它是 agent 的能力供给方，不是一个"B站解析页"。**
> 页面只是把这条链路可视化出来，方便你看和试。

## 它不是什么

- 不是视频下载器（不落视频文件）
- 不是评论/弹幕发送器（只读，不发）
- 不是直播间工具（只看视频，不看直播）
- 不登录别人的账号；cookie 只存在本插件 `data/config.json`

## 它在 agent 链路里的位置

```
QQ 消息
  │
  ├─ harness.qq_agent.should_use_agent()     意图闸：查/看/听 + 视频·链接 才进
  │     └─ 闲聊不进来（中位 21 秒是底线，不能被拖成 60 秒）
  │
  ├─ plugin.bili.before_reply()              便宜地先看一眼
  │     ├─ 有链接 → read() 整包（信息+文稿+弹幕+热评）
  │     └─ 压成 config.inject_chars 字 → {"action":"inject"}
  │
  ├─ harness.agent_loop.run_agent()          模型带着 trace 决定要不要深挖
  │     └─ 需要细节 → agent_tools() 里那 6 个工具
  │
  ├─ harness.bili_api（纯能力层）             真正的抓取：四路、带 cookie、走缓存
  │
  └─ harness.reply_audit 第三类断言           "没读到"必须是真没读到：
                                           拿不到时注入文本里就写着"别编"
```

### 三层分工（缺一层都会退化）

| 层 | 谁 | 严格不许做的事 |
|---|---|---|
| **能力** | `harness/bili_api.py` | 不许有开关、不许依赖插件；拿不到就返回空数组/空串 |
| **策略** | `plugins/bili`（这里） | 不许自己写抓取逻辑；不许注入超预算；不许假装读到 |
| **编排** | `harness/qq_agent.py` | 不许在闲聊上开 agent |

## 一个链接能读到四路

| 路 | 接口 | 要不要登录 | 备注 |
|---|---|---|---|
| 信息 | `x/web-interface/view` | 否 | 标题/UP/封面/简介/分P/播放·点赞·弹幕·评论 |
| 文稿 | `x/player/wbi/v2` → `subtitle_url` | **要** | 必须走 WBI 版，旧接口给的 url 没有 auth_key，下载会 403 |
| 弹幕 | `comment.bilibili.com/{cid}.xml` | 否 | 免签名，一次 1200~3000 条；XML 必须显式按 UTF-8 解 |
| 热评 | `x/v2/reply?type=1&oid&sort=2` | 否 | `sort=2` 才是热度序 |

## 给 agent 的工具（`agent_tools()`）

| 工具 | 取什么 | 成本 |
|---|---|---|
| `bili_info` | 只读基本信息 | 1 次请求 |
| `bili_read` | 整包（信息+文稿+弹幕+热评+注入文本） | 3 次请求，最全最慢 |
| `bili_transcript` | 只取字幕稿 | 2 次请求（WBI + 字幕 JSON） |
| `bili_danmaku` | 只取弹幕（总数/高频词/样例） | 1 次请求，最稳 |
| `bili_comments` | 只取热评 | 1 次请求 |
| `bili_summary` | 稿子 → 3–5 句摘要 | 1 次 LLM |

统一入口 `agent_call(name, args)` 返回 `{ok, data, error}`，**绝不抛**。
`ok=False` 时模型应当转述 `error`，而不是自己编一个结果。

## 预算与降级契约

回复预算是**每条几十字**（F3）。所以：

- 注入的不是原稿，是 `标题 + 弹幕高频 + 前两行稿子 + 「共 N 条」`；
- 上限由 `config.inject_chars` 控制，超了从尾部截；
- 降级链是硬的：**没登录 → 没字幕 → 用弹幕梗顶 → 什么都没有就明写「别编内容」**。
  任何一环都不允许静默省略，否则模型只能猜。

## 登录

字幕轨要登录态（匿名调接口返回 200，但 `subtitles` 恒为空）。

- **扫码**：生成 `qrcode_key` → 二维码渲染成半块字符 → 弹一个 cmd 窗口 → 每 2.5s 轮询。
- **粘贴**：`SESSDATA=…`，`nav.isLogin` 校验通过才存。
- cookie 只在 `data/config.json`，不进 `bot_config.json`、不进版本库。

## 缓存

整包按 `bvid#p` 存 `data/cache/*.json`，默认 24h。面板上的「清空缓存」会全删。
弹幕量大（3000 条）时不进缓存包，只留高频词和 40 条样例。

## 测试

```bash
python tests/test_bili_api.py    # 能力层：签名/字幕 WBI/弹幕 UTF-8/归一化
python tests/test_bili.py        # 插件：开关、注入压缩、缓存、钩子、登录、工具契约
```

两条测试刻意钉住最容易退化的地方：**字幕必须走 WBI**、**弹幕必须按 UTF-8 解**、
**拿不到时不许编**。

## 写能力（1.6.0，默认关闭）

能做的：**评论 / 弹幕 / 点赞 / 投币 / 关注**。全都在插件里，主程序不掺和。

### 两道总闸

| 开关 | 默认 | 作用 |
|---|---|---|
| `write_enabled` | **false** | 总开关。关着时所有写动作直接拒绝（并留痕） |
| `write_dry_run` | **true** | 打开总开关后仍先干跑：闸门全过，但不真发 |

### 六道闸（`_write_guard`）

1. 总开关
2. 登录 + `bili_jct`（CSRF）—— 缺一个都不发
3. **熔断**：连续两次"真的打到接口但失败"就停手（被闸门挡下的不计）
4. **冷却**：两次写之间 ≥ 20 秒（可配 0 关掉）
5. **日上限**：默认 3 次/天；**同视频 24h 内只发一次**
6. **UP 白名单**：配了就只对这些 UP 发言（空 = 不限）

### 回读校验

评论发出去 `code=0` **不代表**别人看得见（影子删除、审核中、限流都会 code=0）。
所以发完会去前两页评论里找自己那条，`verified=false` 时面板会明说
"接口收下了，但没读到，可能审核中"。

### 审计

每次写动作（含干跑、被拒）都追加一行 JSON 到 `data/actions.jsonl`：
`{ts, date, at, kind, bvid, owner, text, ok, dry_run, blocked, rpid, code, error, verified}`。
面板「发言」页签直接显示最近几条。

### 常见失败码

| code | 含义 |
|---|---|
| -101 | 未登录 |
| -111 | CSRF 失败（`bili_jct` 不对） |
| -352 | 风控（触发后**别重试**，等几小时） |
| -412 | 请求被拦截 |
| 12002 / 12061 | 该视频评论已关闭 |
