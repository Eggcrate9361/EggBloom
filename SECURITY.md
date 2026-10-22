# 安全策略

## 支持范围

| 版本 | 状态 |
|---|---|
| 1.5.x | 接收安全报告 |
| < 1.5 | 不再维护 |

## 怎么报告

**不要开公开 Issue。**

用 GitHub 的 [Private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
（仓库 → Security → Report a vulnerability），或直接给维护者发私信。

报告里请包含：

- 受影响版本 / 提交号
- 复现步骤（最小可复现最好）
- 影响范围（能读到什么、能执行什么、是否需要本机权限）
- 如果涉及凭据，**不要贴真实 token**，用 `sk-***` 代替

## 这个项目的攻击面

EggBloom 是**本地优先**的桌面程序，默认只监听 `127.0.0.1`，但它仍然：

1. **会执行本机命令**：`py_run`、`job_*`、`open_app`、`send_user_file` 等工具可读写文件、起进程。
2. **会接收群消息**：群友的文本会进入模型上下文，存在提示注入（prompt injection）风险。
3. **会保存凭据**：API Key 可存 Windows 凭据管理器，也可能出现在 `bot_config.json`。
4. **会加载插件**：插件是本地 Python 代码，拥有与主程序相同的权限。

因此以下问题都算安全问题，欢迎报告：

- 群聊消息绕过工具白名单 / 作用域限制
- 越权读写任意路径、越权发文件
- WebUI 的鉴权/CSRF/路径穿越
- 凭据泄漏到日志、出站文本或打包产物
- 插件卸载 fail-open（插件已禁用但代码仍被执行）

## 不算安全问题的

- 模型自己说错话、幻觉、语气不像人（这是效果问题，走 [Discussions](https://github.com/Eggcrate9361/EggBloom/discussions)）
- 需要本机管理员权限才能利用的配置问题
- 依赖上游（NapCat / Electron / Python）的已知漏洞 —— 请同时报给上游

## 维护者的承诺

个人项目，没有 SLA。会尽量在 **7 天内**确认收到，30 天内给出修复或说明。
修复会在 CHANGELOG 的 `Security` 段落里致谢（除非你要求匿名）。
