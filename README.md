# EggBloom Desktop

面向 QQ 的 AI 桌面助手。基于 OneBot v11 协议，支持多种协议驱动（SnowLuma / NapCat / LLOneBot），内置 DeepSeek/Ollama/llama.cpp 多模型后端，提供群聊/私聊 AI、情绪状态机、分层记忆、群聊决策、联系人管理、WebUI 管理面板。

---

## 免责声明 / Disclaimer

本项目仅为管理面板与 AI 核心逻辑，不包含任何 QQ 协议实现。

- 协议驱动（SnowLuma、NapCat、LLOneBot 等）均为第三方开源项目，本项目仅通过标准 OneBot v11 接口（HTTP 动作 + WebSocket 事件桥）进行桥接。
- 使用本项目接入 QQ 可能违反《腾讯 QQ 用户服务协议》及相关条款，账号封禁、限制登录等风险由使用者自行承担。
- 本项目不提供、不分发、不维护任何协议驱动的安装包或破解补丁，请自行前往对应官方仓库获取。
- 本项目代码仅供学习交流与个人研究使用，禁止用于商业用途、刷量、骚扰、违法信息传播等场景。
- 因使用本项目导致的任何账号损失、数据泄露、法律纠纷，作者概不负责。

This project is a management panel and AI core only. It does not include any QQ protocol implementation.

- Protocol drivers (SnowLuma, NapCat, LLOneBot, etc.) are third-party open-source projects. This project only bridges them via standard OneBot v11 (HTTP actions + WebSocket event bridge).
- Connecting to QQ using this project may violate Tencent QQ Terms of Service. Account bans, login restrictions and other risks are solely borne by the user.
- This project does not provide, distribute, or maintain any protocol driver packages or patches.
- Code is for educational and personal research only. Commercial use, spam, harassment, or illegal content distribution is prohibited.
- The author assumes no liability for any account loss, data breach, or legal disputes arising from use of this project.

---

## 功能

| 功能 | 说明 |
|------|------|
| AI 对话 | 群聊 + 私聊，支持多模型后端（DeepSeek / OpenAI / Ollama / LM Studio / llama-server 等） |
| 群聊决策 | AI 链式思考 + 多维评分（价值/时机/风险）决定是否发言，支持图片理解 |
| EGO 情绪状态机 | 心情随对话衰减变化，影响回复语气 |
| 超级记忆分层 | 永久/长期/短期记忆 + 自动遗忘，向量检索（LiGGUF 进程内嵌入，llama-server 兜底） |
| 浏览器自动化 | web_operator 插件基于 Playwright 操控真实浏览器（截图/网页操作） |
| 联系人管理 | 群聊/好友统一管理：群角色检测、改群名/名片/禁言/踢人/设撤管理员/全员禁言/成员搜索；好友自动回复开关、备注编辑 |
| 事件自动化 | 加好友/加群/入群申请自动同意（白黑名单+欢迎语）、通知管理员、群管理指令 |
| 可视化 WebUI | 单页管理面板：配置中心/插件/表情包/联系人/通知/Token 统计/协议面板 |
| 隐私安全 | API Key 存 Windows 凭据管理器、前端掩码、CORS 仅本机来源、Host 头防 DNS 重绑定 |

---

## 架构

```
┌─────────────────────────────────────────────────┐
│  WebUI (webui_server.py :8080)  管理面板          │
│         │                                        │
│  bot_core.py  AI 核心 (:3005 事件服务)             │
│         │ HTTP/WS                                │
│  协议适配层 (protocols/)                          │
│   ├── snowluma_adapter → SnowLuma                │
│   ├── napcat_adapter  → NapCat                   │
│   └── llonebot_adapter→ LLOneBot                 │
│         │ OneBot v11 HTTP + WS 事件桥            │
│         ▼                                        │
│   QQ 客户端（QQNT）                                │
└─────────────────────────────────────────────────┘
```

- `bot_core.py` — 机器人核心：消息处理、AI 对话、事件分支、联系人管理 API
- `webui/webui_server.py` — 管理面板 HTTP 服务（单文件 SPA，Chart.js + Glassmorphism CSS）
- `protocols/` — 协议适配层（SnowLuma / NapCat / LLOneBot 驱动，共用 OneBotStdAdapter 基类），OneBot v11 事件 → bot_core
- `plugins/` — 插件体系（群聊决策、记忆、情绪、搜索、截图等）

---

## 依赖的开源项目

| 项目 | 用途 | 仓库 |
|------|------|------|
| SnowLuma | 协议驱动（OneBot v11 实现） | https://github.com/SnowLuma/SnowLuma |
| NapCat | 协议驱动（OneBot v11 实现，注入 QQNT） | https://github.com/NapNeko/NapCatQQ |
| LLOneBot | 备用协议驱动（QQNT 插件型） | https://github.com/LLOneBot/LLOneBot |
| OneBot v11 | 协议标准 | https://github.com/botuniverse/onebot-11 |
| DeepSeek | 云端大模型后端 | https://github.com/deepseek-ai |
| Ollama | 本地大模型运行时 | https://github.com/ollama/ollama |
| llama.cpp | llama-server 本地模型推理 | https://github.com/ggerganov/llama.cpp |
| LiGGUF | 进程内 GGUF 量化推理引擎（超级记忆向量化 DLL） | https://github.com/matrixsmaster/ligguf |
| NexSandglass | 沙漏记忆系统（分层记忆/画像/决策粒子） | https://github.com/...（上游独立仓库） |
| Playwright | 浏览器自动化（web_operator 插件） | https://github.com/microsoft/playwright |
| Electron | 桌面外壳 | https://github.com/electron/electron |
| LM Studio | 本地模型（可选后端） | https://lmstudio.ai |

### 插件接入的三方模型服务

| 服务 | 用途 |
|------|------|
| 硅基流动 SiliconFlow | 群聊决策模型（需注册领免费额度） → https://siliconflow.cn |
| 智谱 BigModel | 模型服务（open.bigmodel.cn） |
| 阿里云百炼 DashScope | 模型服务（dashscope.aliyun.com） |
| DeepSeek | 主对话后端 |

### 协议驱动版本说明

- 三个驱动均走标准 OneBot v11（HTTP 动作 + WS Server 事件桥），EggBloom 不依赖框架私有 API，同一版本族内换机器/换目录可直接沿用。
- SnowLuma 以 `node.exe + index.mjs` 形态运行（运行时目录含 node.exe）；旧 `SnowLuma.exe` / `Lagrange.OneBot.exe` 形态或跨大版本，需确保 `config/onebot.json` 网络结构与 v1.14（HTTP api_port + WS Server Universal）一致，事件桥靠 `ws_port` 配置。
- NapCat 按 QQ 版本发布，不同 QQ 版本需下载对应版本的 NapCat；版本不匹配会导致注入失败。
- 换机器后，在 WebUI「协议」面板点「自动探测」定位各驱动运行时，或「浏览」手动选择。

---

## 发行版本

| 版本 | 内容 | 使用前提 |
|------|------|----------|
| Lite 版 | 源码 + 配置模板 | 需自行安装 Python 3.13 并安装依赖 |
| 标准版 | 嵌入式 Python + 核心依赖 + Electron 壳 + 源码 | 解压即用，无需 Python |
| 完整版 | 标准版 + 表情包 + 向量模型 + 运行时数据 | 解压即用，含完整资源（不公开分发） |

### 标准版快速开始

1. 从 GitHub Releases 下载 `EggBloom-Standard.zip`，解压到非中文/空格路径（如 `C:\EggBloom\`）
2. 双击 `start.bat` 启动
3. 访问 `http://localhost:8080`
4. 首次使用：将 `resources\app\bot_config.json.example` 复制为 `bot_config.json` 并填写，或在 WebUI「设置」页配置

### Lite 版快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/your-repo/EggBloom.git
cd EggBloom

# 2. 安装 Python 3.13（Windows: https://www.python.org/downloads/）
#    勾选 "Add Python to PATH"

# 3. 安装依赖
pip install -e .

# 4. 环境自检（可选，自动修复缺失依赖）
python env_check_tool.py --fix

# 5. 启动
python launch.py
# 或
python webui/webui_server.py
```

### 从源码打包

```bash
pip install pyinstaller
pyinstaller --onefile --console --name EggBloomEnvCheck env_check_tool.py
# 完整打包参考 EggBloom-debug.spec
```

---

## 配置步骤

### 1. 启动 WebUI 管理面板

运行后浏览器打开 `http://localhost:8080`，进入「设置」页面。

### 2. 配置协议驱动（必选）

「协议」面板选择驱动：

- SnowLuma：下载运行时（含 node.exe + index.mjs），填入 `exe_path`，点击「启动」→ 浏览器打开 WebUI :5099 扫码登录
- NapCat：下载对应 QQ 版本 NapCat，填入 `exe_path` 和 `qq_path`（QQNT 路径）
- LLOneBot：QQNT 插件型

### 3. 填写 API Key（必选）

「设置」→「模型配置」：选择 Provider，填入 API Key（写入 Windows 凭据管理器，不落盘明文），选择模型。

### 4. 配置 Bot 基本信息

- `bot_qq`：Bot 的 QQ 号（登录协议驱动后自动获取）
- `target_qqs`：私聊监听的目标 QQ 列表
- `group_ids`：启用群聊功能的群号列表

### 5. 启动 Bot

点击「启动 Bot」。

---

## 主要端口

| 端口 | 服务 | 说明 |
|------|------|------|
| 8080 | WebUI 管理面板 | 核心配置入口 |
| 3005 | bot_core 事件服务 | OneBot 事件入口（HTTP 上报） |
| 3000 / 3001 | SnowLuma / LLOneBot | HTTP API / WS 事件（默认） |
| 3100 / 3101 | NapCat | HTTP API / WebSocket 事件 |
| 5099 | SnowLuma WebUI | 登录引导、账号管理 |
| 6099 | NapCat WebUI | 登录引导、配置管理 |
| 7000 | LLOneBot WebUI | 登录引导 |
| 8081 | llama-server | 本地 LLM 推理 |
| 8083 | llama-server Embedding | 向量嵌入服务 |
| 11434 | Ollama API | 本地模型 |
| 1234 | LM Studio API | 本地模型 |

各驱动端口可在协议面板驱动配置中修改（`protocol_drivers.<key>`）。

---

## 目录结构

```
EggBloom/
├── bot_core.py              # 机器人核心（事件处理）
├── config.py                # 配置加载（凭据管理器优先 + 多数据源回退）
├── data_store.py            # 数据目录管理（Electron userData / 平台标准目录 / 开发模式）
├── logger_setup.py          # 日志轮转（5MB × 5，按模块分文件）
├── protocols/               # 协议适配层
│   ├── onebot_std_adapter.py  # OneBot v11 通用基类
│   ├── snowluma_adapter.py    # SnowLuma 驱动
│   ├── napcat_adapter.py      # NapCat 驱动
│   ├── llonebot_adapter.py    # LLOneBot 驱动
│   ├── ws_client.py           # 纯标准库 WebSocket 客户端
│   └── login_guard.py         # 登录风控护栏（冷却/限流）
├── nexsandglass/            # 沙漏记忆（外部开源项目，vendored 源码副本）
├── webui/
│   ├── webui_server.py      # 管理面板服务（内嵌 SPA）
│   └── webui_artistic_v3.html  # 前端 SPA 源码
├── plugins/                 # 插件体系
│   ├── ego/                 # EGO 核心插件组
│   │   ├── super_memory/    # 超级记忆（分层+向量）
│   │   ├── slang_learner/   # 黑话学习
│   │   └── __init__.py      # 情绪/人格/好感度
│   ├── web_operator/        # 浏览器自动化
│   ├── web_search/          # 网页搜索
│   ├── group_chat_decider/  # 群聊决策
│   └── ...                  # 更多插件
├── memes/                   # 表情包资源（不入库，运行时下载）
├── bot_config.json.example  # 配置模板
├── .env.example             # 环境变量模板
├── setup.ps1                # 环境初始化脚本
├── launch.py                # 启动脚本
├── env_check_tool.py        # 环境自检工具
├── pyproject.toml           # 依赖声明
├── README.md                # 本文档
├── LICENSE                  # MIT 许可证
└── CONTRIBUTING.md          # 贡献指南
```

---

## 技术栈

- Python 3.10+（主逻辑，标准库为主，无第三方 HTTP 框架依赖）
- HTML/CSS/JS 单页应用（Glassmorphism + Chart.js + Vanilla JS）
- OneBot v11 标准协议
- Electron 桌面外壳
- SQLite 本地存储（记忆、好感度、人格等）

---

## 常用工具脚本

| 脚本 | 用途 |
|------|------|
| `setup.ps1` | 环境初始化（需管理员）：写 hosts、装 Python/Node 依赖、查端口、建目录、复制配置模板 |
| `env_check_tool.py` | 环境自检：Python/Node/依赖/端口/磁盘/进程/配置文件，生成报告 |
| `launch.py` | 启动器：自动定位 Electron 运行时与应用目录（开发/打包双模式） |
| `python -m pip install -e .` | 以可编辑模式安装依赖（从 pyproject.toml） |

---

## 环境要求

| 组件 | 版本要求 | 说明 |
|------|----------|------|
| Python | 3.10+（推荐 3.13） | 标准库为主，依赖见 pyproject.toml |
| Node.js | 18+ (LTS) | 运行 SnowLuma 等协议驱动 |
| Windows | 10/11 (x64) | 主要目标平台，含 VC++ 2015-2022 运行时 |
| QQ | QQNT（最新版） | 协议驱动需匹配 QQ 版本 |

---

## 故障排查

| 问题 | 解决方案 |
|------|----------|
| 端口被占用 | 运行 `setup.ps1` 查看端口占用，或在 WebUI 协议面板修改端口 |
| 协议驱动启动失败 | 检查 `exe_path` 是否正确、QQ 版本是否匹配、Node.js 是否安装 |
| API Key 无效 | 确认 Key 正确、余额充足、网络可达 |
| LiGGUF DLL 加载失败 | 安装 VC++ 2015-2022 运行时：https://aka.ms/vs/17/release/vc_redist.x64.exe |
| 配置不生效 | 删除 `bot_config.json` 重新从模板复制，或检查 WebUI「设置」页保存后是否刷新 |
| 打包版配置写入不可见 | 程序目录优先写入，避免 MSIX 沙箱重定向，见 `config.py:save_config` |

---

## 许可证

本项目采用 MIT License，详见 [LICENSE](LICENSE)。

---

## 贡献

详见 [CONTRIBUTING.md](CONTRIBUTING.md)。