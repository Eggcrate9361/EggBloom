<table bgcolor="#16130F" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td align="center" style="padding:40px 16px 36px 16px">

<img src="assets/readme-hero.png" width="840" alt="EGGBLOOM"><br><br>

<span style="color:#F0EEEA;font-size:15px">一个跑在 QQ 群里的 AI，尽量别像个 AI。</span>

<br><br>

<a href="docs/USAGE.md"><img src="https://img.shields.io/badge/%E9%83%A8%E7%BD%B2%E6%95%99%E7%A8%8B-Setup-C9A96E?style=for-the-badge" alt="部署教程"></a>
<a href="docs/USAGE_en.md"><img src="https://img.shields.io/badge/%E6%96%87%E6%A1%A3-Docs-16130F?style=for-the-badge" alt="文档"></a>
<a href="CHANGELOG.md"><img src="https://img.shields.io/badge/%E5%8F%98%E6%9B%B4-Changelog-16130F?style=for-the-badge" alt="变更日志"></a>

</td></tr></table>

<p align="center">
<img src="https://img.shields.io/badge/Python-3.13%2B-16130F?style=flat-square&logo=python&logoColor=C9A96E" alt="Python 3.13+">
<img src="https://img.shields.io/badge/OneBot-v11-16130F?style=flat-square" alt="OneBot v11">
<img src="https://img.shields.io/badge/Platform-Windows-16130F?style=flat-square&logo=windows&logoColor=C9A96E" alt="Windows">
<img src="https://img.shields.io/badge/License-MIT-C9A96E?style=flat-square" alt="MIT">
</p>

OneBot v11，支持 SnowLuma / NapCat / LLOneBot。模型后端 DeepSeek / OpenAI / Ollama / LM Studio / llama-server。不用装外部数据库，解压双击就能起。只有 Windows 试过，别的系统没验。

---

## 🎉 是什么

别的 bot 在比谁功能多，这个在比谁更像群里那个人。

会敷衍，会骂人，会记不住三天前说过什么。这些不是没做完，是故意留着的。

真正常用的就三样：知道什么时候该闭嘴、说话不带客服腔、被怼了能骂回去。

<details>
<summary><b>都会什么（问就是都会，问就是都不精）</b></summary>
<br>

| | |
| --- | --- |
| 💬 群聊私聊 | 群里有白名单，知道谁是管理员 |
| ⏱️ 发言时机 | 值不值得回、在不在叫我、他是不是在追着问，每层过一道闸 |
| 🧠 记忆 | 三层，会忘。跨群只自知不串味 |
| 🗣️ 黑话 | 预置 563 条，带误报探针 |
| 😂 表情包 | 可以只回一张图不写字 |
| 🔧 干活 | 自己判断该不该接活，能跑脚本、查文件、发群文件 |
| 🧩 插件 | 热加载，生成物不能自己装 |

</details>

<details>
<summary><b>群里的一次发言，具体是这样</b></summary>
<br>
<img src="assets/gate-flow.png" alt="发言决策闸门">
</details>

---

## 🚀 快速开始

标准版解压到不含中文空格的路径，双击 `EggBloom Desktop.exe`，浏览器开 `http://localhost:8080`。

源码版：

```bash
git clone https://github.com/Eggcrate9361/EggBloom.git
cd EggBloom && pip install -e . && python webui/webui_server.py
```

首次运行把 `bot_config.json.example` 复制成 `bot_config.json`，填 `bot_qq` 和 `onebot_url`。API Key 在设置页填，存进 Windows 凭据管理器，不落盘。

## 📦 分发包

| 包 | 体积 | 内容 |
| --- | --- | --- |
| Lite | 4 MB | 本体，没素材 |
| Standard | 214 MB | 带 20 个梗 |
| Full | 705 MB | 全量素材（暂不发布） |

> 下载地址：待补

## 📚 文档

[部署教程](docs/USAGE.md) · [English](docs/USAGE_en.md) · [插件规范](docs/PLUGIN_SPEC.md) · [发布清单](docs/RELEASE_CHECKLIST.md)

---

## ⚠️ 注意

- 接入 QQ 可能违反《腾讯 QQ 用户服务协议》，账号风险自己担
- 项目还在动，配置和 API 说不上什么时候就改了
- 跑一次对话烧一次 Token，群越活跃烧得越狠

## 演示

> 视频 / 截图：待补

## 社区

> 交流群：待补

## 许可

MIT，见 [LICENSE](LICENSE)。安全问题走 [SECURITY.md](SECURITY.md)。

本项目不含任何 QQ 协议实现，也不提供、不分发、不维护协议驱动。
