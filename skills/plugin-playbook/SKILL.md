---
name: plugin-playbook
description: 调用 EggBloom 插件能力的操作手册（发 QQ 空间 / 定时任务 / 主动聊天 / 人格蒸馏 / 网页操作）。任务涉及插件能力，或主人问"你能做什么"时使用。
---

# 插件调用手册（EggBloom）

## 何时用
需要**真实操作**（发空间、建定时任务、主动说话、查蒸馏进度）时，
不要用语言假装做过 —— 先 `plugin_list` 确认，再 `plugin_call` 真正执行。

## 步骤
1. `plugin_list {}` —— 看有哪些插件、简介、动作、是否启用
2. `plugin_call {plugin, action, params}` —— 执行；未启用的插件会**弹授权窗**，等主人点「允许并继续」
3. 工具返回是真实结果，如实转述；失败就说失败原因，不要编造成功

## 常用对照
| 想做的事 | plugin | action |
|---|---|---|
| 发 QQ 空间说说 | qzone | run（有频率硬上限；默认未启用，会先弹授权） |
| 定时任务 | scheduler | add / list / run_now / toggle |
| 主动开口聊天 | proactive_talk | send_now / test_decision |
| 人格蒸馏 | 这样脆弱的东西 | start_distill / distill_status / stats |
| 操作网页 | web_operator | get_tasks / …（CDP 结构化感知） |

## 注意
- 未启用的插件会被拒绝并弹授权窗：**如实告诉主人在等授权**，不要盲目重试
- params 是该动作认识的参数对象；不确定动作名就先 plugin_list
- plugin_call 的结果里 error 字段出现时，把原因转述给主人，不要吞掉
