# EggBloom 可移植目录结构说明

本程序采用**单一工作根**设计：所有业务代码、插件、表情包、配置和数据都集中在
`resources\app\` 这一个目录内。整个文件夹可以**复制到任意路径、任意电脑**直接运行，
不依赖绝对路径，也不用安装任何额外环境。

---

## 目录结构

```
EggBloom Desktop（整个文件夹可整体复制）
│
├── EggBloom Desktop.exe        ← 主程序（Electron 壳），双击启动
├── start.bat                   ← 源码启动（自动起 WebUI + 界面）
├── start-source.bat            ← 源码启动（仅起后端 + 浏览器）
├── start-waterstar.bat         ← 源码启动（仅起后端 + 浏览器）
│
├── resources\
│   └── app\                    ★ 唯一的工作根（所有东西都在这）
│       ├── webui\webui_server.py   后端服务（入口）
│       ├── bot_core.py             机器人核心
│       ├── plugins\                插件目录（改这里！）
│       ├── memes\memes\            表情包
│       ├── bot_config.json         主配置
│       ├── .env                    环境变量（API 地址等）
│       ├── personality.txt         性格预设
│       ├── slang_dict.json         梗词典
│       ├── chat_logs\              聊天记录
│       ├── self_learning_db\       自主学习库
│       └── ...                     其余运行文件
│
└── locales\  python\   *.dll 等     Electron + Python 运行时（勿动）
```

---

## 启动方式（任选其一）

1. **双击** `EggBloom Desktop.exe`（推荐，带界面）
2. 运行 `start.bat`（自动起后端 + 打开界面，含依赖检查）
3. 运行 `start-source.bat` / `start-waterstar.bat`（仅起后端，浏览器访问 `http://localhost:8080`）

> 无论哪种方式，后端都从 `resources\app\webui\webui_server.py` 启动，
> 机器人（bot_core）与 WebUI 统一以 `resources\app` 为工作根。

---

## 修改插件改哪里？

**只改 `resources\app\plugins\` 这一个位置。**

- 新增插件：在 `resources\app\plugins\<插件名>\` 下创建，并在 `__init__.py` 里按标准声明
  （`PLUGIN_ACTIONS` 或 `execute_action`），WebUI 会自动识别，**无需改主程序**。
- 修改已有插件：直接改 `resources\app\plugins\<插件名>\` 下的文件。

不要在其他位置重复存放插件副本——本目录只有一份真实文件。

---

## 复制 / 迁移 / 分发给他人

- 整个文件夹**复制粘贴**即可，无需安装。
- 请**复制整个文件夹**（含 `resources\`、exe、dll），不要只拷某一个子目录。
- 目标电脑需已安装 **Python 3.10+**（若使用 exe 则无需额外安装）。
- 首次在新机器运行，若提示缺少依赖，运行 `start.bat` 会自动创建虚拟环境并安装
  `resources\app\requirements.txt` 中的依赖。

---

## 注意事项

- **不要删除** 根目录的 `*.dll`、`*.pak`、`locales\`、`python\` —— 这些是 Electron/Python
  运行时，删了程序无法启动。
- 配置（bot_config.json / .env）**以程序目录 `resources\app\` 为准**，不是 AppData：
  - **读取**：程序目录 `bot_config.json` → 数据目录（`%LOCALAPPDATA%\EggBloom_v3`）
    **仅「补缺」——字段已存在就完全不动** → `.env` → 代码内置默认值。
  - **写入**：优先写程序目录；程序目录不可写（只读介质 / 权限不足）才回退数据目录。
  - 完整优先级图见 `docs\CONFIG_PRIORITY.md`，实现见 `config.py` 的 `load_config` / `save_config`。
  - API Key 由 Windows 凭据管理器安全保存，不写入明文配置。
- 若想彻底重置某台机器：要**同时**清掉两份配置——程序目录的
  `resources\app\bot_config.json` 和数据目录的 `%LOCALAPPDATA%\EggBloom_v3\bot_config.json`。
  只删其中一份的话，另一份仍会生效（这正是"删了配置却还是老样子"的原因）。
- 想连记忆一起重置，另外再删 `resources\app\.neurobase\`（见下一节，删前务必确认不再需要）。

---

## NeuroBase 记忆库的位置（2026-09-27 起改为可移植）

**NexSandglass（沙漏记忆）的全部数据现在也放在本目录内**：

```
resources\app\.neurobase\
├── sandglass.txt              全量消息落沙
├── sandglass.db / .idx        结构化消息 + 全文索引
├── shadow_sand.db             影子灵魂
├── decision_particles.txt     决策粒子（他做过的选择）
├── emotion_vocab.json         情绪词表（静态配置）
├── persona\                   画像（由 persona_l3 生成）
├── scripts\                   vault 脚本
└── archive\                   归档
```

**复制 / 迁移时必须把这个目录一起带上** —— 它存的是机器人的长期记忆，
丢了就是失忆。

### 路径解析顺序（`nexsandglass\sandglass_paths.py`）

1. 环境变量 `NEXSANDBASE_HOME`（显式指定，最优先）
2. 本目录旁的 `resources\app\.neurobase`（存在即用 —— **推荐的可移植布局**）
3. `~\.neurobase`（旧版本的数据还在这儿，继续可用）
4. 都没有 → 新建在第 2 项的位置；若程序目录不可写（只读介质）则退回第 3 项

> **历史数据**：2026-09-27 之前数据放在 `~\.neurobase`。已迁移到上面的可移植位置。
>
> ⚠️ **旧目录 `C:\Users\<用户>\.neurobase` 是一份旧快照**（其 sandglass.db 停在 08-10，
> 比本目录少约 170 条、且缺 08-11 ~ 09-05 的记录）——
> **千万不要拿它覆盖本目录**，那等于让机器人失忆一段时间。
> 确认机器人运行正常后可整个删除；如果想保险，改名成 `.neurobase.bak-20260927` 留着。

### 备份建议

整个 `resources\app\` 拷一份就是完整备份（程序 + 配置 + 记忆都在里面）。
如果只想备份记忆，单独拷 `resources\app\.neurobase\` 一个目录即可。
也可以用内置命令导出记忆包：`python nexsandglass\sandglass_think.py export`（tar.gz）。