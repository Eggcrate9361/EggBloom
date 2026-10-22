# 开源发布清单（GitHub）

> 目标：别人 clone 下来能装、能跑、能看懂；出了问题知道去哪说。
> 最后更新：2026-10-21（上一次 2026-10-18；再上一次 2026-08-01，那份针对旧的 `app-src` 布局，已作废）
> 版本号一律以 `package.json` 的 `version` 为准 —— 本文用 `<version>` 表示，不写死。

---

## 0. 发布前必须先定的事

| # | 事项 | 现状 | 谁定 |
|---|---|---|---|
| 0.1 | **仓库地址 / 用户名** | `Eggcrate9361/EggBloom`（已替换 README / CONTRIBUTING / issue 模板） | ✅ |
| 0.2 | **NexSandglass 的处理** | **源码已彻底移除**（2026-10-19）。沙漏改由自研 `sandglass_lite.py` 实现，数据格式与上游兼容。上游仅作**设计参考**署名（`THIRD_PARTY.md`），不再内嵌分发 | ✅ |
| 0.3 | **LiGGUF DLL 的再分发许可** | README 明写 MIT；已补 `models/LICENSE-ligguf.txt`（随 zip 分发） | ✅ |
| 0.4 | 表情包 / 图标来源 | 混合，部分不明 | 抽查后决定 |

---

## 1. 必须修（P0，不修不建议公开）

### 1.1 许可证冲突

- [x] **NexSandglass**：上游已确认 → <https://github.com/lovevin1314-tech/NexSandglass-Agent-DedicatedMemory>
  （MIT，`Copyright (c) 2026 NexSandglass`）。**处置结论：移除内嵌源码**。
  审计显示原内嵌的 31 个模块（约 7,500 行）中，EggBloom 只用到 6 模块 16 符号；
  现由自研 `sandglass_lite.py` 复刻这四项能力（落沙 / 原文检索 / 按日期回忆 / 决策粒子），
  **数据格式完全兼容**（同一份 `sandglass.txt` / `sandglass.idx`）。
  仓库不再分发上游任何源码 → 无需保留其 LICENSE；`THIRD_PARTY.md` 按「设计参考」署名。
- [x] **LiGGUF**：README 末尾声明 MIT；已在 `models/` 放 `LICENSE-ligguf.txt`，随包分发。
- [ ] `LICENSE` 年份/署名确认：当前 `MIT / Copyright (c) 2026 EggBloom Team`。

### 1.2 隐私

- [x] 地域词已清理（2026-10-18）：旧城市名 → `某市`；天气默认城市从旧值改为 `Beijing`。
- [x] 人名已移出代码：`known_names` 改为配置项，原名单只在本机 `bot_config.json`。
- [x] 真实 QQ / 群号已全部换成占位符（`10000` / `10003` / `20001`）。
- [x] **包级复扫**：三个 zip 的文件名与内容均无真实 QQ / 人名 / 昵称；`conversation_state`、`personality.txt` 均不在包内（2026-10-18 打包后发现 Standard 曾夹带陈旧 v3 与会话状态，已修 sync-dist 并加陈旧文件硬闸）。
- [ ] 复扫一遍（应为 0，天气城市映射表里的通用城市名除外）：
      `git grep -nE "<旧人名A>|<旧人名B>|<旧城市>|<旧BotQQ>|<旧目标QQ>|<旧群号>"`
      —— 具体值不要写进仓库，见你的私有备忘。
- [ ] 确认 `.gitignore` 覆盖：`chat_logs/`、`personality/`、`personality_versions/`、
      `logs/`、`image_cache/`、`self_learning_db/`、`plugins/state/`、`memes` 图库、
      `super_memory/models/`、`slang_*.json`。
- [ ] `git status --porcelain` 里只剩该提交的新文件（见 §3.1）。

### 1.3 历史提交（最容易被忽略）

**当前工作树干净 ≠ 历史干净。** 之前的提交里仍然有真实 QQ 号、人名、地域等信息，
一旦推到 GitHub，`git log -p` 就能翻出来。两条路：

**A. 重写历史（保留 337 个提交）**

```bash
pip install git-filter-repo
# 准备替换表 replacements.txt（值从你的私有备忘里取，别写进仓库）：
#   <旧BotQQ>==>10000
#   <旧目标QQ>==>10003
#   <旧群号>==>20001
#   <旧人名A>==>示例甲
#   <旧人名B>==>示例乙
#   <旧城市>==>某市
git filter-repo --replace-text replacements.txt
git remote add origin https://github.com/YOUR_NAME/EggBloom.git
```

**B. 全新历史（最干净，推荐给第一次开源）**

```bash
git checkout --orphan release
git add -A
git commit -m "Initial public release: EggBloom Desktop 1.5.5"
git branch -M release main
git remote add origin https://github.com/YOUR_NAME/EggBloom.git
git push -u origin main
```

代价是丢掉提交历史；收益是**永远不会有人从历史里翻出你的号**。

### 1.4 占位符

- [x] `README.md`、`CONTRIBUTING.md`、`.github/ISSUE_TEMPLATE/config.yml` 已替换为 `Eggcrate9361`。
- [x] `docs/` 里指向旧路径的说明已清；`docs/` 已从 21 项裁到 6 项（其余移入仓库外归档）。

---

## 2. 应该有（P1，影响第一印象）

- [x] `CHANGELOG.md`（Keep a Changelog 格式）
- [x] `SECURITY.md`（走私密报告，不公开 Issue）
- [x] `.github/ISSUE_TEMPLATE/`（Bug / Feature / 联系方式）
- [x] `.github/PULL_REQUEST_TEMPLATE.md`
- [x] `.gitattributes`（`.bat` 强制 CRLF，二进制不合并）
- [x] `THIRD_PARTY.md`（第三方组件与许可）
- [x] 版本号对齐：`package.json` = `pyproject.toml` = `<version>`（当前 1.5.7，`scripts/activate.py --check-only` 会校验）
- [x] 字体署名：`webui/fonts/README.txt` 补 Fusion Pixel（OFL-1.1）
- [ ] CI：`.github/workflows/ci.yml`
- [ ] 已知失败测试：修掉，或在 CI 里显式排除并开 Issue 记录
- [ ] README 首屏加一句「这不是又一个通用框架」的定位（可选，但建议）

---

## 3. 提交与打包

### 3.1 本次要提交的文件

不要照抄清单（每次都不一样的）。用命令现查，只提交你**确实改过**的东西：

```bash
git status --porcelain          # 未跟踪 + 已修改
git diff --stat                 # 改了哪些行

# 一个版本一次打包一次提交：先把「代码 + 文档」一次性收干净，再改版本号、再打包
```

⚠ 提交前确认 `git status` 里没有 `bot_config.json`、`data/`、`logs/`、
`conversation_state/`、`personality.txt`、`slang_*.json` 这类运行态文件。

### 3.2 顺序（不能反）

```bash
# 1. 版本号（package.json 与 pyproject.toml）→ 2. commit → 3. 打包
git add -A
git commit -m "chore(release): <version>"
python scripts/pack_all.py            # Lite 走 git archive HEAD，不 commit 会缺文件
```

### 3.3 出包后核对

- [ ] `EggBloom-Standard.zip` 根目录有 `EggBloom Desktop.exe`
- [ ] Standard 内 `resources/app/webui/webui_server.py` 存在
- [ ] Standard 内 `.pyc` 数量为 0
- [ ] Lite 文件数 ≈ `git ls-files | wc -l`（+ `VERSION`）
- [ ] 三个包内 `VERSION` 的 `source_commit` == `git rev-parse --short HEAD`
- [ ] 解压 Standard 到干净目录，双击 `EggBloom Desktop.exe` 能拉起后端并打开界面

### 3.4 Release

- [ ] Tag：`v<version>`（与 `package.json` 一致）
- [ ] Release notes 直接取 `CHANGELOG.md` 对应段落
- [ ] 只附 `EggBloom-Standard.zip` + `EggBloom-Lite.zip` + `SHA256SUMS.txt`
      （**Full 不对外分发**：内含 283 张二次创作动漫表情包，版权与内容都不适合上公开仓库）
- [ ] 不要附 `bot_config.json`、`data/`、任何日志

---

## 4. 发布后

- [ ] 置顶 Issue：安装/配置问题请走 Discussions
- [ ] 观察 72 小时：启动失败、依赖缺失、杀软误报
- [ ] 记录第一周的真实反馈到 `docs/`（比 star 有用）

---

## 5. 本机自检命令

```bash
# 隐私（值见私有备忘；下面用变量代替）
git grep -nE "1[0-9]{9}" -- . | grep -v "test_\|docs/" | head
git grep -nE "<旧人名A>|<旧人名B>|<旧城市>" -- .

# 占位符
git grep -n "YOUR_NAME\|your-repo\|TODO" -- README.md CONTRIBUTING.md .github/

# 版本
grep '"version"' package.json; grep '^version' pyproject.toml

# 测试（见 §2 的已知失败项）
python tests/test_persona_tag.py
python tests/test_dev_watch.py
```

---

## 6. 测试现状

> **2026-10-22 全量跑 `tests/test_*.py`：77 个文件 / 451 个 `test_` 函数，全部通过。**
> 不用按测试名逐个比对了 —— **现在可以直接看 exit code**（见下面"三条判据都修好了"）。

改之前是 68 通过 / 9 失败。那 9 个里**只有一个是真的 bug**：

| 类别 | 文件 | 真因 |
|---|---|---|
| 环境缺失（4） | `test_webui_v4_{geometry,nav_sync,overlap,pages}` | Playwright 库装了但**浏览器找得到却没用上**：`import` 成功→跳过分支不触发→`launch()` 抛异常→rc=1 |
| 断言过期（3） | `test_plugin_bridge` / `test_sandglass_dedup` / `test_webui_v4_pages` | 断言描述的是**已被有意改掉**的旧行为 |
| **真 bug（1）** | `test_webui_v4_geometry` | `.stage.pending` 与全局布局类 `.pending` 撞名，导航预演时舞台被压到 **560px** |
| 退出码误判（2） | `test_history_ledger` / `test_search_parsing` | 测试全过，但 `import bot_core` 留下的守护线程抢 stdout 锁 → 退出码 `0xC0000409` |

### 三条判据都修好了

1. **退出码可信了**。`tests/_quiet_exit.py` 在 `import bot_core` 之后停插件线程，
   不再出现「测试全绿、退出码崩溃」。
2. **不用人工比对 summary 了**。上一条修好后，exit code 就等价于"有没有弄坏东西"。
3. **浏览器能找到**。`tests/_pw_chrome.py` 自动扫 `ms-playwright/chromium-*`（取版本最高），
   也认 `PW_CHROME` 显式指定；**真起不来会跳过而不是判红**（CI 上就是这种状态）。

### 三条过期断言改成了什么

不是改字符串，是把断言换成**真正该守的不变量**：

| 测试 | 旧断言 | 新断言 |
|---|---|---|
| `test_plugin_bridge` | 「旧式 `after_reply` 调用行原样存在」 | `after_reply` **一条单数 `execute_hook` 都不许有**，链式恰好 2 处 |
| `test_sandglass_dedup` | Bot 行正文 `== "早上好"` | 带 `[群:123]` 归属标记、且不含旧格式 `[Bot回复]` |
| `test_webui_v4_pages` | 「连接状态 = 运行中」 | 「在线」+ 标注来源（本界面启动 / 外部驱动） |

`test_plugin_bridge` 那条最值得记：旧断言锁的其实是**一个 bug 的写法**。
`execute_hook`（单数）是「首个非 None 即返回」，ego 返回 override 后，
排在后面的 web_search 幻觉拦截永远跑不到，而且日志里看不出来。

### 环境依赖说明（仍然成立）

- `test_image_fetch` / `test_meme_back` 需要 `requests`、`Pillow`
- 4 个 `test_webui_v4_*` 需要 Playwright **且真的能起浏览器**；起不来会 `SKIP` 并 exit 0
- CI（`.github/workflows/ci.yml`）目前只跑 8 个 smoke —— 不是因为有红的，
  而是 CI 上没有 `bot_config.json` / chromium / 插件运行态，扩容要逐个确认

历史记录（2026-10-18 同轮修掉的过期断言）：

- `test_scope_policy_text`：档位文本从 `dynamic_parts` 挪到 `stable_parts` 后，断言仍锁死旧名字。
- `test_prompt_segments`：黄金哈希随「触发词默认值改为 `["小a"]`」更新（group 2306 → 2349 字符，private 不变）。
- `test_memory_feed`：说话人标签统一 `_display_name` 后，解析点从 2 处收敛为 1 处。
