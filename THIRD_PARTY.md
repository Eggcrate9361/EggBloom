# 第三方组件与许可

本项目以 MIT 分发（`Copyright (c) 2026 EggBloom Team`，见 `LICENSE`）。
下面列出随仓库或随发行包分发的第三方内容。

> 截至 2026-10-18，随仓库分发的组件许可均已落实（见下表「状态」列）。
> 表情包图库已决定**不对外分发**（仅本机自用），见 `docs/发布打包.md`。

## 随仓库分发

| 组件 | 用途 | 许可证 | 位置 | 状态 |
|---|---|---|---|---|
| **NexSandglass** | 沙漏记忆系统（分层记忆 / 画像 / 决策粒子） | **MIT**（`Copyright (c) 2026 NexSandglass`） | `nexsandglass/` | ✅ 已声明（`nexsandglass/LICENSE`） |
| Fusion Pixel Font 12px SC | 中文像素字体 | SIL OFL-1.1 | `webui/fonts/fusion-pixel-12px-sc.*` | ✅ 已署名 |
| Press Start 2P | 显示体 | SIL OFL-1.1 | `webui/fonts/press-start-2p-latin-400.woff2` | ✅ 已署名 |
| Silkscreen | 等宽/标注体 | SIL OFL-1.1 | `webui/fonts/silkscreen-latin-400.woff2` | ✅ 已署名 |
| card-icons | UI 图标 | 自绘 / 自制 | `webui/card-icons/` | ✅ |
| 表情包图库 | 动漫二创表情图 | **来源不明** | `memes/`（git 忽略） | 🚫 **不分发**：仅本机自用 |

> NexSandglass 于 2026-08-21 随初始提交进入本仓库（提交者 EggBloom），无第三方版权头、
> 无上游引用。按自有代码处理，用**独立版权行** `Copyright (c) 2026 NexSandglass`
> 单独授权（`nexsandglass/LICENSE`）——与根目录的 `EggBloom Team` 分开，
> 将来它独立成仓库时不用改版权归属。

字体版权与来源详见 `webui/fonts/README.txt`。

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

## 处置记录（2026-10-18）

| 组件 | 结论 |
|---|---|
| NexSandglass | 自有代码 → `nexsandglass/LICENSE`（MIT，`Copyright (c) 2026 NexSandglass`） |
| LiGGUF | 上游 README 声明 MIT → `models/LICENSE-ligguf.txt` 随包分发 |
| 表情包图库 | 283 张二创动漫图来源不明 → **不随任何包对外分发**（仓库里本就未跟踪） |
| 字体 | Press Start 2P / Silkscreen / Fusion Pixel 均 OFL-1.1，署名见 `webui/fonts/README.txt` |
