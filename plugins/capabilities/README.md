# capabilities —— 统一能力面板

项目里**闲置的 OneBot 扩展动作**，集中在这一个插件里手动触发、显示原始返回。

## 为什么是"一个"插件

手册 M4 明确：**不要新建一堆插件**（会让插件列表膨胀，而"干净"是用户明确要求）。
所以签名/在线状态/昵称/群签到/群待办/群相册 全部收进本面板，而不是各建一个插件。

## 收录的动作（按手册优先级）

| 组 | 动作 | 说明 |
|---|---|---|
| `profile` | `set_self_longnick` | ★★★ 改个性签名（成本极低） |
| `profile` | `set_online_status` | ★★ 在线状态（⚠️ `status` 无枚举表，需实测） |
| `profile` | `set_diy_online_status` | ★★ 自定义在线状态 |
| `profile` | `set_qq_profile` | ★ 改昵称 / 个人说明 |
| `avatar` | `set_qq_avatar` | ★ 改头像（**默认关**，风控风险） |
| `safe` | `get_group_signed_list` | ★★ 群今日打卡列表 |
| `safe` | `get_group_todo_list` | ★ 群待办列表（**取 id 用**） |
| `group` | `set_group_todo` / `complete_group_todo` / `cancel_group_todo` | ★ 群待办增/完成/取消 |
| `safe` | `get_group_album_list` / `get_group_album_media_list` | ★ 群相册列表 |
| `group` | `upload_image_to_qun_album` / `del_group_album_media` | ★ 群相册上传/删除 |

共 **14 个动作**。

## 发语音：按用户决定不收录

手册 ★★★ 列了 `send_group_ai_record`（QQ 自带 AI 音色发语音），并注明
"先实测 `get_ai_characters` 能否拿到可用音色列表 —— 拿不到就跳过"。
**用户已明确决定不用语音功能**，故本插件不收录 —— 不是"没做"，是"按决定不做"。
`status` 动作返回 `voice_skipped: true` 作为记录。

## 分级开关（默认全关）

| 组 | 含义 |
|---|---|
| `safe` | 只读（群签到 / 群待办列表 / 群相册列表） |
| `profile` | 改自己资料（签名 / 在线状态 / 昵称） |
| `group` | 改群内内容或对外可见（群待办增删 / 相册上传删除） |
| `avatar` | 改头像（**单独二次确认** —— 风控风险） |

总开关 + 四档，逐项开。改头像除了开 `avatar` 组，动作本身还要 `confirm=true`（双重保险）。

## 三条硬约束（手册 1.3 已核实事实，照做不重蹈）

1. **`del_group_album_media` 是真名**（`del_` 不是 `delete_`）。
   `delete_group_album_media` 在驱动源文件里零命中、不存在 —— 不要照抄失败的名字。

2. **群待办的 `message_id` 必须是本进程缓存过的群消息**（源文件 `groupTodoRun`）。
   所以本面板**强制**：先 `get_group_todo_list`（把返回的 `message_id` 缓存到进程内），
   再从列表里选一条做 `set/complete/cancel`。手填裸 id 必失败 —— 面板会拒绝并列出可用 id。
   注意 id 是 **int32 hash，可为负数**。

3. **`set_online_status.status` 无官方枚举表**（提取方标注"未能确认"）。
   面板如实标注，由用户实测填写；`status` 不能为 0（驱动 `f.int({nonzero:true})`）。

## 用 WebUI 管理

插件页「能力面板」：总开关 + 四组开关 → 动作表格（每行可填参、点"执行"）
→ 下方"返回结果"显示**原始 JSON**。

对应动作（`/api/plugins/action`，`plugin_id=capabilities`）：

| action | 参数 | 说明 |
|---|---|---|
| `status` | — | 开关状态 + 动作清单 + 已缓存待办 id |
| `set_config` | `enabled` / `groups` / `confirm_avatar` | 改配置（头像需 `confirm_avatar`） |
| `run` | `action` + `params` | **试运行**：执行某动作，返回原始 JSON |

## 自检

```bash
python tests/test_capabilities.py    # 48 项
```

专项：动作清单与分组 / `del_` 真名 / 语音不收录 / 分组门禁 / 头像二次确认 /
参数校验 / ★群待办必须先取 id（含乱填拒绝、负数 id）/ raw 透传 / 未注入可读错误 / 持久化。
