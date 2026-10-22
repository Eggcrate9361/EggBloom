# EggBloom Desktop 使用说明

> 面向使用者：怎么装、怎么配、每一页是干什么的、出问题先看哪里。
> 开发相关见 [`PLUGIN_DEV.md`](PLUGIN_DEV.md)、打包见 [`发布打包.md`](发布打包.md)。

## 1. 这是什么

跑在 QQ 群/私聊里的 AI 助手，带一个本地 Web 控制台。

- 协议：OneBot v11（SnowLuma / NapCat / LLOneBot 等，任选其一）
- 模型：DeepSeek / OpenAI / Ollama / LM Studio / llama-server，以及 14 个预置云厂商
- 形态：Electron 桌面壳 + **独立 Python 后端**（两个进程，别混淆，见 §7）

**本仓库不含任何 QQ 协议实现**，也不分发协议驱动。接入 QQ 可能违反《腾讯 QQ 用户服务协议》，
风险自负。

## 2. 环境要求

| 场景 | 要求 |
|---|---|
| 发行版（Standard / Full） | Windows 10/11，解压即用，**自带 Python 运行时** |
| 源码运行（Lite） | Python **3.13**（`requires-python >=3.13,<3.14`） |
| 协议驱动 | 单独安装并开启 OneBot v11 的 HTTP/WS 服务 |
| 构建/打包 | Node.js（Electron 外壳）+ 7z（打包脚本用） |

## 3. 启动

**A. 发行版（推荐）**

1. 解压 `EggBloom-Standard.zip`，得到一个完整目录（zip 内顶层就是同名文件夹）
2. 双击 `EggBloom Desktop.exe`
3. 桌面壳会拉起 Python 后端并打开界面；也可手动访问 `http://localhost:8080`

**B. 源码**

```bash
git clone https://github.com/Eggcrate9361/EggBloom.git
cd EggBloom
pip install -e .
python webui/webui_server.py
```

**C. 发行包里的 `start.bat`**：等价于 B，多做了环境检查（源码库里没有这个文件）。

## 4. 首次配置

**没有自动弹出的配置向导**，首次运行要自己做两件事：

1. 把 `bot_config.json.example` 复制成 `bot_config.json`（不放也能起，走的是内置默认值）
2. 打开 WebUI 的「配置」页，在**模型设置区域**点**配置向导**按钮
   （和「测试连接」「清除当前提供商 Key」并排，位于页面中下部，首屏需要往下滚才看到），
   三步填完 LLM 部分

| 向导步骤 | 字段 | 说明 |
|---|---|---|
| 1 | AI 供应商 | DeepSeek / OpenAI / Ollama / 其它（共 14 个预置） |
| 2 | API 地址 + 密钥 | 密钥**写入 Windows 凭据管理器**，不落盘明文；走中转/自建服务时改地址 |
| 3 | 模型名 | 决定上下文窗口，见 §6.4。填完可当场测试连接 |

QQ 号和协议地址**不在这个向导里**，在「协议」页单独填：驱动端先开好 WS 服务，
再把地址填进去。QQ 路径可选，填了才能从控制台拉起 QQ 客户端。

向导随时能重开（还是那个按钮），跳过它直接在「配置」页逐项填也行。

## 5. 控制台 15 页速查

| 页 | 用途 |
|---|---|
| 仪表盘 | 总览：运行状态、消息量、今日 token |
| 协议 | 连接与驱动：驱动状态、启停、日志 |
| 群管理 | 群列表与开关：白名单、群名片、禁言/踢人 |
| 表情包 | 素材库：浏览、启用/停用、删除 |
| 生图 | ComfyUI 出图 |
| 群聊行为 | 说话时机：接话窗口、潜水观察、发言评分 |
| Token | 用量与预算：累计消耗、缓存命中、上下文压力 |
| 配置中心 | 参数：API、行为、监听对象、功能开关 |
| Bot | 账号与状态 |
| 日志 | 会话记录：可搜索、可筛选 |
| 通知 | 监控：异常与告警 |
| 控制台 | 底层命令入口 |
| 记忆 | 沙漏与本体：分层记忆、按日期回忆 |
| 插件 | 扩展：热加载、启停、自造插件审批 |
| Agent | 对话与轨迹：会话列表、流式执行、工具调用回放 |

> 旧版 v3 界面仍保留，可一键切换用于对照；`ui_version.txt` 控制默认版本，
> 发行包里刻意不带这个文件。

## 6. 常用操作

### 6.1 换模型 / 换供应商

「配置中心」改 `api_provider` + `model_name`。API Key 按供应商分别存凭据管理器，
切换时不会误用上一家的 Key。

### 6.2 调群聊的“话痨程度”

「群聊行为」页调评分参数与接话窗口；不确定就先只改接话窗口，改动最容易观察。

### 6.3 看用量

「Token」页给两类数据：**累计**（今天花了多少）和**上下文压力**（这一轮有多满）。
压力是双轨判定——

- 窗口轨：占模型窗口 60% 提示、85% 高危；
- 成本轨：单轮 prompt 到 32k 提示、128k 高危。

之所以要成本轨：1M 窗口下按比例算要 600k token 才报警，那时单轮成本早已不可接受。

### 6.4 模型窗口

窗口由 `model_caps.py` 按模型名解析（`deepseek-flash` → 1M），也可用环境变量
`EGGBLOOM_CTX_WINDOW` 强制覆盖。它同时决定工具结果预算和上下文闸门，不用手调。

### 6.5 插件

「插件」页浏览/启停。AI 自造插件先落 `plugins/_pending/`（下划线前缀，扫描器不加载），
管理员收到通知并在 Agent 页审批后才移入 `plugins/` 热加载。

## 7. 数据、日志与“改了代码为什么不生效”

**位置**

| 内容 | 路径 |
|---|---|
| 配置 | `bot_config.json`（程序目录优先，`%LOCALAPPDATA%\EggBloom_v3` 兜底） |
| 运行日志 | `%LOCALAPPDATA%\EggBloom_v3\qq_bot.log` |
| token 统计 | 程序目录 `logs/token_stats.jsonl` |
| API Key | Windows 凭据管理器（`cred_store.py`） |
| 记忆 | 沙漏：`NEXSANDBASE_HOME` → `<程序目录>/.neurobase` → `~/.neurobase`（`sandglass.txt` / `sandglass.idx`） |

**改了源码要两步生效**，漏一步就静默失败：

```
源码库  --scripts/sync-dist.ps1-->  发行目录 resources\app
                                        │
                                        └── 重启 Python 后端进程（必须）
```

- Electron 只是浏览器壳，**不持有业务逻辑**；只重开壳不会加载新代码
- 同步用 robocopy `/E`（只增不删），显式传 `-DistPath "发行根目录"`
- 不需要清 `__pycache__`：源码 mtime 变了 Python 会自动重编译
- 一条命令走完并校验：`python scripts/activate.py --dist "<发行根>"`

## 8. 常见问题

**8080 打不开**
后端 python 进程没起来。看 `qq_bot.log` 末尾；Electron 壳在等它。

**改了代码没反应**
见 §7，九成是没重启后端。

**报 400 上下文超限**
上游回 400，这类错误**不重试**（`harness.retry_policy` 把 4xx 判为不可恢复），
所以表现是整条回复丢失。常见原因：人格文件过大、单轮塞了多张大图、工具链把上下文堆满。
工具链现在有总量闸会在逼近上限时强制收尾，但人格与图片体积仍需自行控制。

**协议连不上**
先确认驱动端的 WS 服务已开、地址端口对得上；「协议」页有每一类驱动的状态与日志。

**换电脑 / 重装**
带上 `bot_config.json` 与 `%LOCALAPPDATA%\EggBloom_v3`；API Key 在凭据管理器里，需要重填。

## 9. 目录速览

```
EggBloom/
├── bot_core.py              机器人核心（消息、AI 对话、事件分支）
├── model_caps.py            模型窗口/输出上限的唯一来源
├── harness/                 Agent 框架（事件总线、工具注册、预算、MCP）
├── plugins/                 插件（热加载）
├── protocols/               OneBot v11 适配层
├── webui/                   控制台前端 + HTTP 服务
├── sandglass_lite.py        沙漏记忆引擎
└── scripts/                 同步、打包、校验、开发工具
```
