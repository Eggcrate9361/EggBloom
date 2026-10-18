# EggBloom Desktop 使用说明

## 简介
EggBloom Desktop 是一个本地 QQ 机器人管理工具，提供 Web 管理后台、协议层管理和 AI 对话能力。

## 启动
双击 `EggBloom Desktop.exe` 即可启动（桌面壳会拉起后端并打开界面）。
从源码运行时用 `python webui/webui_server.py`。

## 首次配置
首次打开时会弹出配置向导，按提示填写：
- AI 供应商、API 地址、API 密钥、模型
- QQ 路径（可选）
- Bot QQ 号

如跳过或需要修改，可在应用内“设置”页继续配置。

## 主要功能
- 仪表盘：查看 Bot 状态、运行时长、消息数、内存占用
- 设置：配置 AI、监听对象、群号、行为参数
- 日志：查看运行日志，支持搜索和筛选
- 控制台：执行指令，查看学习状态、群聊历史、私聊记忆
- 协议：管理协议层启动/停止，查看日志
- 插件：浏览和启用插件
- WebUI：打开浏览器管理后台

## 注意事项
- 需要 Python 3.10+ 和 Node.js
- 协议层需要 QQNT 和 PMHQ
- 请勿删除 `AppData/Local/EggBloom_v3` 目录，否则配置和日志会丢失
