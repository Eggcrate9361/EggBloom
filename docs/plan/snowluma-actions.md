> ⚠️ **可信度声明**：本文由子代理从本机源文件/设计文档机械提取（2026-10-03），结论均标注来源与行号。
> 我逐条抽验过其中若干关键结论（动作总数 190、wrapper 四类、HTTP 端点=动作名、缓存命中率字段存在），均吻合。
> 引用具体数字前建议按来源行号回查。

---

# SnowLuma 动作契约（机械提取自 `config-k6rruub-.js`）

> **数据来源（唯一真源）**：`E:\SnowLuma-v1.14.10-win-x64\config-k6rruub-.js`（405,790 字节，12,403 行，SnowLuma v1.14.10 win-x64）
> **提取方式**：脚本扫描全部 `defineAction({...})` / `groupAction({...})` / `groupUserAction({...})` / `defineStreamAction({...})` 调用，用括号配对法取出完整定义体，再逐字段解析。
> **不做任何推测**：所有类型、必填、默认值、范围、中文说明均逐字取自源文件；源文件没有写的，本报告写「未提供」并在第 4 部分列出。

## 0. 提取口径说明（读表前必看）

### 0.1 参数构造器语义（源文件 `action-kit.ts`，第 3006–3382 行）

| 构造器 | 逻辑类型 | 必填 | coerce 行为 | JSON Schema |
| --- | --- | --- | --- | --- |
| `f.uint()` / `f.groupId()` / `f.userId()` / `f.memberId()` | `uint` | 是 | 数字或数字字符串 → 截断取整；`< 1` 报错 `must be >= 1` | `{type:integer,minimum:1}` |
| `f.int(opts)` | `int` | 是 | 截断取整；按 `min`/`max`/`nonZero` 校验 | `{type:integer,...}` |
| `f.messageId()` | `messageId` | 是 | 非 0 整数（**允许负数**，id 是 signed int32 hash） | `{type:integer,not:{const:0}}` |
| `f.string(opts)` | `string` | 是 | 字符串；数字/布尔会 `String()` 化；`allowEmpty:false` 拒绝 `""` | `{type:string}`，`minLength:1` 视选项 |
| `f.fileId()` / `f.file()` / `f.image()` / `f.record()` / `f.video()` | `string` | 是 | 同上，`allowEmpty:false`，另带 `x-role` | `{type:string,minLength:1}` |
| `f.bool()` | `bool` | 是 | `true/1/yes/on` → true；`false/0/no/off` → false | `{type:boolean}` |
| `f.array(el)` | `el.type[]` | 是 | 必须是数组，逐元素交给 `el.coerce`；`.nonEmpty()` 额外拒绝 `[]` | `{type:array,items:{...}}` |
| `f.message()` | `message` | 是 | 字符串若形如 JSON 数组则 `JSON.parse`，否则原样透传（OneBot `string \| segment[] \| object`） | 仅 description |
| `f.enum(...v)` | `enum` | 是 | 白名单字面量 | `{enum:[...]}` |
| `f.number()` | `number` | 是 | 有限数值，允许小数 | `{type:number}` |
| `f.faceId()` / `f.duration()` / `f.timestamp()` | `int` | 是 | 整型，`min:0` | `{type:integer,minimum:0}` |
| `f.raw()` | `raw` | **否** | 不做任何校验，原值透传；缺省为 `undefined` | `{}` |

链式方法（第 3032–3056 行）：

- `.optional()` → `required:false`、`default:undefined`
- `.default(v)` → **同时** `required:false`、`default:v`（注意：`.default()` 会让参数变成非必填）
- `.describe(s)` → 中文说明，写入 schema 的 `description`
- `.role(r)` → 写入 `x-role`，仅前端控件提示，**不影响校验**
- `.nonEmpty()` (仅 array) → 拒绝空数组

缺省值语义（第 3025–3031 行）：只有**键完全缺失**时才走默认值分支；显式传 `null` 会进入 coerce 并报类型错误。

### 0.2 wrapper 注入的参数

源文件有三个 wrapper，它们会**注入**参数，因此某些动作在自己的 `params` 里看不到 `group_id`：

```js
// 第 3472 行
function groupAction(def) {
  const params = { group_id: f.groupId().describe("群号"), ...def.params ?? {} };
  return defineAction({ ...def, params });
}
// 第 3483 行
function groupUserAction(def) {
  const params = {
    group_id: f.groupId().describe("群号"),
    user_id: f.memberId().describe("QQ 号"),
    ...def.params ?? {}
  };
  return defineAction({ ...def, params });
}
// 第 3443 行：defineStreamAction → kind:"stream"，params/rules 原样透传
```

本报告已把注入参数并入各动作的参数表（并在「注入」列标注）。

### 0.3 统计

- 动作总数：**190**
- 其中 `defineAction` 直接定义：125
- 其中 `groupAction`（注入 `group_id`）：53
- 其中 `groupUserAction`（注入 `group_id`+`user_id`）：7
- 其中 `defineStreamAction`（流式接口）：5
- 带别名的动作：5

---

# 第 1 部分：完整动作清单

## 1.1 官方注册表分类统计（源文件 `ACTION_GROUPS`，第 11855–11904 行）

| 官方分类 | 源文件模块 | 动作数 |
| --- | --- | --- |
| 信息 | `../onebot/src/actions/info.ts` | 5 |
| 消息 | `../onebot/src/actions/message.ts` | 5 |
| 好友 | `../onebot/src/actions/friend.ts` | 3 |
| 群信息 | `../onebot/src/actions/group-info.ts` | 6 |
| 群管理 | `../onebot/src/actions/group-admin.ts` | 17 |
| 群文件 | `../onebot/src/actions/group-file.ts` | 12 |
| 请求 | `../onebot/src/actions/request.ts` | 2 |
| 扩展 | `../onebot/src/actions/extended.ts` | 113 |
| 群相册 | `../onebot/src/actions/group-album.ts` | 8 |
| 空间 | `../onebot/src/actions/qzone.ts` | 9 |
| 系统表情 | `../onebot/src/actions/system-face.ts` | 4 |
| 流式接口 | `../onebot/src/actions/stream-file.ts + stream-download.ts` | 6 |
| **合计** | | **190** |

## 1.2 前缀分组统计（本报告派生，便于按 M3/M4 功能域查阅）

| 前缀组 | 动作数 |
| --- | --- |
| `qzone` | 9 |
| `group` | 46 |
| `friend` | 12 |
| `file` | 31 |
| `face` | 14 |
| `profile` | 9 |
| `album` | 8 |
| `todo` | 4 |
| `ai` | 3 |
| `system` | 33 |
| `其他` | 21 |
| **合计** | **190** |

> 前缀分组是本报告为便于查阅而做的机械归类（按动作名子串），**不是**源文件里的字段；源文件里的权威分类见 1.1。

## 1.3 完整动作清单（按官方分类分组，组内按源文件行号排序）

列说明：**参数个数**=合并 wrapper 注入后的参数数量；**注入**=该动作的 `group_id`/`user_id` 由 wrapper 注入而非自己声明；**流式**=`kind:"stream"`。

### 信息（5 个）

| 动作名 | 别名 | 中文 summary | 参数个数 | 前缀组 | 注入 | 源文件行 |
| --- | --- | --- | --- | --- | --- | --- |
| `get_login_info` | — | **（源文件未提供 summary）** | 0 | `system` | — | 3498 |
| `get_status` | — | **（源文件未提供 summary）** | 0 | `system` | — | 3525 |
| `get_version_info` | — | **（源文件未提供 summary）** | 0 | `system` | — | 3552 |
| `can_send_image` | — | **（源文件未提供 summary）** | 0 | `system` | — | 3587 |
| `can_send_record` | — | **（源文件未提供 summary）** | 0 | `system` | — | 3604 |

### 消息（5 个）

| 动作名 | 别名 | 中文 summary | 参数个数 | 前缀组 | 注入 | 源文件行 |
| --- | --- | --- | --- | --- | --- | --- |
| `send_msg` | — | 发送消息（按 message_type/群号 自动路由群聊或私聊） | 5 | `其他` | — | 3650 |
| `send_private_msg` | — | 发送私聊消息 | 4 | `其他` | — | 3671 |
| `send_group_msg` | — | 发送群消息 | 3 | `group` | group_id | 3686 |
| `get_msg` | — | 获取消息 | 1 | `其他` | — | 3698 |
| `delete_msg` | — | 撤回消息 | 1 | `其他` | — | 3718 |

### 好友（3 个）

| 动作名 | 别名 | 中文 summary | 参数个数 | 前缀组 | 注入 | 源文件行 |
| --- | --- | --- | --- | --- | --- | --- |
| `get_friend_list` | — | 获取好友列表 | 0 | `friend` | — | 3733 |
| `get_stranger_info` | — | 获取陌生人信息 | 1 | `profile` | — | 3769 |
| `delete_friend` | — | 删除好友 | 2 | `friend` | — | 3840 |

### 群信息（6 个）

| 动作名 | 别名 | 中文 summary | 参数个数 | 前缀组 | 注入 | 源文件行 |
| --- | --- | --- | --- | --- | --- | --- |
| `get_group_list` | — | 获取群列表 | 1 | `group` | — | 4397 |
| `get_group_info` | — | 获取群信息 | 2 | `group` | group_id | 4462 |
| `get_group_member_list` | — | 获取群成员列表 | 2 | `group` | group_id | 4487 |
| `get_group_member_info` | — | 获取群成员信息 | 3 | `group` | group_id+user_id | 4592 |
| `get_group_honor_info` | — | 获取群荣誉信息 | 2 | `group` | group_id | 4723 |
| `get_group_system_msg` | — | 获取群系统消息 | 3 | `group` | — | 4740 |

### 群管理（17 个）

| 动作名 | 别名 | 中文 summary | 参数个数 | 前缀组 | 注入 | 源文件行 |
| --- | --- | --- | --- | --- | --- | --- |
| `set_group_kick` | — | 踢出群成员 | 3 | `group` | group_id+user_id | 4816 |
| `set_group_kick_members` | — | 批量踢出群成员 | 3 | `group` | group_id | 4825 |
| `set_group_ban` | — | 禁言群成员（duration=0 解除） | 3 | `group` | group_id+user_id | 4837 |
| `set_group_whole_ban` | — | 全员禁言开关 | 2 | `group` | group_id | 4846 |
| `set_group_add_option` | — | 设置加群选项 | 2 | `group` | group_id | 4855 |
| `set_group_search` | — | 设置群被搜索方式（群指纹 / 群号搜索开关） | 3 | `group` | group_id | 4864 |
| `set_group_member_invite_policy` | — | 设置群成员邀请策略 | 2 | `group` | group_id | 4876 |
| `set_group_new_member_history_visibility` | — | 设置新成员是否可查看历史消息 | 2 | `group` | group_id | 4885 |
| `set_group_member_permissions` | — | 设置群成员权限 | 4 | `group` | group_id | 4894 |
| `set_group_admin` | — | 设置/取消管理员 | 3 | `group` | group_id+user_id | 4912 |
| `set_group_card` | — | 设置群名片（空字符串清除） | 3 | `group` | group_id+user_id | 4921 |
| `set_group_name` | — | 设置群名 | 2 | `group` | group_id | 4930 |
| `set_group_leave` | — | 退群 | 1 | `group` | group_id | 4939 |
| `set_group_special_title` | — | 设置群头衔 | 3 | `group` | group_id+user_id | 4947 |
| `set_group_anonymous` | — | 匿名开关（未实现，返回 ok） | 0 | `group` | — | 4956 |
| `set_group_anonymous_ban` | — | 匿名禁言（未实现，返回 ok） | 0 | `group` | — | 4962 |
| `set_group_portrait` | — | 设置群头像 | 2 | `group` | group_id | 4968 |

### 群文件（12 个）

| 动作名 | 别名 | 中文 summary | 参数个数 | 前缀组 | 注入 | 源文件行 |
| --- | --- | --- | --- | --- | --- | --- |
| `upload_group_file` | — | 上传群文件 | 6 | `file` | group_id | 4985 |
| `upload_private_file` | — | 上传私聊文件 | 4 | `file` | — | 5001 |
| `get_group_file_url` | — | 获取群文件下载链接 | 3 | `file` | group_id | 5031 |
| `get_group_root_files` | — | 获取群根目录文件列表 | 1 | `file` | group_id | 5052 |
| `get_group_files_by_folder` | — | 获取群子目录文件列表 | 3 | `file` | group_id | 5169 |
| `delete_group_file` | — | 删除群文件 | 2 | `file` | group_id | 5291 |
| `move_group_file` | — | 移动群文件 | 4 | `file` | group_id | 5300 |
| `rename_group_file` | — | 重命名群文件 | 4 | `file` | group_id | 5313 |
| `create_group_file_folder` | — | 创建群文件夹 | 3 | `file` | group_id | 5326 |
| `delete_group_file_folder` | — | 删除群文件夹 | 2 | `file` | group_id | 5344 |
| `rename_group_file_folder` | — | 重命名群文件夹 | 4 | `file` | group_id | 5353 |
| `get_private_file_url` | — | 获取私聊文件下载链接 | 3 | `file` | — | 5368 |

### 请求（2 个）

| 动作名 | 别名 | 中文 summary | 参数个数 | 前缀组 | 注入 | 源文件行 |
| --- | --- | --- | --- | --- | --- | --- |
| `set_friend_add_request` | — | 处理好友添加请求 | 2 | `friend` | — | 5393 |
| `set_group_add_request` | — | 处理加群请求 | 5 | `group` | — | 5404 |

### 扩展（113 个）

| 动作名 | 别名 | 中文 summary | 参数个数 | 前缀组 | 注入 | 源文件行 |
| --- | --- | --- | --- | --- | --- | --- |
| `send_like` | — | 点赞 | 2 | `其他` | — | 5911 |
| `friend_poke` | — | 好友拍一拍 | 2 | `friend` | — | 5923 |
| `group_poke` | — | 群拍一拍 | 2 | `group` | group_id+user_id | 5935 |
| `send_poke` | — | 拍一拍（群聊/私聊自动路由） | 2 | `其他` | — | 5943 |
| `set_essence_msg` | — | 设置精华消息 | 1 | `其他` | — | 5956 |
| `delete_essence_msg` | — | 移除精华消息 | 1 | `其他` | — | 5965 |
| `get_essence_msg_list` | — | 获取精华消息列表 | 1 | `其他` | group_id | 5974 |
| `set_group_reaction` | — | 群聊表情回应 | 4 | `face` | — | 5991 |
| `delete_custom_face` | — | 删除收藏表情 | 1 | `face` | — | 6009 |
| `add_custom_face` | — | 添加收藏表情 | 1 | `face` | — | 6018 |
| `modify_custom_face` | — | 修改收藏表情备注 | 2 | `face` | — | 6026 |
| `move_custom_face_to_front` | — | 收藏表情移到最前 | 1 | `face` | — | 6038 |
| `get_group_msg_history` | — | 获取群消息历史 | 4 | `group` | group_id | 6047 |
| `get_friend_msg_history` | — | 获取好友消息历史（无锚点时从服务器获取最新双向记录） | 4 | `friend` | — | 6070 |
| `mark_group_msg_as_read` | — | 标记群消息已读 | 2 | `group` | — | 6094 |
| `mark_private_msg_as_read` | — | 标记私聊消息已读 | 2 | `其他` | — | 6109 |
| `mark_msg_as_read` | — | 标记消息已读（群聊/私聊自动路由） | 2 | `其他` | — | 6124 |
| `_send_group_notice` | — | 发送群公告（支持置顶、弹窗、新成员、群昵称引导与确认） | 9 | `group` | group_id | 6140 |
| `_get_group_notice` | — | 获取群公告 | 1 | `group` | group_id | 6180 |
| `upload_forward_msg` | — | 上传转发消息 | 3 | `其他` | — | 6189 |
| `upload_foward_msg` | — | 上传转发消息（别名拼写） | 3 | `其他` | — | 6211 |
| `get_image` | — | 获取图片信息 | 2 | `system` | — | 6231 |
| `get_record` | — | 获取语音信息；传 out_format 则服务端转码并附带 base64 | 3 | `system` | — | 6247 |
| `fetch_ptt_text` | `get_ptt_text`, `get_record_text` | 获取语音转文字结果 | 1 | `其他` | — | 6276 |
| `get_cookies` | — | 获取 Cookies | 1 | `system` | — | 6300 |
| `get_csrf_token` | — | 获取 CSRF 令牌 | 0 | `system` | — | 6318 |
| `get_credentials` | — | 获取凭证 | 1 | `system` | — | 6336 |
| `set_restart` | — | 重启（不支持） | 0 | `system` | — | 6368 |
| `clean_cache` | — | 清理缓存 | 0 | `system` | — | 6376 |
| `set_friend_remark` | — | 设置好友备注 | 2 | `friend` | — | 6384 |
| `set_group_remark` | — | 设置群备注 | 2 | `group` | — | 6396 |
| `set_msg_emoji_like` | — | 设置消息表情回应 | 3 | `face` | — | 6408 |
| `_mark_all_as_read` | — | 标记全部已读 | 0 | `system` | — | 6421 |
| `get_group_file_system_info` | — | 获取群文件系统信息 | 1 | `file` | group_id | 6431 |
| `check_url_safely` | — | 检查链接安全性 | 0 | `system` | — | 6473 |
| `set_qq_profile` | — | 设置 QQ 资料 | 2 | `profile` | — | 6491 |
| `set_online_status` | — | 设置在线状态 | 3 | `profile` | — | 6505 |
| `set_diy_online_status` | — | 设置自定义在线状态 | 3 | `profile` | — | 6518 |
| `get_group_ignored_notifies` | — | 获取被过滤的入群请求 | 0 | `group` | — | 6531 |
| `get_group_ignore_add_request` | — | 获取被忽略的入群请求（NapCat） | 0 | `group` | — | 6618 |
| `get_group_shut_list` | — | 获取群禁言列表 | 1 | `group` | group_id | 6693 |
| `get_group_signed_list` | — | 获取群今日打卡列表 | 1 | `group` | group_id | 6733 |
| `forward_friend_single_msg` | — | 转发单条消息给好友 | 2 | `friend` | — | 6741 |
| `forward_group_single_msg` | — | 转发单条消息到群 | 2 | `group` | — | 6752 |
| `get_profile_like` | — | 获取资料点赞 | 3 | `profile` | — | 6763 |
| `fetch_custom_face` | — | 获取自定义表情 | 2 | `face` | — | 6846 |
| `fetch_custom_face_detail` | — | 获取自定义表情详情 | 1 | `face` | — | 6871 |
| `get_emoji_likes` | — | 获取表情回应用户 | 2 | `face` | — | 6922 |
| `fetch_emoji_like` | — | 获取表情回应用户（NapCat 分页） | 4 | `face` | — | 6960 |
| `get_friends_with_category` | — | 获取分组好友列表 | 0 | `friend` | — | 7049 |
| `set_friends_category` | — | 移动好友到指定分组 | 3 | `friend` | — | 7121 |
| `get_recent_contact` | — | 获取最近会话（占位） | 1 | `system` | — | 7140 |
| `get_online_clients` | — | 获取在线客户端 | 1 | `system` | — | 7154 |
| `_get_model_show` | — | 获取机型展示（兼容 mock） | 1 | `system` | — | 7213 |
| `_set_model_show` | — | 设置机型展示（占位） | 0 | `system` | — | 7247 |
| `get_group_at_all_remain` | — | 获取群 @全体成员 剩余次数 | 1 | `group` | group_id | 7255 |
| `get_unidirectional_friend_list` | — | 获取单向好友列表 | 0 | `friend` | — | 7286 |
| `get_clientkey` | — | 获取 clientkey | 0 | `system` | — | 7295 |
| `share_peer` | — | 分享用户/群 Ark 卡片 | 3 | `其他` | — | 7329 |
| `send_ark_share` | — | 分享用户/群 Ark 卡片（NapCat 标准名） | 3 | `其他` | — | 7353 |
| `share_group_ex` | — | 分享群 Ark 卡片 | 1 | `group` | — | 7377 |
| `send_group_ark_share` | — | 分享群 Ark 卡片（NapCat 标准名） | 1 | `group` | — | 7391 |
| `get_doubt_friends_add_request` | — | 获取可疑好友申请 | 1 | `friend` | — | 7405 |
| `set_doubt_friends_add_request` | — | 处理可疑好友申请 | 2 | `friend` | — | 7450 |
| `set_group_robot_add_option` | — | 设置群机器人加群选项 | 3 | `group` | group_id | 7463 |
| `get_collection_list` | — | 获取收藏列表 | 2 | `system` | — | 7475 |
| `create_collection` | — | 创建收藏（未实现） | 0 | `system` | — | 7593 |
| `.get_word_slices` | — | 分词（未实现） | 0 | `system` | — | 7601 |
| `set_qq_avatar` | — | 设置 QQ 头像 | 1 | `profile` | — | 7610 |
| `set_input_status` | — | 设置输入状态 | 2 | `profile` | — | 7619 |
| `get_group_info_ex` | — | 获取群信息（扩展） | 2 | `group` | — | 7631 |
| `get_group_detail_info` | — | 获取群详细信息 | 2 | `group` | — | 7645 |
| `trans_group_file` | — | 转存群文件 | 2 | `file` | — | 7659 |
| `get_file` | — | 获取文件信息（仅图片/语音缓存；群文件请用 get_group_file_url） | 2 | `file` | — | 7672 |
| `bot_exit` | — | 退出机器人 | 0 | `system` | — | 7690 |
| `nc_get_packet_status` | — | 获取 packet 状态（占位） | 0 | `system` | — | 7699 |
| `delete_group_folder` | — | 删除群文件夹 | 2 | `group` | group_id | 7713 |
| `nc_get_user_status` | — | 获取用户在线/扩展状态 | 1 | `system` | — | 7722 |
| `get_ai_characters` | — | 获取 AI 语音角色 | 2 | `ai` | group_id | 7748 |
| `get_ai_record` | — | 生成 AI 语音 | 4 | `ai` | group_id | 7804 |
| `send_group_ai_record` | — | 发送 AI 语音到群 | 4 | `ai` | group_id | 7817 |
| `request_decrypt_key` | — | 请求数据库解密密钥 | 1 | `system` | — | 7830 |
| `get_rkey` | `nc_get_rkey` | 获取下载 rkey | 0 | `system` | — | 7844 |
| `get_rkey_server` | — | 获取 rkey 服务器信息 | 0 | `system` | — | 7851 |
| `ocr_image` | `.ocr_image` | OCR 图片（服务端，需图片 URL 或已缓存的图片 file_id） | 1 | `system` | — | 7897 |
| `_del_group_notice` | — | 删除群公告（fid 或 notice_id 二选一） | 3 | `group` | group_id | 7964 |
| `send_forward_msg` | — | 发送合并转发（按 message_type/群号自动路由） | 2 | `其他` | — | 7977 |
| `send_group_forward_msg` | — | 发送群合并转发 | 3 | `group` | group_id | 8018 |
| `send_private_forward_msg` | — | 发送私聊合并转发 | 3 | `其他` | — | 8037 |
| `get_forward_msg` | — | 获取合并转发消息（id 或 message_id） | 1 | `system` | — | 8057 |
| `download_file` | — | 下载文件（url 或 base64）到 data/downloads | 3 | `file` | — | 8098 |
| `translate_en2zh` | — | 英译中 | 1 | `system` | — | 8125 |
| `set_self_longnick` | — | 设置个性签名（longNick/long_nick，严格 string） | 2 | `profile` | — | 8147 |
| `get_mini_app_ark` | — | 获取小程序卡片 ark | 0 | `system` | — | 8161 |
| `click_inline_keyboard_button` | — | 点击内联键盘按钮 | 3 | `其他` | group_id | 8175 |
| `set_group_sign` | `send_group_sign` | 群签到 | 1 | `group` | group_id | 8189 |
| `send_packet` | `.send_packet` | 发送原始 SSO 包（cmd + hex data） | 3 | `其他` | — | 8197 |
| `get_group_todo_list` | — | 获取群待办列表 | 1 | `todo` | group_id | 8214 |
| `set_group_todo` | — | 设置群待办 | 2 | `todo` | group_id | 8254 |
| `complete_group_todo` | — | 完成群待办 | 2 | `todo` | group_id | 8260 |
| `cancel_group_todo` | — | 取消群待办 | 2 | `todo` | group_id | 8266 |
| `create_flash_task` | — | 创建闪传任务 | 3 | `file` | — | 8272 |
| `get_fileset_info` | — | 获取文件集信息 | 1 | `file` | — | 8290 |
| `get_flash_file_list` | — | 获取闪传文件列表 | 1 | `file` | — | 8302 |
| `list_filesets` | — | 列出当前账号的所有闪传文件集 | 0 | `file` | — | 8310 |
| `get_flash_file_url` | — | 获取闪传文件链接 | 3 | `file` | — | 8318 |
| `get_share_link` | — | 获取文件分享链接 | 1 | `system` | — | 8330 |
| `delete_flash_file` | — | 删除闪传文件 | 1 | `file` | — | 8338 |
| `rename_flash_file` | — | 重命名闪传文件 | 2 | `file` | — | 8347 |
| `download_fileset` | — | 解析闪传文件下载直链（不下载，由调用方实现下载） | 3 | `file` | — | 8359 |
| `send_flash_msg` | — | 发送闪传消息（私聊或群聊，引用 fileset_id 让对端下载） | 3 | `file` | — | 8380 |
| `get_fileset_id` | — | 从 QQ 闪传分享码或官方分享链接获取 fileset_id | 1 | `file` | — | 8398 |
| `_get_friend_dress` | — | 获取指定 QQ 号正在使用的个性装扮（挂件/名片/来电/输入状态等） | 1 | `profile` | — | 8416 |

### 群相册（8 个）

| 动作名 | 别名 | 中文 summary | 参数个数 | 前缀组 | 注入 | 源文件行 |
| --- | --- | --- | --- | --- | --- | --- |
| `get_group_album_list` | — | **（源文件未提供 summary）** | 1 | `album` | group_id | 8544 |
| `get_qun_album_list` | — | **（源文件未提供 summary）** | 2 | `album` | group_id | 8600 |
| `upload_image_to_qun_album` | — | **（源文件未提供 summary）** | 4 | `album` | group_id | 8688 |
| `get_group_album_media_list` | — | **（源文件未提供 summary）** | 3 | `album` | group_id | 8700 |
| `do_group_album_comment` | — | **（源文件未提供 summary）** | 4 | `album` | group_id | 8727 |
| `set_group_album_media_like` | — | **（源文件未提供 summary）** | 4 | `album` | group_id | 8738 |
| `cancel_group_album_media_like` | — | **（源文件未提供 summary）** | 4 | `album` | group_id | 8749 |
| `del_group_album_media` | — | **（源文件未提供 summary）** | 3 | `album` | group_id | 8760 |

### 空间（9 个）

| 动作名 | 别名 | 中文 summary | 参数个数 | 前缀组 | 注入 | 源文件行 |
| --- | --- | --- | --- | --- | --- | --- |
| `get_qzone_msg_list` | — | 获取 QQ 空间说说列表（默认机器人自己的空间） | 3 | `qzone` | — | 8792 |
| `get_qzone_feeds` | — | 获取 QQ 空间好友动态（feed）；page_num 仅首页可靠，深翻页需时间游标（暂未实现） | 2 | `qzone` | — | 8861 |
| `send_qzone_msg` | — | 发表说说（QQ 空间，支持纯文字或带图；传 images 自动上传；可设置查看权限） | 4 | `qzone` | — | 8928 |
| `delete_qzone_msg` | — | 删除一条说说（QQ 空间，按 tid） | 1 | `qzone` | — | 8946 |
| `set_qzone_msg_right` | — | 修改一条已发说说的查看权限（QQ 空间，按 tid） | 3 | `qzone` | — | 8955 |
| `like_qzone` | — | 给一条说说点赞（QQ 空间） | 3 | `qzone` | — | 8977 |
| `unlike_qzone` | — | 取消对一条说说的点赞（QQ 空间；取消赞端点待真机核实） | 3 | `qzone` | — | 8990 |
| `comment_qzone` | — | 评论一条说说（QQ 空间，支持纯文字或带图；传 images 自动上传） | 4 | `qzone` | — | 9003 |
| `set_qzone_ban` | — | 拉黑或解除拉黑某人（修改机器人自身 QQ 空间黑名单；enable=true 拉黑，false 解除） | 2 | `qzone` | — | 9020 |

### 系统表情（4 个）

| 动作名 | 别名 | 中文 summary | 参数个数 | 前缀组 | 注入 | 源文件行 |
| --- | --- | --- | --- | --- | --- | --- |
| `fetch_sys_faces` | — | 获取 QQ 系统表情目录 | 1 | `face` | — | 11668 |
| `fetch_face_entity` | — | 按编号查询 QQ 系统表情 | 2 | `face` | — | 11699 |
| `search_sys_faces` | — | 搜索 QQ 系统表情 | 1 | `face` | — | 11714 |
| `fetch_super_face_id` | — | 判断 QQ 系统表情是否使用超级表情格式 | 2 | `face` | — | 11730 |

### 流式接口（6 个）

| 动作名 | 别名 | 中文 summary | 参数个数 | 前缀组 | 注入 | 源文件行 |
| --- | --- | --- | --- | --- | --- | --- |
| `upload_file_stream` | — | 以流式分块方式上传文件到机器人本地(返回可用于发送的本地路径) | 11 | `file` | — | 9463 |
| `clean_stream_temp_file` | — | 清理流式传输临时文件(仅清理 stream 上传/下载目录) | 0 | `file` | — | 9481 |
| `download_file_stream` | — | 以流式方式下载文件(stream 目录本地文件 / URL / 缓存媒体) | 0 | `file` | — | 9785 |
| `download_file_image_stream` | — | 以流式方式下载图片(缓存图片 id / URL / stream 目录本地文件) | 0 | `file` | — | 9792 |
| `download_file_record_stream` | — | 以流式方式下载语音(缓存语音 id / URL / stream 目录本地文件) | 0 | `file` | — | 9799 |
| `test_download_stream` | — | 测试下载流(推送 10 个数据帧,验证流式传输,不触达 QQ) | 1 | `其他` | — | 9806 |

## 1.4 没有 `summary` 的动作：其 `returns` 文案（逐字，作为权威中文描述）

下列动作在源文件中**没有 `summary` 字段**（已逐行回读确认），下表把它们的 `returns` 文案逐字列出，作为该动作在源文件中**唯一的中文描述**。

| 动作名 | 源文件行 | 源文件 `returns` 文案（逐字） |
| --- | --- | --- |
| `get_login_info` | 3498 | 当前登录账号的 QQ 号与昵称。 |
| `get_status` | 3525 | 运行状态。`online` 表示账号在线；`good` 表示已确认的收发链路健康状态。 |
| `get_version_info` | 3552 | 实现与协议版本信息。 |
| `can_send_image` | 3587 | 能力查询结果。 |
| `can_send_record` | 3604 | 能力查询结果。 |
| `get_group_album_list` | 8544 | 群相册列表数组，每项为一个相册的基本信息。 |
| `get_qun_album_list` | 8600 | NapCat 风格的相册列表封套：{album_list, attach_info, has_more}。 |
| `upload_image_to_qun_album` | 8688 | **（`returns` 也未提供）** |
| `get_group_album_media_list` | 8700 | 相册图片/视频列表及下一页分页游标；视频项包含 id、url、cover、尺寸、时长和多规格地址。 |
| `do_group_album_comment` | 8727 | **（`returns` 也未提供）** |
| `set_group_album_media_like` | 8738 | **（`returns` 也未提供）** |
| `cancel_group_album_media_like` | 8749 | **（`returns` 也未提供）** |
| `del_group_album_media` | 8760 | **（`returns` 也未提供）** |

---

# 第 2 部分：★重点动作的完整契约

说明：**必填=是** 表示参数在源文件中 `required:true`（`f.xxx()` 默认必填）；`.optional()` 与 `.default(v)` 都会把参数变成非必填。
「范围与取值」全部来自源文件的构造器选项与 `rules`，未做任何推测。

## QQ 空间（M3 要用，最重要）

### `send_qzone_msg`

- **summary**：发表说说（QQ 空间，支持纯文字或带图；传 images 自动上传；可设置查看权限）
- **官方分类**：空间　|　**前缀组**：`qzone`　|　**包装器**：`defineAction`　|　**源文件行**：8928
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.qzone.publish`

**参数（4 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `content` | string(非空) | **是** | — | 非空字符串（不允许空串） | 说说正文 |
| `images` | string(非空)[] | 否 | 无（缺省 → `undefined`） | 数组元素：f.string({ allowEmpty: false }) | 图片数组（可选），支持 file:// http:// base64://；自动上传 |
| `ugc_right` | int | 否 | `1` | 整数，min=1 | 查看权限：1=所有人可见，4=好友可见，16=部分好友可见，64=仅自己可见，128=部分好友不可见 |
| `target_uins` | uint[] | 否 | 无（缺省 → `undefined`） | 数组元素：f.uint() | 权限作用 QQ 号数组；ugc_right=16 时表示可见名单，128 时表示不可见名单 |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
content: f.string({ allowEmpty: false }).describe("说说正文"),
images: f.array(f.string({ allowEmpty: false })).describe("图片数组（可选），支持 file:// http:// base64://；自动上传").optional(),
ugc_right: f.int({ min: 1 }).describe("查看权限：1=所有人可见，4=好友可见，16=部分好友可见，64=仅自己可见，128=部分好友不可见").default(1),
target_uins: f.array(f.uint()).describe("权限作用 QQ 号数组；ugc_right=16 时表示可见名单，128 时表示不可见名单").optional(),
```

</details>

**隐式约束（`rules` invariants，在参数解析之后检查）**：

- ugc_right must be one of 1, 4, 16, 64, 128
- target_uins is required when ugc_right is 16 or 128

<details><summary>rules 原文</summary>

```js
(r) => [r.rule("ugc_right must be one of 1, 4, 16, 64, 128", (p) => QZONE_UGC_RIGHTS.has(p.ugc_right)), r.rule("target_uins is required when ugc_right is 16 or 128", (p) => p.ugc_right !== 16 && p.ugc_right !== 128 || !!p.target_uins?.length)]
```

</details>

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			if (p.images && p.images.length > 0) {
				const richval = await uploadQzoneImages(ctx, p.images, (upload) => upload.richval);
				return okResponse(await ctx.bridge.apis.qzone.publish(p.content, 1, richval, p.ugc_right, p.target_uins?.join("|")));
			}
			return okResponse(await ctx.bridge.apis.qzone.publish(p.content, void 0, void 0, p.ugc_right, p.target_uins?.join("|")));
		}
```

</details>

---

### `get_qzone_feeds`

- **summary**：获取 QQ 空间好友动态（feed）；page_num 仅首页可靠，深翻页需时间游标（暂未实现）
- **官方分类**：空间　|　**前缀组**：`qzone`　|　**包装器**：`defineAction`　|　**源文件行**：8861
- **readOnly**：`true`
- **returns**：好友动态对象，含本页 feed 数组与是否有更多页。
- **run 中调用的 bridge API**：`ctx.bridge.apis.qzone.getFeeds`

**参数（2 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `page_num` | int | 否 | `1` | 整数，min=1 | 页码（1 起；仅首页可靠） |
| `count` | int | 否 | `10` | 整数，min=1，max=50 | 本页数量 |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
page_num: f.int({ min: 1 }).describe("页码（1 起；仅首页可靠）").default(1),
count: f.int({
				min: 1,
				max: 50
			}).describe("本页数量").default(10),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			return okResponse(await ctx.bridge.apis.qzone.getFeeds(p.page_num, p.count));
		}
```

</details>

---

### `get_qzone_msg_list`

- **summary**：获取 QQ 空间说说列表（默认机器人自己的空间）
- **官方分类**：空间　|　**前缀组**：`qzone`　|　**包装器**：`defineAction`　|　**源文件行**：8792
- **readOnly**：`true`
- **returns**：说说列表对象，含说说总数与本页说说数组。
- **run 中调用的 bridge API**：`ctx.bridge.apis.qzone.getMsgList`

**参数（3 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `target_uin` | uint | 否 | 无（缺省 → `undefined`） | 正整数，最小 1；`x-role`（前端控件提示） | 目标 QQ 号，省略则取机器人自己 |
| `pos` | int | 否 | `0` | 整数，min=0 | 起始偏移 |
| `num` | int | 否 | `20` | 整数，min=1，max=100 | 本页数量 |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
target_uin: f.userId().describe("目标 QQ 号，省略则取机器人自己").optional(),
pos: f.int({ min: 0 }).describe("起始偏移").default(0),
num: f.int({
				min: 1,
				max: 100
			}).describe("本页数量").default(20),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			return okResponse(await ctx.bridge.apis.qzone.getMsgList(p.target_uin, p.pos, p.num));
		}
```

</details>

---

### `like_qzone`

- **summary**：给一条说说点赞（QQ 空间）
- **官方分类**：空间　|　**前缀组**：`qzone`　|　**包装器**：`defineAction`　|　**源文件行**：8977
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.qzone.like`

**参数（3 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `tid` | string(非空) | **是** | — | 非空字符串（不允许空串） | 说说 tid |
| `target_uin` | uint | 否 | 无（缺省 → `undefined`） | 正整数，最小 1；`x-role`（前端控件提示） | 说说所属 QQ 号，省略则为机器人自己 |
| `abstime` | int | 否 | `0` | 整数，min=0；`.role("timestamp")` | 说说发表时间（unix 秒），传真实值更可靠 |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
tid: f.string({ allowEmpty: false }).describe("说说 tid"),
target_uin: f.userId().describe("说说所属 QQ 号，省略则为机器人自己").optional(),
abstime: f.int({ min: 0 }).describe("说说发表时间（unix 秒），传真实值更可靠").default(0).role("timestamp"),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			await ctx.bridge.apis.qzone.like(p.tid, p.target_uin, true, p.abstime);
			return okResponse(null);
		}
```

</details>

---

### `unlike_qzone`

- **summary**：取消对一条说说的点赞（QQ 空间；取消赞端点待真机核实）
- **官方分类**：空间　|　**前缀组**：`qzone`　|　**包装器**：`defineAction`　|　**源文件行**：8990
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.qzone.like`

**参数（3 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `tid` | string(非空) | **是** | — | 非空字符串（不允许空串） | 说说 tid |
| `target_uin` | uint | 否 | 无（缺省 → `undefined`） | 正整数，最小 1；`x-role`（前端控件提示） | 说说所属 QQ 号，省略则为机器人自己 |
| `abstime` | int | 否 | `0` | 整数，min=0；`.role("timestamp")` | 说说发表时间（unix 秒），传真实值更可靠 |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
tid: f.string({ allowEmpty: false }).describe("说说 tid"),
target_uin: f.userId().describe("说说所属 QQ 号，省略则为机器人自己").optional(),
abstime: f.int({ min: 0 }).describe("说说发表时间（unix 秒），传真实值更可靠").default(0).role("timestamp"),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			await ctx.bridge.apis.qzone.like(p.tid, p.target_uin, false, p.abstime);
			return okResponse(null);
		}
```

</details>

---

### `comment_qzone`

- **summary**：评论一条说说（QQ 空间，支持纯文字或带图；传 images 自动上传）
- **官方分类**：空间　|　**前缀组**：`qzone`　|　**包装器**：`defineAction`　|　**源文件行**：9003
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.qzone.comment`

**参数（4 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `tid` | string(非空) | **是** | — | 非空字符串（不允许空串） | 说说 tid |
| `content` | string(非空) | **是** | — | 非空字符串（不允许空串） | 评论内容 |
| `target_uin` | uint | 否 | 无（缺省 → `undefined`） | 正整数，最小 1；`x-role`（前端控件提示） | 说说所属 QQ 号，省略则为机器人自己 |
| `images` | string(非空)[] | 否 | 无（缺省 → `undefined`） | 数组元素：f.string({ allowEmpty: false }) | 图片数组（可选），支持 file:// http:// base64://；自动上传 |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
tid: f.string({ allowEmpty: false }).describe("说说 tid"),
content: f.string({ allowEmpty: false }).describe("评论内容"),
target_uin: f.userId().describe("说说所属 QQ 号，省略则为机器人自己").optional(),
images: f.array(f.string({ allowEmpty: false })).describe("图片数组（可选），支持 file:// http:// base64://；自动上传").optional(),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			if (p.images && p.images.length > 0) {
				const richval = await uploadQzoneImages(ctx, p.images, (upload) => upload.url);
				return okResponse(await ctx.bridge.apis.qzone.comment(p.tid, p.content, p.target_uin, 1, richval));
			}
			return okResponse(await ctx.bridge.apis.qzone.comment(p.tid, p.content, p.target_uin));
		}
```

</details>

---

### `delete_qzone_msg`

- **summary**：删除一条说说（QQ 空间，按 tid）
- **官方分类**：空间　|　**前缀组**：`qzone`　|　**包装器**：`defineAction`　|　**源文件行**：8946
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.qzone.delete`

**参数（1 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `tid` | string(非空) | **是** | — | 非空字符串（不允许空串） | 说说 tid（来自 get_qzone_msg_list / send_qzone_msg） |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
tid: f.string({ allowEmpty: false }).describe("说说 tid（来自 get_qzone_msg_list / send_qzone_msg）"),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			await ctx.bridge.apis.qzone.delete(p.tid);
			return okResponse(null);
		}
```

</details>

---

### `set_qzone_ban`

- **summary**：拉黑或解除拉黑某人（修改机器人自身 QQ 空间黑名单；enable=true 拉黑，false 解除）
- **官方分类**：空间　|　**前缀组**：`qzone`　|　**包装器**：`defineAction`　|　**源文件行**：9020
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.qzone.setBlack`

**参数（2 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `user_id` | uint | **是** | — | 正整数，最小 1；`x-role`（前端控件提示） | 目标 QQ 号 |
| `enable` | bool | 否 | `true` | true/1/yes/on ⇒ true；false/0/no/off ⇒ false | true 拉黑，false 解除拉黑 |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
user_id: f.userId().describe("目标 QQ 号"),
enable: f.bool().describe("true 拉黑，false 解除拉黑").default(true),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			await ctx.bridge.apis.qzone.setBlack(p.user_id, p.enable);
			return okResponse(null);
		}
```

</details>

---

### `set_qzone_msg_right`

- **summary**：修改一条已发说说的查看权限（QQ 空间，按 tid）
- **官方分类**：空间　|　**前缀组**：`qzone`　|　**包装器**：`defineAction`　|　**源文件行**：8955
- **returns**：更新后的权限对象。
- **run 中调用的 bridge API**：`ctx.bridge.apis.qzone.updateRight`

**参数（3 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `tid` | string(非空) | **是** | — | 非空字符串（不允许空串） | 说说 tid（来自 get_qzone_msg_list / send_qzone_msg） |
| `ugc_right` | int | **是** | — | 整数，min=1 | 查看权限：1=所有人可见，4=好友可见，16=部分好友可见，64=仅自己可见，128=部分好友不可见 |
| `target_uins` | uint[] | 否 | 无（缺省 → `undefined`） | 数组元素：f.uint() | 权限作用 QQ 号数组；ugc_right=16 时表示可见名单，128 时表示不可见名单 |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
tid: f.string({ allowEmpty: false }).describe("说说 tid（来自 get_qzone_msg_list / send_qzone_msg）"),
ugc_right: f.int({ min: 1 }).describe("查看权限：1=所有人可见，4=好友可见，16=部分好友可见，64=仅自己可见，128=部分好友不可见"),
target_uins: f.array(f.uint()).describe("权限作用 QQ 号数组；ugc_right=16 时表示可见名单，128 时表示不可见名单").optional(),
```

</details>

**隐式约束（`rules` invariants，在参数解析之后检查）**：

- ugc_right must be one of 1, 4, 16, 64, 128
- target_uins is required when ugc_right is 16 or 128

<details><summary>rules 原文</summary>

```js
(r) => [r.rule("ugc_right must be one of 1, 4, 16, 64, 128", (p) => QZONE_UGC_RIGHTS.has(p.ugc_right)), r.rule("target_uins is required when ugc_right is 16 or 128", (p) => p.ugc_right !== 16 && p.ugc_right !== 128 || !!p.target_uins?.length)]
```

</details>

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			return okResponse(await ctx.bridge.apis.qzone.updateRight(p.tid, p.ugc_right, p.target_uins?.join("|")));
		}
```

</details>

---

## 资料与状态（M4 要用）

### `set_qq_profile`

- **summary**：设置 QQ 资料
- **官方分类**：扩展　|　**前缀组**：`profile`　|　**包装器**：`defineAction`　|　**源文件行**：6491
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.profile.setProfile`

**参数（2 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `nickname` | string | 否 | 无（缺省 → `undefined`） | — | **（源文件未提供 describe）** |
| `personal_note` | string | 否 | 无（缺省 → `undefined`） | — | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
nickname: f.string().optional(),
personal_note: f.string().optional(),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			const nickname = p.nickname;
			const personalNote = p.personal_note;
			await ctx.bridge.apis.profile.setProfile(nickname, personalNote);
			return okResponse();
		}
```

</details>

---

### `set_qq_avatar`

- **summary**：设置 QQ 头像
- **官方分类**：扩展　|　**前缀组**：`profile`　|　**包装器**：`defineAction`　|　**源文件行**：7610
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.profile.setAvatar`

**参数（1 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `file` | string(非空) | **是** | — | 非空；角色=image；`x-role`（前端控件提示） | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
file: f.image(),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			await ctx.bridge.apis.profile.setAvatar(p.file);
			return okResponse();
		}
```

</details>

---

### `set_self_longnick`

- **summary**：设置个性签名（longNick/long_nick，严格 string）
- **官方分类**：扩展　|　**前缀组**：`profile`　|　**包装器**：`defineAction`　|　**源文件行**：8147
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.profile.setSelfLongNick`

**参数（2 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `longNick` | raw | 否 | 无（缺省 → `undefined`） | 任意值，不做校验；可选 | **（源文件未提供 describe）** |
| `long_nick` | raw | 否 | 无（缺省 → `undefined`） | 任意值，不做校验；可选 | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
longNick: f.raw(),
long_nick: f.raw(),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			const longNick = p.longNick !== void 0 ? p.longNick : p.long_nick;
			if (typeof longNick !== "string") return failedResponse(RETCODE.BAD_REQUEST, "invalid longNick");
			await ctx.bridge.apis.profile.setSelfLongNick(longNick);
			return okResponse({});
		}
```

</details>

---

### `set_online_status`

- **summary**：设置在线状态
- **官方分类**：扩展　|　**前缀组**：`profile`　|　**包装器**：`defineAction`　|　**源文件行**：6505
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.profile.setOnlineStatus`

**参数（3 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `status` | int | **是** | — | 整数，非 0 | **（源文件未提供 describe）** |
| `ext_status` | int | 否 | `0` | 整数，min=0 | **（源文件未提供 describe）** |
| `battery_status` | int | 否 | `100` | 整数，min=0 | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
status: f.int({ nonZero: true }),
ext_status: f.int({ min: 0 }).default(0),
battery_status: f.int({ min: 0 }).default(100),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			await ctx.bridge.apis.profile.setOnlineStatus(p.status, p.ext_status, p.battery_status);
			return okResponse();
		}
```

</details>

---

### `set_diy_online_status`

- **summary**：设置自定义在线状态
- **官方分类**：扩展　|　**前缀组**：`profile`　|　**包装器**：`defineAction`　|　**源文件行**：6518
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.profile.setDiyOnlineStatus`

**参数（3 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `face_id` | int | **是** | — | 非负整数，min=0 | **（源文件未提供 describe）** |
| `face_type` | int | 否 | `1` | 整数，min=0 | **（源文件未提供 describe）** |
| `wording` | string | 否 | `""` | — | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
face_id: f.faceId(),
face_type: f.int({ min: 0 }).default(1),
wording: f.string().default(""),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			await ctx.bridge.apis.profile.setDiyOnlineStatus(p.face_id, p.wording, p.face_type);
			return okResponse();
		}
```

</details>

---

### `get_profile_like`

- **summary**：获取资料点赞
- **官方分类**：扩展　|　**前缀组**：`profile`　|　**包装器**：`defineAction`　|　**源文件行**：6763
- **readOnly**：`true`
- **returns**：点赞资料：uid、最近点赞时间、收藏与点赞统计及用户明细。
- **run 中调用的 bridge API**：`ctx.bridge.apis.profile.getLike`

**参数（3 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `user_id` | int | 否 | `0` | 整数，min=0；`.role("user_id")` | **（源文件未提供 describe）** |
| `start` | int | 否 | `0` | 整数，min=0 | **（源文件未提供 describe）** |
| `count` | int | 否 | `10` | 整数，min=0 | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
user_id: f.int({ min: 0 }).default(0).role("user_id"),
start: f.int({ min: 0 }).default(0),
count: f.int({ min: 0 }).default(10),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			return okResponse(await ctx.bridge.apis.profile.getLike(p.user_id, p.start, p.count));
		}
```

</details>

---

## AI 语音（M4 要用）

### `send_group_ai_record`

- **summary**：发送 AI 语音到群
- **官方分类**：扩展　|　**前缀组**：`ai`　|　**包装器**：`groupAction`　|　**源文件行**：7817
- **returns**：{ message_id }：已发送 AI 语音的 OneBot 消息 ID。
- **run 中调用的 bridge API**：`ctx.bridge.apis.extras.sendAiVoice`

**参数（4 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `group_id` | uint | **是** | — | 正整数，最小 1；`x-role`（前端控件提示）；**wrapper 注入** | 群号 |
| `character` | string(非空) | **是** | — | 非空字符串（不允许空串） | **（源文件未提供 describe）** |
| `text` | string(非空) | **是** | — | 非空字符串（不允许空串） | **（源文件未提供 describe）** |
| `chat_type` | int | 否 | `1` | 整数，min=0 | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
group_id: f.groupId().describe("群号"),
character: f.string({ allowEmpty: false }),
text: f.string({ allowEmpty: false }),
chat_type: f.int({ min: 0 }).default(1),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			return okResponse({ message_id: hashMessageIdInt32((await ctx.bridge.apis.extras.sendAiVoice(p.group_id, p.character, p.text, p.chat_type)).sequence, p.group_id, GROUP_MESSAGE_EVENT) });
		}
```

</details>

---

### `get_ai_characters`

- **summary**：获取 AI 语音角色
- **官方分类**：扩展　|　**前缀组**：`ai`　|　**包装器**：`groupAction`　|　**源文件行**：7748
- **readOnly**：`true`
- **returns**：按分类分组的 AI 语音角色列表，每组含分类名与角色（id、名称、试听 URL）。
- **run 中调用的 bridge API**：`ctx.bridge.apis.extras.fetchAiVoiceList`

**参数（2 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `group_id` | uint | **是** | — | 正整数，最小 1；`x-role`（前端控件提示）；**wrapper 注入** | 群号 |
| `chat_type` | int | 否 | `1` | 整数，min=0 | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
group_id: f.groupId().describe("群号"),
chat_type: f.int({ min: 0 }).default(1),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			return okResponse((await ctx.bridge.apis.extras.fetchAiVoiceList(p.group_id, p.chat_type)).map((cat) => ({
				type: cat.category,
				characters: cat.voices.map((v) => ({
					character_id: v.voiceId,
					character_name: v.voiceDisplayName,
					preview_url: v.voiceExampleUrl
				}))
			})));
		}
```

</details>

---

### `get_ai_record`

- **summary**：生成 AI 语音
- **官方分类**：扩展　|　**前缀组**：`ai`　|　**包装器**：`groupAction`　|　**源文件行**：7804
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.extras.fetchAiVoice`, `ctx.bridge.apis.groupFile.getPttUrl`

**参数（4 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `group_id` | uint | **是** | — | 正整数，最小 1；`x-role`（前端控件提示）；**wrapper 注入** | 群号 |
| `character` | string(非空) | **是** | — | 非空字符串（不允许空串） | **（源文件未提供 describe）** |
| `text` | string(非空) | **是** | — | 非空字符串（不允许空串） | **（源文件未提供 describe）** |
| `chat_type` | int | 否 | `1` | 整数，min=0 | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
group_id: f.groupId().describe("群号"),
character: f.string({ allowEmpty: false }),
text: f.string({ allowEmpty: false }),
chat_type: f.int({ min: 0 }).default(1),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			const node = await ctx.bridge.apis.extras.fetchAiVoice(p.group_id, p.character, p.text, p.chat_type);
			return okResponse(await ctx.bridge.apis.groupFile.getPttUrl(p.group_id, node));
		}
```

</details>

---

## 群待办 / 签到 / 相册（M4 要用）

> **运行时前置条件（源文件第 5864–5871 行 `groupTodoRun`，不在 `params`/`rules` 里，但会直接决定成败）**：
>
> ```js
> async function groupTodoRun(p, ctx, op) {
>   const meta = ctx.getMessageMeta(p.message_id);
>   if (!meta) return failedResponse(RETCODE.ACTION_FAILED, "message not found");
>   if (!meta.isGroup || meta.targetId !== p.group_id) return failedResponse(RETCODE.ACTION_FAILED, "message does not belong to this group");
>   if (!hasAuthoritativeSequence(meta)) return failedResponse(RETCODE.ACTION_FAILED, "message has no authoritative QQ sequence");
>   await op(p.group_id, BigInt(meta.sequence));
>   return okResponse();
> }
> ```
>
> 即 `set_group_todo` / `complete_group_todo` / `cancel_group_todo` 的 `message_id` 必须是**本进程见过并缓存过**的群消息，否则返回失败。`get_group_todo_list` 会把列表项写回缓存，因此推荐先调用它拿到 `message_id`。

### `get_group_signed_list`

- **summary**：获取群今日打卡列表
- **官方分类**：扩展　|　**前缀组**：`group`　|　**包装器**：`groupAction`　|　**源文件行**：6733
- **readOnly**：`true`
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.web.getSignedList`

**参数（1 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `group_id` | uint | **是** | — | 正整数，最小 1；`x-role`（前端控件提示）；**wrapper 注入** | 群号 |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
group_id: f.groupId().describe("群号"),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			return okResponse(await ctx.bridge.apis.web.getSignedList(p.group_id));
		}
```

</details>

---

### `set_group_todo`

- **summary**：设置群待办
- **官方分类**：扩展　|　**前缀组**：`todo`　|　**包装器**：`groupAction`　|　**源文件行**：8254
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.extras.setGroupTodo`

**参数（2 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `group_id` | uint | **是** | — | 正整数，最小 1；`x-role`（前端控件提示）；**wrapper 注入** | 群号 |
| `message_id` | messageId | **是** | — | 非 0 整数（可为负数，int32 hash） | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
group_id: f.groupId().describe("群号"),
message_id: f.messageId(),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
(p, ctx) => groupTodoRun(p, ctx, (g, s) => ctx.bridge.apis.extras.setGroupTodo(g, s))
```

</details>

---

### `get_group_todo_list`

- **summary**：获取群待办列表
- **官方分类**：扩展　|　**前缀组**：`todo`　|　**包装器**：`groupAction`　|　**源文件行**：8214
- **readOnly**：`true`
- **returns**：群待办数组，包含可用于待办操作的消息标识、服务端摘要与时间
- **run 中调用的 bridge API**：`ctx.bridge.apis.extras.getGroupTodoList`

**参数（1 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `group_id` | uint | **是** | — | 正整数，最小 1；`x-role`（前端控件提示）；**wrapper 注入** | 群号 |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
group_id: f.groupId().describe("群号"),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			const projections = (await ctx.bridge.apis.extras.getGroupTodoList(p.group_id)).map((todo) => {
				return {
					todo,
					messageId: hashMessageIdInt32(todo.sequence, p.group_id, GROUP_MESSAGE_EVENT),
					meta: {
						isGroup: true,
						targetId: p.group_id,
						sequence: todo.sequence,
						sequenceAuthoritative: true,
						eventName: GROUP_MESSAGE_EVENT,
						clientSequence: 0,
						random: todo.random,
						timestamp: todo.createdAt
					}
				};
			});
			ctx.cacheMessageMetas(projections.map(({ messageId, meta }) => ({
				messageId,
				meta
			})));
			return okResponse(projections.map(({ todo, messageId }) => {
				const cached = ctx.getMessage(messageId)?.message;
				return {
					message_id: messageId,
					message_seq: todo.sequence,
					message_random: todo.random,
					message: Array.isArray(cached) ? cached : null,
					text: todo.text,
					create_time: todo.createdAt,
					update_time: todo.updatedAt
				};
			}));
		}
```

</details>

---

### `complete_group_todo`

- **summary**：完成群待办
- **官方分类**：扩展　|　**前缀组**：`todo`　|　**包装器**：`groupAction`　|　**源文件行**：8260
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.extras.completeGroupTodo`

**参数（2 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `group_id` | uint | **是** | — | 正整数，最小 1；`x-role`（前端控件提示）；**wrapper 注入** | 群号 |
| `message_id` | messageId | **是** | — | 非 0 整数（可为负数，int32 hash） | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
group_id: f.groupId().describe("群号"),
message_id: f.messageId(),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
(p, ctx) => groupTodoRun(p, ctx, (g, s) => ctx.bridge.apis.extras.completeGroupTodo(g, s))
```

</details>

---

### `cancel_group_todo`

- **summary**：取消群待办
- **官方分类**：扩展　|　**前缀组**：`todo`　|　**包装器**：`groupAction`　|　**源文件行**：8266
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.extras.cancelGroupTodo`

**参数（2 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `group_id` | uint | **是** | — | 正整数，最小 1；`x-role`（前端控件提示）；**wrapper 注入** | 群号 |
| `message_id` | messageId | **是** | — | 非 0 整数（可为负数，int32 hash） | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
group_id: f.groupId().describe("群号"),
message_id: f.messageId(),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
(p, ctx) => groupTodoRun(p, ctx, (g, s) => ctx.bridge.apis.extras.cancelGroupTodo(g, s))
```

</details>

---

### `upload_image_to_qun_album`

- **summary**：**（源文件未提供 summary）**
- **官方分类**：群相册　|　**前缀组**：`album`　|　**包装器**：`groupAction`　|　**源文件行**：8688
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.groupAlbum.upload`

**参数（4 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `group_id` | uint | **是** | — | 正整数，最小 1；`x-role`（前端控件提示）；**wrapper 注入** | 群号 |
| `album_id` | string(非空) | **是** | — | 非空字符串（不允许空串） | **（源文件未提供 describe）** |
| `album_name` | string(非空) | **是** | — | 非空字符串（不允许空串） | **（源文件未提供 describe）** |
| `file` | string(非空) | **是** | — | 非空；角色=image；`x-role`（前端控件提示） | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
group_id: f.groupId().describe("群号"),
album_id: f.string({ allowEmpty: false }),
album_name: f.string({ allowEmpty: false }),
file: f.image(),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			await ctx.bridge.apis.groupAlbum.upload(p.group_id, p.album_id, p.album_name, p.file);
			return okResponse(null);
		}
```

</details>

---

### `get_group_album_list`

- **summary**：**（源文件未提供 summary）**
- **官方分类**：群相册　|　**前缀组**：`album`　|　**包装器**：`groupAction`　|　**源文件行**：8544
- **readOnly**：`true`
- **returns**：群相册列表数组，每项为一个相册的基本信息。
- **run 中调用的 bridge API**：`ctx.bridge.apis.groupAlbum.list`

**参数（1 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `group_id` | uint | **是** | — | 正整数，最小 1；`x-role`（前端控件提示）；**wrapper 注入** | 群号 |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
group_id: f.groupId().describe("群号"),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			return okResponse(await ctx.bridge.apis.groupAlbum.list(p.group_id));
		}
```

</details>

---

### `get_group_album_media_list`

- **summary**：**（源文件未提供 summary）**
- **官方分类**：群相册　|　**前缀组**：`album`　|　**包装器**：`groupAction`　|　**源文件行**：8700
- **readOnly**：`true`
- **returns**：相册图片/视频列表及下一页分页游标；视频项包含 id、url、cover、尺寸、时长和多规格地址。
- **run 中调用的 bridge API**：`ctx.bridge.apis.groupAlbum.getMediaList`

**参数（3 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `group_id` | uint | **是** | — | 正整数，最小 1；`x-role`（前端控件提示）；**wrapper 注入** | 群号 |
| `album_id` | string(非空) | **是** | — | 非空字符串（不允许空串） | **（源文件未提供 describe）** |
| `attach_info` | string | 否 | `""` | — | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
group_id: f.groupId().describe("群号"),
album_id: f.string({ allowEmpty: false }),
attach_info: f.string().default(""),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			return okResponse(await ctx.bridge.apis.groupAlbum.getMediaList(p.group_id, p.album_id, p.attach_info));
		}
```

</details>

---

### `delete_group_album_media`

> ⚠️ **源文件中不存在名为 `delete_group_album_media` 的动作。** 该名字在源文件里查无此动作（全文 grep `delete_group_album_media` 零命中），本报告不为其编造契约。见第 4 部分。

---

### `del_group_album_media`

- **summary**：**（源文件未提供 summary）**
- **官方分类**：群相册　|　**前缀组**：`album`　|　**包装器**：`groupAction`　|　**源文件行**：8760
- **returns**：源文件未提供 `returns` 描述
- **run 中调用的 bridge API**：`ctx.bridge.apis.groupAlbum.delete`

**参数（3 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `group_id` | uint | **是** | — | 正整数，最小 1；`x-role`（前端控件提示）；**wrapper 注入** | 群号 |
| `album_id` | string(非空) | **是** | — | 非空字符串（不允许空串） | **（源文件未提供 describe）** |
| `lloc` | string(非空) | **是** | — | 非空字符串（不允许空串） | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
group_id: f.groupId().describe("群号"),
album_id: f.string({ allowEmpty: false }),
lloc: f.string({ allowEmpty: false }),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			return okResponse(await ctx.bridge.apis.groupAlbum.delete(p.group_id, p.album_id, p.lloc));
		}
```

</details>

---

## 已有的对照（用于确认理解无误）

### `send_group_msg`

- **summary**：发送群消息
- **官方分类**：消息　|　**前缀组**：`group`　|　**包装器**：`groupAction`　|　**源文件行**：3686
- **returns**：{ message_id: number }
- **run 中调用的 bridge API**：无 `ctx.bridge.*` 调用；其它 ctx 方法：`ctx.sendGroupMessage`

**参数（3 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `group_id` | uint | **是** | — | 正整数，最小 1；`x-role`（前端控件提示）；**wrapper 注入** | 群号 |
| `message` | message | **是** | — | OneBot 消息：string \| segment[] \| object | **（源文件未提供 describe）** |
| `auto_escape` | bool | 否 | `false` | true/1/yes/on ⇒ true；false/0/no/off ⇒ false | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
group_id: f.groupId().describe("群号"),
message: f.message(),
auto_escape: f.bool().default(false),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			return okResponse({ message_id: (await ctx.sendGroupMessage(p.group_id, p.message, p.auto_escape)).messageId });
		}
```

</details>

---

### `send_private_msg`

- **summary**：发送私聊消息
- **官方分类**：消息　|　**前缀组**：`其他`　|　**包装器**：`defineAction`　|　**源文件行**：3671
- **returns**：{ message_id: number }
- **run 中调用的 bridge API**：无 `ctx.bridge.*` 调用；其它 ctx 方法：`ctx.sendPrivateMessage`

**参数（4 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `user_id` | uint | **是** | — | 正整数，最小 1；`x-role`（前端控件提示） | **（源文件未提供 describe）** |
| `message` | message | **是** | — | OneBot 消息：string \| segment[] \| object | **（源文件未提供 describe）** |
| `group_id` | int | 否 | 无（缺省 → `undefined`） | 整数，min=0 | **（源文件未提供 describe）** |
| `auto_escape` | bool | 否 | `false` | true/1/yes/on ⇒ true；false/0/no/off ⇒ false | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
user_id: f.userId(),
message: f.message(),
group_id: f.int({ min: 0 }).optional(),
auto_escape: f.bool().default(false),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			const tempGroupId = p.group_id && p.group_id > 0 ? p.group_id : void 0;
			return okResponse({ message_id: (await ctx.sendPrivateMessage(p.user_id, p.message, p.auto_escape, tempGroupId)).messageId });
		}
```

</details>

---

### `get_group_list`

- **summary**：获取群列表
- **官方分类**：群信息　|　**前缀组**：`group`　|　**包装器**：`defineAction`　|　**源文件行**：4397
- **readOnly**：`true`
- **returns**：群信息对象数组。
- **run 中调用的 bridge API**：无 `ctx.bridge.*` 调用；其它 ctx 方法：`ctx.getGroupList`

**参数（1 个）**：

| 参数名 | 类型 | 必填 | 默认值 | 范围与取值 | 中文说明（describe） |
| --- | --- | --- | --- | --- | --- |
| `no_cache` | bool | 否 | `false` | true/1/yes/on ⇒ true；false/0/no/off ⇒ false | **（源文件未提供 describe）** |

<details><summary>参数原始表达式（逐字，供核对）</summary>

```js
no_cache: f.bool().default(false),
```

</details>

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (p, ctx) => {
			const noCache = p.no_cache;
			if (ctx.getGroupList) return okResponse(await ctx.getGroupList(noCache));
			return okResponse([]);
		}
```

</details>

---

### `get_friend_list`

- **summary**：获取好友列表
- **官方分类**：好友　|　**前缀组**：`friend`　|　**包装器**：`defineAction`　|　**源文件行**：3733
- **readOnly**：`true`
- **returns**：好友列表数组，每项含 QQ 号、昵称与备注。
- **run 中调用的 bridge API**：无 `ctx.bridge.*` 调用；其它 ctx 方法：`ctx.getFriendList`

**参数**：无（`params: {}`，不接受任何参数）。

**隐式约束**：无（`rules` 字段不存在）。

<details><summary>run 实现原文（判定参数如何被使用）</summary>

```js
async (_p, ctx) => {
			if (ctx.getFriendList) return okResponse(await ctx.getFriendList());
			return okResponse([]);
		}
```

</details>

---

# 第 3 部分：调用方式（HTTP）

## 3.1 结论先说

**是的 —— 动作名就是 HTTP 端点名。** `POST {onebot_url}/<动作名>` 会被直接派发到同名 action。

## 3.2 证据链

### (1) 动作名进入注册表（`config-k6rruub-.js`，第 11767–11825 行）

`compileActionRegistry()` 把每个 `ActionSpec` 的 `names` 全部 `claim()` 进同一个 `byName` Map：

```js
const canonical = spec.names[0];
action.names.forEach((name, index) => claim(Object.freeze({
  name, canonical,
  role: index === 0 ? "canonical" : "alias",
  action
})));
```

也就是说 **`names[0]` 是规范名，其余是别名，二者都能作为 HTTP 端点名使用**。

### (2) 派发只按「名字字符串」查表（第 12006–12013 行）

```js
const registered = this.handlers.get(action);
if (!registered) {
  const response = failedResponse(RETCODE.UNKNOWN_ACTION, "unknown action");
  ...
}
```

handler 不区分来源（HTTP / WS / 内部），全部按 action 名查 `Map`。

### (3) HTTP 路径 → 动作名（`index.mjs`，第 216405–216437 行）

```js
const expectedPath = normalizePath(this.config.path ?? "/");
const incomingPath = parsedUrl.pathname;
const ep = expectedPath.endsWith("/") ? expectedPath : expectedPath + "/";
let action = "";
if (incomingPath === expectedPath || incomingPath === expectedPath + "/") action = "";
else if (incomingPath.startsWith(ep)) action = incomingPath.substring(ep.length);
else { respond(404, {..."not found"...}); return; }
```

即：**动作名 = 请求路径去掉配置里的 `path` 前缀后的剩余部分**。

### (4) 本机的实际 HTTP 配置（`config\onebot.json`）

```json
"httpServers": [{ "name": "http-default", "host": "0.0.0.0", "port": 3000,
                  "path": "/", "accessToken": "", "messageFormat": "array" }]
```

`path` 是 `"/"` → 前缀为 `/` → 端点就是根路径下的动作名。

### (5) 两种请求形态都支持（`index.mjs`，第 216438–216518 行）

| 形态 | 请求 | 参数来源 |
| --- | --- | --- |
| A. 路径即动作名 | `POST http://host:3000/send_qzone_msg` | JSON body 的**顶层字段**即参数（body 里若无 `params` 键，整个 body 就是 params） |
| B. 根路径 + action 字段 | `POST http://host:3000/` body `{"action":"send_qzone_msg","params":{...},"echo":123}` | body 的 `params` 对象 |
| C. GET 查询串 | `GET http://host:3000/get_group_list?no_cache=true` | URL search params，值做 `JSON.parse` 尝试后再回退字符串 |
| D. 表单 | `POST` + `application/x-www-form-urlencoded` | 表单字段，同样先试 `JSON.parse` |

**形参名与 body 字段名一一对应**（`defineAction` 里 `Object.keys(def.params)` 就是允许/需要提供的键；`describe()` 生成的 schema 是 `additionalProperties:true`，而实际解析走 `parse()`——只遍历参数表里声明的键，**多传的字段被静默忽略、不报错**）。

**只支持 `GET` 与 `POST`**：其他方法命中 `index.mjs` 第 216520–216527 行的 `respond(405, { retcode:1400, wording:"method not allowed" })`。

**健康检查**：`GET http://host:3000/`（路径等于配置的 `path`，无动作名）返回 `{status:"ok",retcode:0,data:{online:true}}`（第 216438–216444 行）。

**⚠️ 表单 A 的字段碰撞陷阱**（第 216479 行）：JSON body 分支里有 `if (parsedBody.action && !action) action = String(parsedBody.action)`。当用 `POST /<动作名>` 时 `action` 已非空所以不受影响；但当动作本身有名为 `action` 的参数时，它会被当成动作名吃掉。**当前 190 个动作里没有名为 `action` 的参数**（已核），所以这条只是防御性说明。

### (6) 鉴权（`index.mjs`，第 215916–215918 行）

```js
if ((request.headers.authorization ?? "") === `Bearer ${token}`) return true;
if (new URL(request.url ?? "/", "http://127.0.0.1").searchParams.get("access_token") === token) return true;
```

本机配置 `accessToken` 为空串 → 实际不校验。若将来配置了 token，EggBloom 需要带 `Authorization: Bearer <token>` 或 `?access_token=<token>`。

### (7) 响应与 `echo`（`config-k6rruub-.js`，第 2990–3003 行；`index.mjs` 第 216549 行）

- 成功：`{ status:"ok", retcode:0, data:<返回值>, message:"", wording:"", echo? }`
- 失败：`{ status:"failed", retcode:<非0>, data:{type:"error",data_type:"error"}, message:<原因>, wording:<原因> }`
- `echo` 原样回传，便于请求/响应配对。

### (8) 流式动作例外（`index.mjs`，第 216544–216547 行）

```js
if (this.ctx.api.isStreamAction(action)) {
  await streamHttpResponse(res, this.ctx.api, action, params, echo);
  return;
}
```

`kind:"stream"` 的动作（见 1.3 的「流式接口」组）返回多帧流式响应，不能按普通单帧 JSON 解析。

## 3.3 EggBloom 侧调用示例

```http
POST http://127.0.0.1:3000/send_qzone_msg
Content-Type: application/json

{ "content": "hello", "images": ["file:///D:/a.png"], "ugc_right": 1 }
```

```http
POST http://127.0.0.1:3000/get_group_list
Content-Type: application/json

{ }
```

> 端口 3000 / 路径 `/` 来自本机 `config\onebot.json`；EggBloom 侧应以用户配置的 `onebot_url` 为准。

## 3.4 动作名 → bridge API 映射（重点动作）

| 动作名 | run 中调用的 bridge API（源文件逐字） |
| --- | --- |
| `send_qzone_msg` | `ctx.bridge.apis.qzone.publish` |
| `get_qzone_feeds` | `ctx.bridge.apis.qzone.getFeeds` |
| `get_qzone_msg_list` | `ctx.bridge.apis.qzone.getMsgList` |
| `like_qzone` | `ctx.bridge.apis.qzone.like` |
| `unlike_qzone` | `ctx.bridge.apis.qzone.like` |
| `comment_qzone` | `ctx.bridge.apis.qzone.comment` |
| `delete_qzone_msg` | `ctx.bridge.apis.qzone.delete` |
| `set_qzone_ban` | `ctx.bridge.apis.qzone.setBlack` |
| `set_qzone_msg_right` | `ctx.bridge.apis.qzone.updateRight` |
| `set_qq_profile` | `ctx.bridge.apis.profile.setProfile` |
| `set_qq_avatar` | `ctx.bridge.apis.profile.setAvatar` |
| `set_self_longnick` | `ctx.bridge.apis.profile.setSelfLongNick` |
| `set_online_status` | `ctx.bridge.apis.profile.setOnlineStatus` |
| `set_diy_online_status` | `ctx.bridge.apis.profile.setDiyOnlineStatus` |
| `get_profile_like` | `ctx.bridge.apis.profile.getLike` |
| `send_group_ai_record` | `ctx.bridge.apis.extras.sendAiVoice` |
| `get_ai_characters` | `ctx.bridge.apis.extras.fetchAiVoiceList` |
| `get_ai_record` | `ctx.bridge.apis.extras.fetchAiVoice`, `ctx.bridge.apis.groupFile.getPttUrl` |
| `get_group_signed_list` | `ctx.bridge.apis.web.getSignedList` |
| `set_group_todo` | `ctx.bridge.apis.extras.setGroupTodo` |
| `get_group_todo_list` | `ctx.bridge.apis.extras.getGroupTodoList` |
| `complete_group_todo` | `ctx.bridge.apis.extras.completeGroupTodo` |
| `cancel_group_todo` | `ctx.bridge.apis.extras.cancelGroupTodo` |
| `upload_image_to_qun_album` | `ctx.bridge.apis.groupAlbum.upload` |
| `get_group_album_list` | `ctx.bridge.apis.groupAlbum.list` |
| `get_group_album_media_list` | `ctx.bridge.apis.groupAlbum.getMediaList` |
| ~~`delete_group_album_media`~~ | **源文件中不存在该动作** |
| `send_group_msg` | `（无 `ctx.bridge.*`）` |
| `send_private_msg` | `（无 `ctx.bridge.*`）` |
| `get_group_list` | `（无 `ctx.bridge.*`）` |
| `get_friend_list` | `（无 `ctx.bridge.*`）` |

# 第 4 部分：不确定项 / 未能确认

以下各项是**我在源文件中没能确认**或**与任务描述不一致**的地方。全部明确标注，不做任何填补。

## 4.1 源文件中没有 `summary` 的动作（13 个）

这些动作**确实没有** `summary` 字段，不是提取失败（已逐行回读确认）。中文说明列在文档中标记为「（源文件未提供 summary）」。

| 动作名 | 源文件行 |
| --- | --- |
| `get_login_info` | 3498 |
| `get_status` | 3525 |
| `get_version_info` | 3552 |
| `can_send_image` | 3587 |
| `can_send_record` | 3604 |
| `get_group_album_list` | 8544 |
| `get_qun_album_list` | 8600 |
| `upload_image_to_qun_album` | 8688 |
| `get_group_album_media_list` | 8700 |
| `do_group_album_comment` | 8727 |
| `set_group_album_media_like` | 8738 |
| `cancel_group_album_media_like` | 8749 |
| `del_group_album_media` | 8760 |

## 4.2 源文件中没有 `describe` 的参数（247 个，涉及 128 个动作）

这些参数的含义在源文件里**没有写中文说明**，本报告不猜测其语义。含义只能从 `run` 实现的调用位置推断（已附 run 原文）。

| 动作名 | 参数名 | 构造器原文 |
| --- | --- | --- |
| `send_msg` | `message` | `f.message()` |
| `send_msg` | `message_type` | `f.string().optional()` |
| `send_msg` | `group_id` | `f.groupId().optional()` |
| `send_msg` | `user_id` | `f.userId().optional()` |
| `send_msg` | `auto_escape` | `f.bool().default(false)` |
| `send_private_msg` | `user_id` | `f.userId()` |
| `send_private_msg` | `message` | `f.message()` |
| `send_private_msg` | `group_id` | `f.int({ min: 0 }).optional()` |
| `send_private_msg` | `auto_escape` | `f.bool().default(false)` |
| `send_group_msg` | `message` | `f.message()` |
| `send_group_msg` | `auto_escape` | `f.bool().default(false)` |
| `get_msg` | `message_id` | `f.messageId()` |
| `delete_msg` | `message_id` | `f.messageId()` |
| `delete_friend` | `block` | `f.bool().default(false)` |
| `get_group_list` | `no_cache` | `f.bool().default(false)` |
| `get_group_info` | `no_cache` | `f.bool().default(false)` |
| `get_group_member_list` | `no_cache` | `f.bool().default(false)` |
| `get_group_member_info` | `no_cache` | `f.bool().default(false)` |
| `get_group_honor_info` | `type` | `f.raw()` |
| `get_group_system_msg` | `group_id` | `f.groupId().optional()` |
| `get_group_system_msg` | `only_pending` | `f.bool().default(false)` |
| `set_group_kick` | `reject_add_request` | `f.bool().default(false)` |
| `set_group_kick_members` | `user_id` | `f.array(f.memberId()).nonEmpty()` |
| `set_group_kick_members` | `reject_add_request` | `f.bool().default(false)` |
| `set_group_ban` | `duration` | `f.duration().default(1800)` |
| `set_group_whole_ban` | `enable` | `f.bool().default(true)` |
| `set_group_add_option` | `add_type` | `f.int({ min: 0 }).default(0)` |
| `set_group_search` | `no_finger_open` | `f.int({ min: 0 }).optional()` |
| `set_group_search` | `no_code_finger_open` | `f.int({ min: 0 }).optional()` |
| `set_group_member_invite_policy` | `policy` | `f.enum("disabled", "require_approval", "no_approval", "no_approval_under_100")` |
| `set_group_new_member_history_visibility` | `visible` | `f.bool()` |
| `set_group_member_permissions` | `allow_member_upload_album` | `f.bool().optional()` |
| `set_group_member_permissions` | `allow_member_temporary_session` | `f.bool().optional()` |
| `set_group_member_permissions` | `allow_member_create_group` | `f.bool().optional()` |
| `set_group_admin` | `enable` | `f.bool().default(true)` |
| `set_group_card` | `card` | `f.string().default("")` |
| `set_group_name` | `group_name` | `f.string().default("")` |
| `set_group_special_title` | `special_title` | `f.string().default("")` |
| `set_group_portrait` | `file` | `f.image()` |
| `upload_group_file` | `file` | `f.file()` |
| `upload_group_file` | `name` | `f.string().default("")` |
| `upload_group_file` | `folder` | `f.string().default("")` |
| `upload_group_file` | `folder_id` | `f.string().default("")` |
| `upload_group_file` | `upload_file` | `f.bool().default(true)` |
| `upload_private_file` | `user_id` | `f.userId()` |
| `upload_private_file` | `file` | `f.file()` |
| `upload_private_file` | `name` | `f.string().default("")` |
| `upload_private_file` | `upload_file` | `f.bool().default(true)` |
| `get_group_file_url` | `file_id` | `f.fileId()` |
| `get_group_file_url` | `busid` | `f.raw()` |
| `get_group_files_by_folder` | `folder_id` | `f.string().default("")` |
| `get_group_files_by_folder` | `folder` | `f.string().default("")` |
| `delete_group_file` | `file_id` | `f.fileId()` |
| `move_group_file` | `file_id` | `f.fileId()` |
| `move_group_file` | `parent_directory` | `f.string({ allowEmpty: false })` |
| `move_group_file` | `target_directory` | `f.string({ allowEmpty: false })` |
| `rename_group_file` | `file_id` | `f.fileId()` |
| `rename_group_file` | `current_parent_directory` | `f.string().default("/")` |
| `rename_group_file` | `new_name` | `f.string({ allowEmpty: false })` |
| `create_group_file_folder` | `name` | `f.string({ allowEmpty: false })` |
| `create_group_file_folder` | `parent_id` | `f.string().default("/")` |
| `delete_group_file_folder` | `folder_id` | `f.string({ allowEmpty: false })` |
| `rename_group_file_folder` | `folder_id` | `f.string({ allowEmpty: false })` |
| `rename_group_file_folder` | `new_folder_name` | `f.string().default("")` |
| `rename_group_file_folder` | `name` | `f.string().default("")` |
| `get_private_file_url` | `user_id` | `f.userId().optional()` |
| `get_private_file_url` | `file_id` | `f.fileId()` |
| `get_private_file_url` | `file_hash` | `f.string().default("")` |
| `set_friend_add_request` | `flag` | `f.string({ allowEmpty: false })` |
| `set_friend_add_request` | `approve` | `f.bool().default(true)` |
| `set_group_add_request` | `flag` | `f.string({ allowEmpty: false })` |
| `set_group_add_request` | `sub_type` | `f.raw()` |
| `set_group_add_request` | `type` | `f.raw()` |
| `set_group_add_request` | `approve` | `f.bool().default(true)` |
| `set_group_add_request` | `reason` | `f.string().default("")` |
| `send_like` | `user_id` | `f.userId()` |
| `send_like` | `times` | `f.int({ min: 0 }).default(1)` |
| `friend_poke` | `user_id` | `f.userId()` |
| `friend_poke` | `target_id` | `f.userId().optional()` |
| `send_poke` | `user_id` | `f.userId()` |
| `send_poke` | `group_id` | `f.groupId().optional()` |
| `set_essence_msg` | `message_id` | `f.messageId()` |
| `delete_essence_msg` | `message_id` | `f.messageId()` |
| `set_group_reaction` | `group_id` | `f.groupId().optional()` |
| `set_group_reaction` | `message_id` | `f.messageId()` |
| `set_group_reaction` | `code` | `f.string({ allowEmpty: false })` |
| `set_group_reaction` | `is_set` | `f.bool().default(true)` |
| `delete_custom_face` | `emoji_id` | `f.string({ allowEmpty: false })` |
| `add_custom_face` | `file` | `f.image()` |
| `modify_custom_face` | `emoji_id` | `f.string({ allowEmpty: false })` |
| `modify_custom_face` | `desc` | `f.string().default("")` |
| `move_custom_face_to_front` | `emoji_id` | `f.string({ allowEmpty: false })` |
| `get_group_msg_history` | `message_id` | `f.int().default(0).role("message_id")` |
| `get_group_msg_history` | `count` | `f.int({ min: 0 }).default(20)` |
| `get_friend_msg_history` | `user_id` | `f.userId()` |
| `get_friend_msg_history` | `message_id` | `f.int().default(0).role("message_id")` |
| `mark_group_msg_as_read` | `message_id` | `f.messageId()` |
| `mark_group_msg_as_read` | `group_id` | `f.groupId().optional()` |
| `mark_private_msg_as_read` | `message_id` | `f.messageId()` |
| `mark_private_msg_as_read` | `user_id` | `f.userId().optional()` |
| `mark_msg_as_read` | `message_id` | `f.messageId()` |
| `mark_msg_as_read` | `target_id` | `f.uint().optional()` |
| `upload_forward_msg` | `messages` | `f.message().optional()` |
| `upload_forward_msg` | `message` | `f.message().optional()` |
| `upload_forward_msg` | `group_id` | `f.groupId().optional()` |
| `upload_foward_msg` | `messages` | `f.message().optional()` |
| `upload_foward_msg` | `message` | `f.message().optional()` |
| `upload_foward_msg` | `group_id` | `f.groupId().optional()` |
| `get_image` | `file` | `f.string().default("").role("image")` |
| `get_image` | `file_id` | `f.string().default("").role("file_id")` |
| `get_record` | `file` | `f.string().default("").role("record")` |
| `get_record` | `file_id` | `f.string().default("").role("file_id")` |
| `get_record` | `out_format` | `f.enum("mp3", "amr", "wma", "m4a", "spx", "ogg", "wav", "flac").optional()` |
| `fetch_ptt_text` | `message_id` | `f.string().default("").role("message_id")` |
| `get_cookies` | `domain` | `f.string().default("qun.qq.com")` |
| `get_credentials` | `domain` | `f.string().default("qun.qq.com")` |
| `set_friend_remark` | `user_id` | `f.userId()` |
| `set_friend_remark` | `remark` | `f.string()` |
| `set_group_remark` | `group_id` | `f.groupId()` |
| `set_group_remark` | `remark` | `f.string()` |
| `set_msg_emoji_like` | `message_id` | `f.messageId()` |
| `set_msg_emoji_like` | `emoji_id` | `f.string({ allowEmpty: false })` |
| `set_msg_emoji_like` | `set` | `f.bool().default(true)` |
| `set_qq_profile` | `nickname` | `f.string().optional()` |
| `set_qq_profile` | `personal_note` | `f.string().optional()` |
| `set_online_status` | `status` | `f.int({ nonZero: true })` |
| `set_online_status` | `ext_status` | `f.int({ min: 0 }).default(0)` |
| `set_online_status` | `battery_status` | `f.int({ min: 0 }).default(100)` |
| `set_diy_online_status` | `face_id` | `f.faceId()` |
| `set_diy_online_status` | `face_type` | `f.int({ min: 0 }).default(1)` |
| `set_diy_online_status` | `wording` | `f.string().default("")` |
| `forward_friend_single_msg` | `message_id` | `f.messageId()` |
| `forward_friend_single_msg` | `user_id` | `f.userId()` |
| `forward_group_single_msg` | `message_id` | `f.messageId()` |
| `forward_group_single_msg` | `group_id` | `f.groupId()` |
| `get_profile_like` | `user_id` | `f.int({ min: 0 }).default(0).role("user_id")` |
| `get_profile_like` | `start` | `f.int({ min: 0 }).default(0)` |
| `get_profile_like` | `count` | `f.int({ min: 0 }).default(10)` |
| `fetch_custom_face` | `count` | `f.int({ min: 0 }).default(10)` |
| `fetch_custom_face` | `return_type` | `f.string().default("url")` |
| `fetch_custom_face_detail` | `count` | `f.int({ min: 0 }).default(48)` |
| `get_emoji_likes` | `message_id` | `f.messageId()` |
| `get_emoji_likes` | `emoji_id` | `f.string({ allowEmpty: false })` |
| `fetch_emoji_like` | `message_id` | `f.messageId()` |
| `fetch_emoji_like` | `emojiId` | `f.string({ allowEmpty: false })` |
| `fetch_emoji_like` | `count` | `f.int({ min: 0 }).default(10)` |
| `fetch_emoji_like` | `cookie` | `f.string().default("")` |
| `get_recent_contact` | `count` | `f.int({ min: 0 }).default(10)` |
| `_get_model_show` | `model` | `f.string().default("")` |
| `share_peer` | `user_id` | `f.userId().optional()` |
| `share_peer` | `group_id` | `f.groupId().optional()` |
| `share_peer` | `phone_number` | `f.string().default("")` |
| `send_ark_share` | `user_id` | `f.userId().optional()` |
| `send_ark_share` | `group_id` | `f.groupId().optional()` |
| `send_ark_share` | `phone_number` | `f.string().default("")` |
| `share_group_ex` | `group_id` | `f.groupId()` |
| `send_group_ark_share` | `group_id` | `f.groupId()` |
| `get_doubt_friends_add_request` | `count` | `f.int({ min: 0 }).default(50)` |
| `set_doubt_friends_add_request` | `flag` | `f.string({ allowEmpty: false })` |
| `set_doubt_friends_add_request` | `approve` | `f.bool().default(true)` |
| `set_group_robot_add_option` | `robot_member_switch` | `f.int({ min: 0 }).optional()` |
| `set_group_robot_add_option` | `robot_member_examine` | `f.int({ min: 0 }).optional()` |
| `set_qq_avatar` | `file` | `f.image()` |
| `set_input_status` | `user_id` | `f.userId()` |
| `set_input_status` | `event_type` | `f.int().default(0)` |
| `get_group_info_ex` | `group_id` | `f.groupId()` |
| `get_group_info_ex` | `no_cache` | `f.bool().default(false)` |
| `get_group_detail_info` | `group_id` | `f.groupId()` |
| `get_group_detail_info` | `no_cache` | `f.bool().default(false)` |
| `trans_group_file` | `group_id` | `f.groupId()` |
| `trans_group_file` | `file_id` | `f.fileId()` |
| `get_file` | `file_id` | `f.string().default("").role("file_id")` |
| `get_file` | `file` | `f.string().default("").role("file")` |
| `delete_group_folder` | `folder_id` | `f.string({ allowEmpty: false })` |
| `nc_get_user_status` | `user_id` | `f.userId()` |
| `get_ai_characters` | `chat_type` | `f.int({ min: 0 }).default(1)` |
| `get_ai_record` | `character` | `f.string({ allowEmpty: false })` |
| `get_ai_record` | `text` | `f.string({ allowEmpty: false })` |
| `get_ai_record` | `chat_type` | `f.int({ min: 0 }).default(1)` |
| `send_group_ai_record` | `character` | `f.string({ allowEmpty: false })` |
| `send_group_ai_record` | `text` | `f.string({ allowEmpty: false })` |
| `send_group_ai_record` | `chat_type` | `f.int({ min: 0 }).default(1)` |
| `request_decrypt_key` | `db_path` | `f.string({ allowEmpty: false })` |
| `ocr_image` | `image` | `f.image()` |
| `_del_group_notice` | `fid` | `f.string().optional()` |
| `_del_group_notice` | `notice_id` | `f.string().optional()` |
| `send_forward_msg` | `messages` | `f.message().optional()` |
| `send_forward_msg` | `message` | `f.message().optional()` |
| `send_group_forward_msg` | `messages` | `f.message().optional()` |
| `send_group_forward_msg` | `message` | `f.message().optional()` |
| `send_private_forward_msg` | `user_id` | `f.userId()` |
| `send_private_forward_msg` | `messages` | `f.message().optional()` |
| `send_private_forward_msg` | `message` | `f.message().optional()` |
| `get_forward_msg` | `id` | `f.string().optional()` |
| `download_file` | `url` | `f.string().default("")` |
| `download_file` | `base64` | `f.string().default("")` |
| `download_file` | `name` | `f.string().default("")` |
| `translate_en2zh` | `words` | `f.raw()` |
| `set_self_longnick` | `longNick` | `f.raw()` |
| `set_self_longnick` | `long_nick` | `f.raw()` |
| `click_inline_keyboard_button` | `bot_appid` | `f.uint()` |
| `click_inline_keyboard_button` | `msg_seq` | `f.uint()` |
| `send_packet` | `cmd` | `f.string({ allowEmpty: false })` |
| `send_packet` | `data` | `f.string().default("")` |
| `send_packet` | `rsp` | `f.bool().default(true)` |
| `set_group_todo` | `message_id` | `f.messageId()` |
| `complete_group_todo` | `message_id` | `f.messageId()` |
| `cancel_group_todo` | `message_id` | `f.messageId()` |
| `create_flash_task` | `name` | `f.string().optional()` |
| `create_flash_task` | `thumb_path` | `f.string().optional()` |
| `get_fileset_info` | `fileset_id` | `f.string({ allowEmpty: false })` |
| `get_flash_file_list` | `fileset_id` | `f.string({ allowEmpty: false })` |
| `get_flash_file_url` | `fileset_id` | `f.string({ allowEmpty: false })` |
| `get_flash_file_url` | `file_name` | `f.string().optional()` |
| `get_flash_file_url` | `file_index` | `f.number().optional()` |
| `get_share_link` | `fileset_id` | `f.string({ allowEmpty: false })` |
| `delete_flash_file` | `fileset_id` | `f.string({ allowEmpty: false })` |
| `rename_flash_file` | `fileset_id` | `f.string({ allowEmpty: false })` |
| `rename_flash_file` | `new_name` | `f.string({ allowEmpty: false })` |
| `download_fileset` | `fileset_id` | `f.string({ allowEmpty: false })` |
| `download_fileset` | `file_name` | `f.string().optional()` |
| `download_fileset` | `file_index` | `f.number().optional()` |
| `send_flash_msg` | `fileset_id` | `f.string({ allowEmpty: false })` |
| `send_flash_msg` | `user_id` | `f.userId().optional()` |
| `send_flash_msg` | `group_id` | `f.groupId().optional()` |
| `get_qun_album_list` | `attach_info` | `f.string().default("")` |
| `upload_image_to_qun_album` | `album_id` | `f.string({ allowEmpty: false })` |
| `upload_image_to_qun_album` | `album_name` | `f.string({ allowEmpty: false })` |
| `upload_image_to_qun_album` | `file` | `f.image()` |
| `get_group_album_media_list` | `album_id` | `f.string({ allowEmpty: false })` |
| `get_group_album_media_list` | `attach_info` | `f.string().default("")` |
| `do_group_album_comment` | `album_id` | `f.string({ allowEmpty: false })` |
| `do_group_album_comment` | `lloc` | `f.string({ allowEmpty: false })` |
| `do_group_album_comment` | `content` | `f.string({ allowEmpty: false })` |
| `set_group_album_media_like` | `album_id` | `f.string({ allowEmpty: false })` |
| `set_group_album_media_like` | `batch_id` | `f.string({ allowEmpty: false })` |
| `set_group_album_media_like` | `lloc` | `f.string().optional()` |
| `cancel_group_album_media_like` | `album_id` | `f.string({ allowEmpty: false })` |
| `cancel_group_album_media_like` | `batch_id` | `f.string({ allowEmpty: false })` |
| `cancel_group_album_media_like` | `lloc` | `f.string().optional()` |
| `del_group_album_media` | `album_id` | `f.string({ allowEmpty: false })` |
| `del_group_album_media` | `lloc` | `f.string({ allowEmpty: false })` |
| `fetch_sys_faces` | `refresh` | `refreshField()` |
| `fetch_face_entity` | `face_id` | `faceIdField()` |
| `fetch_face_entity` | `refresh` | `refreshField()` |
| `fetch_super_face_id` | `face_id` | `faceIdField()` |
| `fetch_super_face_id` | `refresh` | `refreshField()` |

## 4.3 任务描述与源文件的命名差异（重要）

| 任务里写的名字 | 源文件实际情况 | 结论 |
| --- | --- | --- |
| `delete_group_album_media` | 源文件第 8761 行是 `name: "del_group_album_media"`；全文 grep `delete_group_album` **零命中** | **未能确认存在 `delete_group_album_media`**；EggBloom 必须调用 `del_group_album_media` |
| `set_group_todo` / `complete_group_todo` / `cancel_group_todo` / `get_group_todo_list` | 均存在 | 一致 |
| `get_group_signed_list` | 存在（群签到打卡列表） | 一致；注意「打卡」用 `get_group_signed_list`，「群签到」用 `set_group_sign`/`send_group_sign` |
| `get_qzone_feeds` | 存在，但 summary 自述「`page_num` 仅首页可靠，深翻页需时间游标（暂未实现）」 | 分页能力**受源文件自身承认的限制** |
| `unlike_qzone` | 存在，但 summary 自述「取消赞端点待真机核实」 | 取消点赞的可靠性**源文件自己标注为待核实** |

## 4.4 其他未能确认项

| # | 项目 | 未能确认的内容 |
| --- | --- | --- |
| 1 | `f.raw()` 参数的真实契约 | `set_self_longnick` 的 `longNick` / `long_nick` 都用 `f.raw()` 声明为**可选、无类型**，schema 里是 `{}`；真正的「必须是 string」约束只写在 `run` 里（`if (typeof longNick !== "string") return failedResponse(...)`）。**参数级契约与运行时约束不一致**，文档只能两者都写。 |
| 2 | `send_qzone_msg` 的 `images` 分隔符 | 上传后 richval 用 `parts.join("\t")`（制表符）拼接（第 8782 行）。**该分隔符语义未在 `describe` 中说明**，也未在源文件其它地方注释。 |
| 3 | `send_qzone_msg` 的 `content` 与 `images` 组合 | `run` 里两条分支：有图 → `publish(content, 1, richval, ...)`；无图 → `publish(content, undefined, undefined, ...)`。**第 2/3 个位置参数（`1` 与 `undefined`）的含义在源文件中没有命名，无法确认**。 |
| 4 | `like_qzone` / `unlike_qzone` 的 `abstime` | describe 说「说说发表时间（unix 秒），传真实值更可靠」，默认 `0`。**传 0 时服务端行为无法从源文件确认**。 |
| 5 | `ugc_right` 的 `f.int({min:1})` 与 rules 的双重约束 | 构造器只写 `min:1`，合法集合 `{1,4,16,64,128}` 只由 `rules` 中的 `QZONE_UGC_RIGHTS`（第 8784 行）保证。**schema 层面看不出枚举**（JSON Schema 里只有 `minimum:1`）。 |
| 6 | `target_uins` 与 `ugc_right` 的耦合 | 由 `rules` 保证「`ugc_right` 为 16 或 128 时必填且非空」，但**该规则对 `ugc_right` 为其它值时是否必须缺省，源文件未做约束**（传了也不报错）。 |
| 7 | `get_profile_like` 的 `user_id`/`start`/`count` | 三个参数**均无 describe、无范围、无默认语义说明**；`user_id` 是 `f.int().default(0)` 而非 `f.userId()`，`0` 的具体含义（是否代表「自己」）**未能确认**。 |
| 8 | `set_online_status` 的 `status` 取值表 | `f.int({nonZero:true})` 只要求非 0，**合法状态码枚举在源文件中不存在**（无 describe、无 enum、无 rules）。 |
| 9 | `set_diy_online_status` 的 `face_type` | `f.int({min:0}).default(1)`，**取值含义未能确认**（无 describe）。 |
| 10 | `get_group_album_list` 与 `upload_image_to_qun_album` | 这两个动作**完全没有 `summary`、参数也全无 `describe`**；且 `upload_image_to_qun_album` 要求 `album_id` 与 `album_name` **同时提供**，但**源文件没有 rules 强制二者一致性**。 |
| 11 | `set_group_todo` 类动作的前置条件 | `groupTodoRun`（第 5864 行）要求该 `message_id` **必须已被本进程缓存过**（`ctx.getMessageMeta`），且 `meta.isGroup && meta.targetId === group_id`，且 `hasAuthoritativeSequence(meta)`。**这三个前置条件的错误文案对客户端可读，但「怎样才算 authoritative sequence」在源文件中未展开说明**。 |
| 12 | AI 语音的 `chat_type` | `f.int({min:0}).default(1)`，**取值含义（1 代表什么）源文件无 describe、无 enum，未能确认**。 |
| 13 | `get_ai_record` 的返回值 | `run` 返回 `ctx.bridge.apis.groupFile.getPttUrl(...)` 的结果，但**没有 `returns` 描述，返回结构未能从本文件确认**。 |
| 14 | 各动作返回值的完整字段 | 只有部分动作提供了 `returnsSchema`；本报告仅逐字摘录了 `returns` 文案，**未展开 `returnsSchema` 的完整字段树**（如需可再提取）。 |
| 15 | `readOnly` 的语义 | `readOnly` 字段只出现在 `describe()` 输出里（第 3432 行 `readOnly: def.readOnly ?? false`），**本文件中未见它影响 HTTP 校验或派发的代码**；是否仅用于 WebUI/文档展示**未能确认**。 |
| 16 | `additionalProperties` | `describe()` 生成 `additionalProperties: true`，即**多余参数被静默忽略**。但这是 `describe()` 的产物，**HTTP 实际解析走的是 `parse()`（只遍历 `Object.keys(def.params)`），多余的键被丢弃且不报错**——两者一致，但源文件未对此写注释。 |
| 17 | `set_group_anonymous` / `set_group_anonymous_ban` / `create_collection` | summary 自述「未实现，返回 ok」/「未实现」。**它们在源文件中存在且可调用，但行为是空实现**。 |
| 18 | `send_msg` 的路由判定 | 参数里 `message_type`/`group_id`/`user_id` **全部可选**（`f.string().optional()` / `f.groupId().optional()` / `f.userId().optional()`），路由逻辑写在 `run` 里：`isTempReply = message_type==="private" && user_id!==undefined && group_id!==undefined`。**「三者都不传」时返回什么在参数层面看不出**（`run` 里会返回 `user_id is required`）。 |
| 19 | `f.enum` 参数在 HTTP 上的写法 | `set_group_member_invite_policy.policy`、`get_record.out_format` 用 `f.enum(...)`。coerce 只接受 **原始类型相同的字面量**（`typeof raw === "string" || typeof raw === "number"` 且 `values.includes(raw)`），**枚举值大小写、是否允许大小写变体，源文件未做归一化**（无 `.toLowerCase()`）。 |
| 20 | 带前导 `.` 的动作名 | 源文件里有 3 个带点号的可执行名：`ocr_image`/`.ocr_image`（规范名是 `ocr_image`）、`send_packet`/`.send_packet`（规范名是 `send_packet`）、以及**只有** `.get_word_slices` 一个名字的动作（第 7602 行，且 `run` 直接 `not yet implemented`）。**点号在 HTTP 路径中的规范化行为源文件未涉及，未能确认**；EggBloom 应优先用不带点的规范名。 |

---

## 附：提取过程可复现信息

- 提取脚本：`D:\git\_eggbloom-backup-20261003\extract_full.py`（括号配对扫描 + 参数链解析）
- 中间产物：`D:\git\_eggbloom-backup-20261003\actions_full.json`（每动作的结构化记录，含逐字 params/rules/run）
- 源文件：`E:\SnowLuma-v1.14.10-win-x64\config-k6rruub-.js`，405,790 字节 / 12,403 行（本次任务全程只读，未做任何修改；最后写入时间仍为 2026/8/16 14:02）
