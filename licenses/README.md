# 随发行包分发的第三方许可正文

`plugins/ego/super_memory/models/` 被 `.gitignore` 排除，二进制只进 Standard/Full 的 zip，
不进 GitHub 源码仓库。为了让**源码仓库里也有一份可读的许可正文**，把相关文本放在这里。

| 文件 | 对应组件 | 来源 |
|---|---|---|
| `LiGGUF.txt` | `ligguf_embed.dll` | 中文移植 <https://github.com/Eggcrate9361/LiGGUF-CN>；上游 <https://github.com/matrixsmaster/ligguf>。正文逐字取自移植仓库的 `LICENSE` |
| `bge-small-zh-v1.5.txt` | `bge-small-zh-v1.5` f16 GGUF | 模型卡 <https://huggingface.co/BAAI/bge-small-zh-v1.5>（`license: mit`） |

说明：

- 上游 LiGGUF 仓库没有 LICENSE 文件，README 的 `## License` 只写 `MIT`；中文移植仓库补了
  MIT 全文与上游版权行，`LiGGUF.txt` 用的是移植仓库那一份。
- `LiGGUF.txt` 的版权行是 `Copyright (c) 2026 EggBloom Team`，与移植仓库现状一致；
  EggBloom 主仓库的版权行已改成 `Eggcrate9361`。两处是否统一，由维护者决定。
- bge 的版权行取自模型卡的作者字段。
- 包内是 f16 GGUF 转换件，**转换来源与转换命令尚未记录**；补上之后再改本文件。
- 出包时 `licenses/` 会由 `scripts/pack_all.py` 复制到包根，并在压缩前校验。
