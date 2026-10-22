<table bgcolor="#16130F" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td align="center" style="padding:40px 16px 36px 16px">

<img src="assets/readme-hero.png" width="720" alt="EGGBLOOM"><br><br>

<span style="color:#F0EEEA;font-size:15px">一个跑在 QQ 群里的 AI，尽量别像个 AI。</span>

<br><br>

<a href="docs/USAGE.md"><img src="https://img.shields.io/badge/%E9%83%A8%E7%BD%B2%E6%95%99%E7%A8%8B-Setup-C9A96E?style=for-the-badge" alt="部署教程"></a>
<a href="docs/USAGE_en.md"><img src="https://img.shields.io/badge/%E6%96%87%E6%A1%A3-Docs-16130F?style=for-the-badge" alt="文档"></a>
<a href="CHANGELOG.md"><img src="https://img.shields.io/badge/%E5%8F%98%E6%9B%B4-1.5.7-C9A96E?style=for-the-badge" alt="变更日志"></a>

</td></tr></table>

<p align="center">
<img src="https://img.shields.io/badge/Python-3.13%2B-16130F?style=flat-square&logo=python&logoColor=C9A96E" alt="Python 3.13+">
<img src="https://img.shields.io/badge/OneBot-v11-16130F?style=flat-square" alt="OneBot v11">
<img src="https://img.shields.io/badge/Platform-Windows-16130F?style=flat-square&logo=windows&logoColor=C9A96E" alt="Windows">
<img src="https://img.shields.io/badge/License-MIT-C9A96E?style=flat-square" alt="MIT">
</p>

OneBot v11，支持 SnowLuma / NapCat / LLOneBot，模型后端 DeepSeek / OpenAI / Ollama / LM Studio / llama-server。不用装数据库，解压双击就能起。

下面几条是做的过程中反复改的地方，写下来备查。算不上什么方案，群里情况差别太大，能用的不多。

---

## 几个问题，和这里的处理

### 什么时候该说话

有的 bot 每条都回，你发个句号它也接一句；有的整晚不回，你以为它掉线了。

这里是算一个分再决定，另外挂几个时间窗：刚说完五分钟内别人接话不算打断，冒头前先看二十来秒，回完再等一会儿看有没有追问。也区分「引用我说」和「引用别人说」，后者一般轮不到它。

参数是试出来的，不保证在别的群里合适。

### 说话方式

人格写得短、规矩写得多，出来就是客服腔。现在人格 1568 字，占三成七，其余才是规则。

另外几条：模型换行就分条发，一百字以内按标点拆，允许只发一张表情不写字。这些都是写进许可里的，不是漏了。

### 黑话

模型知道 yyds 是什么意思，你问它能解释半天。但让它接话，它写「这确实很有趣」。解释和接话是两回事。

预置了 563 条，格式是「圈层｜一句人话 + 两三个接话」。麻烦的是误伤：子串匹配下「一般」会命中「一般来说」，把别的释义塞进一句普通话，比不注入更糟。这类词都改成「就一般」这样的复合形态，另外拿 15 句普通话当探针，零误报才写进去。

### 插件

遇到两个坑。一是清单里八个插件，实际只有三个接了线，剩下五个你以为在跑，其实没被调用。二是钩子「首个非 None 即返回」，前面的插件一返回，后面的全被静默顶掉，日志里看不出来。

`after_reply` 改成了链式，后一个拿到前一个改完的文本。有测试卡着「一条单数都不许出现」。

AI 生成插件这块也做了，但生成的东西不能自己装：先过 `compile()`，清单用 `ast.literal_eval` 读（不执行），再查一遍有没有 `import bot_core`，最后落在 `_pending/` 目录，扫描器不加载。要人在 WebUI 里点过同意，才移进 `plugins/`。

### 记忆

三层，会遗忘。跨群只自知不串味：它知道自己在 A 群说过什么，A 群的内容不会进 B 群的上下文。目前只做到这个程度，再细的没做。

---

## 快速开始

标准版解压到不含中文空格的路径，双击 `EggBloom Desktop.exe`，浏览器开 `http://localhost:8080`。

源码版：

```bash
git clone https://github.com/Eggcrate9361/EggBloom.git
cd EggBloom && pip install -e . && python webui/webui_server.py
```

首次运行把 `bot_config.json.example` 复制成 `bot_config.json`，填 `bot_qq` 和 `onebot_url`。API Key 在设置页填，存进 Windows 凭据管理器，不落盘。详见 [`docs/USAGE.md`](docs/USAGE.md)。

## 功能

群聊私聊、图片理解、情绪状态、分层记忆、Agent 对话台、MCP 工具、插件热加载、群管理。WebUI 是 v4，十五个导航页，旧版 v3 能切回去对照。

## 依赖

协议驱动 SnowLuma / NapCat / LLOneBot（各自官方仓库），记忆向量化 [LiGGUF](https://github.com/matrixsmaster/ligguf)，自动化 Playwright，外壳 Electron。清单见 [`THIRD_PARTY.md`](THIRD_PARTY.md)。

## 许可

MIT，见 [`LICENSE`](LICENSE)。安全问题走 [SECURITY.md](SECURITY.md)。

## 免责声明

本项目不含任何 QQ 协议实现，也不提供、不分发、不维护协议驱动。接入 QQ 可能违反《腾讯 QQ 用户服务协议》，账号风险自行承担。
