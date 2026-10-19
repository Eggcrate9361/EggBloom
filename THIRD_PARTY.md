# 第三方组件与许可

本项目以 MIT 分发（`Copyright (c) 2026 EggBloom Team`，见 `LICENSE`）。
下面列出随仓库或随发行包分发的第三方内容。

> 截至 2026-10-18，随仓库分发的组件许可均已落实（见下表「状态」列）。
> 表情包图库已决定**不对外分发**（仅本机自用），见 `docs/发布打包.md`。

## 随仓库分发

| 组件 | 用途 | 许可证 | 位置 | 状态 |
|---|---|---|---|---|
| Fusion Pixel Font 12px SC | 中文像素字体 | SIL OFL-1.1 | `webui/fonts/fusion-pixel-12px-sc.*` | ✅ 已署名 |
| Press Start 2P | 显示体 | SIL OFL-1.1 | `webui/fonts/press-start-2p-latin-400.woff2` | ✅ 已署名 |
| Silkscreen | 等宽/标注体 | SIL OFL-1.1 | `webui/fonts/silkscreen-latin-400.woff2` | ✅ 已署名 |
| card-icons | UI 图标 | 自绘 / 自制 | `webui/card-icons/` | ✅ |
| 表情包图库 | 动漫二创表情图 | **来源不明** | `memes/`（git 忽略） | 🚫 **不分发**：仅本机自用 |

> **本仓库已不再内嵌任何第三方源码**（2026-10-19）。历史上曾内嵌 NexSandglass
> 的完整代码树，现已移除，详见下方「设计参考」。

字体版权与来源详见 `webui/fonts/README.txt`。

## 设计参考（**不随仓库分发**）

以下项目的**设计**被 EggBloom 借鉴，但源码不在本仓库内；相关能力由 EggBloom 自行实现。

| 项目 | 借鉴内容 | 上游许可 | EggBloom 的实现 |
|---|---|---|---|
| **NexSandglass**<br><https://github.com/lovevin1314-tech/NexSandglass-Agent-DedicatedMemory> | 沙漏记忆系统的**数据格式与检索思路**：`sandglass.txt` 行格式（`ts \| role \| text`）、倒排索引 `sandglass.idx` 的 token:行号 结构、中文 2-gram + 英文 n-gram 分词规则、密度×信任融合评分公式 | MIT<br>`Copyright (c) 2026 NexSandglass` | `sandglass_lite.py`（仓库根）—— **复刻上述四个被使用的能力**，属重新实现，**非原创设计**；上游源码已不在本仓库 |

**NexSandglass 使用说明（务必如实理解）**

- 2026-10-19 之前，本仓库内嵌其 **v2.9.10** 完整代码树（`nexsandglass/`，31 个模块约 7,500 行，
  含上游 `LICENSE` 原文）。经审计，EggBloom 实际只用到其中 6 个模块、16 个符号
  （落沙写入 / 原文检索 / 按日期回忆 / 决策粒子），其余（MCP Server、TTY Wrapper、
  思维层、偏移率、情绪熵、画像、织布机等约 1,805 行）**从不触发**。
- 现改为**自研薄实现** `sandglass_lite.py`：复刻上述四个真正被使用的能力，
  **数据格式与上游完全兼容** —— 读写同一份 `~/.neurobase/sandglass.txt` / `sandglass.idx`，
  既有数据无需迁移。
- **它是参考设计后的独立实现，不是上游代码的复制**。其中还包含一项上游没有的修正：
  **覆盖度闸**（上游英文 2~4 字符滑窗会产生大量碎片匹配，导致"无命中却返回无关记录"）。
- 因此本仓库**不分发** NexSandglass 的任何源码，也不需要保留其 LICENSE 文件。
  若需使用上游完整功能（如 MCP Server），请从其官方仓库获取。
- 如需回退到上游实现，从 git 历史恢复 `nexsandglass/` 目录即可（该目录在
  commit `4d2446c` 及其之前存在），并设置 `sandglass_bridge.py` 的 `_USE_LITE = False`。

## 随发行包分发（Standard / Full）

| 组件 | 用途 | 许可证 | 说明 |
|---|---|---|---|
| Electron / Chromium | 桌面外壳 | MIT + BSD 系 | 包内自带 `LICENSE.electron.txt`、`LICENSES.chromium.html` |
| Python 3.13 (embedded) | 运行时 | PSF-2.0 | 包内 `python/LICENSE.txt` |
| LiGGUF (`ligguf_embed.dll`) | 进程内 GGUF 推理（bge-small-zh 向量化） | **MIT**（上游 README 声明） | 上游 `matrixsmaster/ligguf` | ✅ 已署名（`models/LICENSE-ligguf.txt`） |
| LiGGUF 中文移植版 | 包内 DLL 的**实际来源**：Windows/MSVC 移植 + 中文分词重写 + fp16 LUT 修复 + F16C 加速 | 衍生作品，随上游 MIT | 本机 `D://LiGGUF-CN`（未入库） | ⚠ **未版本控制**，建议单独建仓 |

> `plugins/ego/super_memory/models/` 已在 `.gitignore` 中，**不会进入 GitHub 源码仓库**，
> 但会进 Standard/Full 的 zip。LiGGUF 的 MIT 声明写在 README 末尾（`## License → MIT`），
> 作者为 Dmitry "sciloaf" Solovyev（MatrixS_Master），2025–2026。
> 二进制再分发时须一并保留 `LICENSE-ligguf.txt`——`pack_all.py` 会随 `models/` 一起带上。
| bge-small-zh-v1.5 (f16 gguf) | 记忆向量化模型 | MIT（BAAI 模型卡） | 45 MB，位于 `plugins/ego/super_memory/models/` |

## 外部依赖（不随包分发）

NapCat / SnowLuma / LLOneBot / OneBot v11 实现、DeepSeek 等云端模型、Ollama / llama.cpp 等本地推理，均由使用者自行安装或配置，遵循各自许可。

## 处置记录（2026-10-19）

| 组件 | 结论 |
|---|---|
| **NexSandglass** | **源码已彻底移除**。自研 `sandglass_lite.py` 替代（数据格式兼容）。仅作**设计参考**署名，不再内嵌、不再分发其代码，故不需要保留其 LICENSE。来源：<https://github.com/lovevin1314-tech/NexSandglass-Agent-DedicatedMemory> |
| LiGGUF | 上游 README 声明 MIT → `models/LICENSE-ligguf.txt` 随包分发 |
| 表情包图库 | 283 张二创动漫图来源不明 → **不随任何包对外分发**（仓库里本就未跟踪） |
| 字体 | Press Start 2P / Silkscreen / Fusion Pixel 均 OFL-1.1，署名见 `webui/fonts/README.txt` |

> 上一轮记录（2026-10-18）：NexSandglass 曾是内嵌上游（v2.9.10 + 桥接层）。
> 2026-10-19 经审计后改为自研薄实现，内嵌代码树已移除。
