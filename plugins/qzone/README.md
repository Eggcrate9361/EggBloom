# qzone —— QQ 空间全套

把驱动现成的 **9 个 QQ 空间动作**按风险分四层封装，接上调度器与 AI，
让"发说说"变成可控、有上限、可审计的能力，而不是裸露的一组 HTTP 端点。

## 为什么需要它

驱动的 QQ 空间动作一直是现成的（`send_qzone_msg` 等 9 个），但此前项目里
**没有任何地方把它们用起来** —— 能力闲置。本插件补齐这一环。

关键设计取向：**这些动作有的不可逆（删除）、有的对外公开（发说说）、有的影响他人（拉黑）**，
所以不能做成一个"点一下就发"的裸按钮。本插件的做法是四层门禁 + 频率硬上限。

## 能力四层（默认全关，逐项开启）

| 层 | 动作 | 风险 |
|---|---|---|
| `read` | `get_qzone_msg_list`、`get_qzone_feeds` | 无 |
| `interact` | `like_qzone`、`unlike_qzone`、`comment_qzone` | 低 |
| `write` | `send_qzone_msg` | 中（发出去人人可见） |
| `manage` | `delete_qzone_msg`、`set_qzone_msg_right`、`set_qzone_ban` | 中（不可逆 / 影响他人） |

四层各自一个开关，**默认全部关闭**。要发说说，必须显式打开 `write`。

## 频率硬上限（防风控，不放宽）

只有 `send_qzone_msg` 受限，读/互动/管不受约束：

- 每天 **≤ 2 条**
- 相邻两条间隔 **≥ 4 小时**

两道都在**发之前**判定，避免"发出去了才发现超限"。拒绝时给出可读原因
（"今天已发 2 条，达到每日上限" / "距上一条仅 N 秒，需间隔 ≥4 小时"）。
发布记录落在 `plugins/state/qzone.json` 的 `publish_log`，跨天自动恢复额度。

## 内容三来源

1. **手动写** —— 插件页「试运行」里直接填 `content` 发；
2. **聊天里被叫去发** —— 群/私聊说 `发条说说 <内容>`（也认 `发说说`/`发空间`），
   带内容就直接发；不带内容就调 AI 按人设生成；
3. **AI 按人设生成** —— 调度器到点广播 `qzone.publish`，本插件生成正文并发布。

## 定时集成（调度器已就绪）

`plugins/scheduler` 到点广播事件，本插件订阅：

```python
# 在调度器里建一个任务，event = "qzone.publish"，payload 可带 hint
# 本插件收到后：
def on_event(ctx, event_name, payload):
    if event_name == "qzone.publish":
        publish(hint=payload.get("hint", ""))   # 生成 → 发布
```

示例：在「定时任务」页新建 `daily 20:00` + event `qzone.publish`，
即可每天自动发一条。**注意**：即便挂定时任务，频率硬上限依然生效 ——
若调度比上限更频繁，超出的会被拒绝（这是刻意的，防止风控）。

## 用 WebUI 管理

插件页「QQ 空间」：

- 总开关 + 四个能力层开关（点一下切换）
- 发布额度实时显示（今日已发 / 是否可发 / 若不可发的原因）
- 人设编辑 + 「试生成一条（不发布）」预览
- **试运行**：9 个动作每个一张卡，填参数 → 执行 → 显示**原始 JSON**
- 返回结果区始终展示驱动给的 raw 返回，不是"成功"两个字

对应动作（`/api/plugins/action`，`plugin_id=qzone`）：

| action | 参数 | 说明 |
|---|---|---|
| `status` | — | 开关状态 + 额度 + 发布记录 + ugc 选项 |
| `set_config` | `enabled` / `layers` / `persona` | 改配置 |
| `reset_limit` | `confirm=true` | 清空今日发布记录（调试/误操作恢复用） |
| `run` | `action` + `params` | **试运行**：直接执行某动作，返回原始 JSON |
| `generate` | `hint` | 仅生成内容，不发布 |

## 已知限制（来自驱动文档，不自行绕过）

- `get_qzone_feeds` 的「按时间游标翻页尚未实现」—— 只能按页码，`page_num` 仅首页可靠。
- `unlike_qzone` 的取消赞端点文档标注「待真机核实」，返回值里附带该提醒。

## 与驱动的参数契约

所有参数校验严格按 `docs/plan/snowluma-actions.md` 第 2 部分（源文件构造器 + `rules`，非推测）：

- `send_qzone_msg` / `set_qzone_msg_right`：`ugc_right` 必须是 `1/4/16/64/128` 之一；
  `ugc_right=16/128` 时 `target_uins` 必填（可见/不可见名单）。
- `content` 非空；`images` 支持 `file://` / `http://` / `base64://`（驱动自动上传）。
- `count` 1–50、`num` 1–100、`page_num` ≥1、`pos` ≥0。

## 自检

```bash
python tests/test_qzone.py     # 65 项：ABI / 分层门禁 / 参数校验 / ★频率上限 / 调度集成 / 聊天触发 / 持久化
```

其中频率上限专项：第 1、2 条通过 → **第 3 条被拒绝**；间隔 <4 小时被拒绝；跨天恢复。
