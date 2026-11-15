# EggBloom Desktop — 标准版

解压即用：内含嵌入式 Python 3.13 + 核心依赖 + Electron 壳，不需要另装 Python。

完整文档见 `resources\app\README.md`；改代码与二次开发见 `resources\app\docs\`。

---

## 快速开始

1. 解压到**非中文、无空格**的路径（如 `C:\EggBloom\`）
2. 双击 `EggBloom Desktop.exe`
   - 桌面壳会用自带 Python 拉起后端（http://localhost:8080）并打开界面
3. 首次使用先配置
   - 把 `resources\app\bot_config.json.example` 复制成 `bot_config.json` 再改
   - 或直接在 WebUI「设置」页填写（自动保存）

遇到环境问题可先跑 `EggBloomEnvCheck.exe` 自检。

---

## 目录结构

```
EggBloom-Standard/
├── EggBloom Desktop.exe      ← 启动入口
├── EggBloomEnvCheck.exe      ← 环境自检
├── python/                   ← 嵌入式 Python 3.13 + 核心依赖
├── locales/  *.dll  *.pak    ← Electron 运行时（勿删）
└── resources/
    └── app/                  ← 应用代码（工作根）
        ├── webui/webui_server.py   后端入口
        ├── bot_core.py             机器人核心
        ├── plugins/                插件
        ├── docs/                   文档
        └── bot_config.json.example 配置模板
```

---

## 首次配置

最低需要填 Bot 的 QQ 号、要工作的群、模型 API Key：

```json
{
  "bot_qq": "你的BotQQ号",
  "group_ids": ["启用功能的群号"],
  "api_key": "你的 API Key"
}
```

API Key 也可以在 WebUI「设置」页填，会写进 Windows 凭据管理器，不落盘明文。

---

## 协议驱动

WebUI「协议」面板选驱动（SnowLuma / NapCat / LLOneBot），填 `exe_path`、`qq_path`
后点「启动」→ 扫码或密码登录。协议驱动是第三方开源项目，需自行获取。

---

## 本版本不包含

- **完整表情包库**（本包带 20 张样本）。完整图库在 Full 版或仓库里
- `bot_config.json` / `.env`（用模板自己生成）
- 聊天记录、数据库、日志（首次运行自动创建）

---

## 常见问题

| 问题 | 处理 |
| --- | --- |
| 提示缺少 `python.exe` | 检查 `python\` 目录是否完整，重新解压 |
| 8080 端口被占用 | 关掉占用进程，或在设置里改端口 |
| 音量控制不可用 | 标准版未含 pycaw / comtypes，属可选功能 |
| 需要 pywin32 | `python\python.exe -m pip install pywin32` |

---

## 免责声明

本项目只含管理面板与 AI 核心逻辑，**不包含 QQ 协议实现**。协议驱动为第三方开源项目，
请自行获取并承担使用风险。仅供学习交流与个人研究使用。