<p align="center">
  <img src="assets/readme-hero.png" width="840" alt="EggBloom">
</p>

<p align="center">
  <b>跑在 QQ 里的本地陪伴型 AI</b><br>
  OneBot v11 · Windows · Python 3.13 · MIT
</p>

<p align="center">
  <img src="https://img.shields.io/badge/release-v1.5.7-16130F?style=flat-square" alt="v1.5.7">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-C9A96E?style=flat-square" alt="MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.13-16130F?style=flat-square&logo=python&logoColor=C9A96E" alt="Python 3.13">
  <img src="https://img.shields.io/badge/OneBot-v11-16130F?style=flat-square" alt="OneBot v11">
  <img src="https://img.shields.io/badge/Windows-10%2F11-16130F?style=flat-square&logo=windows&logoColor=C9A96E" alt="Windows 10/11">
</p>

---

## 这是什么

跑在 QQ 私聊和群聊里的 AI 助手。Electron 桌面壳 + 独立 Python 后端 + 单文件 WebUI，本机运行，模型走 OpenAI 兼容接口。

它不追求功能多，只做三件事：

- **记得住**：记忆分成可查看、可回退的几层，不是只留一段聊天记录。
- **变得像**：人格从群里的表达里长出来，重复出现才写进去。
- **花得少**：上下文按预算分配，每类输入都有上限。

---

## 三个差异点

### 记忆是分层的

| 层 | 位置 | 作用 |
|---|---|---|
| 沙漏原文 | `sandglass_lite.py` | 按时间保存原始对话，可直接翻、可重建索引 |
| 结构与向量 | `plugins/ego/super_memory/` | 结构化条目 + 本地向量召回（bge-small-zh，LiGGUF 推理） |
| 上下文装配 | `plugins/ego/personality_engine/` | 多源合并、阈值、去重、预算 |
| 表达学习 | `plugins/ego/slang_learner/` | 群里的黑话与常用表达 |
| 人格 | `plugins/ego/personality_engine/` | 人格条目与版本 |

数据都在本机，文本可直接编辑；人格有历史版本，可回退。

### 人格靠证据生长

群里的表达要重复出现才写进人格，单次发言不算数；每次改动有版本记录，能回退。

### 上下文是预算制

历史、网页、工具结果、图片各占一份预算，上限集中在 `model_caps.py` 与 `bot_core.py`，不是能塞多少塞多少。

---

## 能力

| 能力 | 状态 |
|---|---|
| 私聊 / 群聊（OneBot v11） | 有 |
| 长期记忆与向量召回 | 有 |
| 人格蒸馏与版本回退 | 有 |
| 群聊行为（接话窗口、主动开口、静默时段） | 有 |
| 插件系统（ABI v2、热加载、自造插件审批） | 有，仓库内置近 20 个 |
| Agent 工具（网页检索、网页操作、定时任务、QQ 空间） | 有 |
| WebUI 控制台 | 15 页 |
| 图片 | 可接收当前轮的图并交给多模态模型；可发表情包 |
| 语音识别 / 语音合成 / 视频 | 无 |
| 多平台 | 无，当前只做 Windows |

群聊里的一次发言，要先过这几道闸：

<details>
<summary>展开看发言决策流程</summary>
<br>
<img src="assets/gate-flow.png" alt="发言决策闸门">
</details>

---

## 快速开始

### 用发行版

1. 下载 `EggBloom-Standard.zip`，解压到不含中文和空格的路径。
2. 双击 `EggBloom Desktop.exe`。
3. 在「配置」页的配置向导里填提供商、API 地址与密钥。

### 从源码

```bash
git clone https://github.com/Eggcrate9361/EggBloom.git
cd EggBloom
pip install -e .
python webui/webui_server.py
```

默认地址 `http://localhost:8080`。

协议驱动需要另外安装（SnowLuma / NapCat / LLOneBot 任选其一），本仓库不含 QQ 协议实现。
首次运行不用复制任何配置文件；配置向导保存时会写出 `bot_config.json`，API Key 存进 Windows 凭据管理器。

详细步骤见 [docs/USAGE.md](docs/USAGE.md)，插件开发见 [docs/PLUGIN_DEV.md](docs/PLUGIN_DEV.md)。

---

## 下载

| 包 | 体积 | 内容 |
|---|---|---|
| `EggBloom-Lite.zip` | 5.3 MB | 源码，自行安装依赖与协议驱动 |
| `EggBloom-Standard.zip` | 215 MB | 含 20 张表情包样本与 Python 运行时，解压即用 |

`EggBloom-Full.zip`（706 MB，全量表情包库）仅本地自用，不对外分发。

去 [Releases](https://github.com/Eggcrate9361/EggBloom/releases) 下载，每个版本附校验文件。

---

## 系统要求

- Windows 10 / 11
- 发行版自带 Python 3.13；源码运行需要 Python 3.13（`>=3.13,<3.14`）
- 一个 OneBot v11 协议端
- 一个 OpenAI 兼容的模型接口（默认 DeepSeek）

---

## 数据与隐私

| 内容 | 位置 | 说明 |
|---|---|---|
| 沙漏原文与索引 | `.neurobase/`（或 `~/.neurobase/`） | 本机明文 |
| 聊天记录 | `chat_logs/QQ_<号>/` | 本机 Markdown 明文 |
| 记忆库 | `plugins/ego/*/data/`、`self_learning_db/` | 本机 SQLite / JSON |
| 人格与版本 | `personality/`、`personality_versions/` | 本机 |
| 配置 | `bot_config.json` | 本机明文 |
| API Key | Windows 凭据管理器 | 由 `cred_store.py` 读写，不落盘明文 |
| 运行日志 | `logs/` | 本机明文 |

这些目录都不在 git 里，也不随 Lite 包分发。升级时只覆盖程序文件，不要动它们。

---

## 许可

- 代码：MIT，见 [LICENSE](LICENSE)。
- 字体：SIL OFL-1.1，见 [webui/fonts/README.txt](webui/fonts/README.txt)。
- 随包分发的模型与推理库：MIT，见 [licenses/](licenses/) 与 [THIRD_PARTY.md](THIRD_PARTY.md)。
- 名称与 Logo 不在 MIT 授权范围内。

安全问题走 [SECURITY.md](SECURITY.md)，不要开公开 Issue。

---

## 文档

[部署教程](docs/USAGE.md) · [English](docs/USAGE_en.md) · [插件规范](docs/PLUGIN_SPEC.md) · [插件开发](docs/PLUGIN_DEV.md) · [发布打包](docs/发布打包.md) · [变更日志](CHANGELOG.md)

---

## 演示

控制台 v4，十五个页。导航是左下角的机械闸片架，按右上角热区展开。

<img src="docs/screenshots/nav.png" width="100%" alt="导航">

| | |
| --- | --- |
| <img src="docs/screenshots/protocol.png" width="100%" alt="协议"> | <img src="docs/screenshots/config.png" width="100%" alt="配置中心"> |
| <img src="docs/screenshots/behavior.png" width="100%" alt="群聊行为"> | <img src="docs/screenshots/agent.png" width="100%" alt="Agent"> |

截图由 `shoot_webui.py` 用 Playwright 驱动真实 WebUI 生成。

---

## 注意

- 接入 QQ 可能违反《腾讯 QQ 用户服务协议》。
- 模型按量计费，群越活跃花得越多。
- 单人维护，不接受常规 PR；Bug 与功能建议走 Issue。
