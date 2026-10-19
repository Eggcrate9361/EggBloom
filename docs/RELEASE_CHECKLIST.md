# 开源发布清单（GitHub）

> 目标：别人 clone 下来能装、能跑、能看懂；出了问题知道去哪说。
> 最后更新：2026-10-18（上一次是 2026-08-01，那份针对旧的 `app-src` 布局，已作废）

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
- [x] `docs/` 里指向旧路径的说明已清；`docs/` 已从 21 项裁到 9 项（其余移入仓库外归档）。

---

## 2. 应该有（P1，影响第一印象）

- [x] `CHANGELOG.md`（Keep a Changelog 格式）
- [x] `SECURITY.md`（走私密报告，不公开 Issue）
- [x] `.github/ISSUE_TEMPLATE/`（Bug / Feature / 联系方式）
- [x] `.github/PULL_REQUEST_TEMPLATE.md`
- [x] `.gitattributes`（`.bat` 强制 CRLF，二进制不合并）
- [x] `THIRD_PARTY.md`（第三方组件与许可）
- [x] 版本号对齐：`package.json` 1.5.5 = `pyproject.toml` 1.5.5
- [x] 字体署名：`webui/fonts/README.txt` 补 Fusion Pixel（OFL-1.1）
- [ ] CI：`.github/workflows/ci.yml`
- [ ] 已知失败测试：修掉，或在 CI 里显式排除并开 Issue 记录
- [ ] README 首屏加一句「这不是又一个通用框架」的定位（可选，但建议）

---

## 3. 提交与打包

### 3.1 当前未跟踪、需要提交的文件

```
.gitattributes
.github/                     (CI + issue/PR 模板)
CHANGELOG.md
SECURITY.md
THIRD_PARTY.md
persona_tag.py
scripts/dev_watch.py
scripts/note_crypt.py
tests/test_dev_watch.py
tests/test_persona_tag.py
```

另有 54 个已跟踪文件被修改（隐私占位符、文档、测试断言、打包脚本等）。

### 3.2 顺序（不能反）

```bash
# 1. 版本号（package.json）→ 2. commit → 3. 打包
git add -A
git commit -m "chore(release): 1.5.5"
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

- [ ] Tag：`v1.5.5`（与 `package.json` 一致）
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

> 2026-10-18 全量跑 `tests/test_*.py`（68 个）：

- **65 个通过**
- **3 个按环境跳过**（缺可选依赖，不报错）：
  - `test_image_fetch` —— 需要 `requests`
  - `test_meme_back` —— 需要 `Pillow`
  - `test_webui_v4_overlap` —— 需要 `playwright` + chromium
- CI 会装 `requests` + `Pillow`，所以前两个在 CI 里真跑；`playwright` 测试在 CI 里跳过。

同轮修掉的过期断言：

- `test_scope_policy_text`：档位文本从 `dynamic_parts` 挪到 `stable_parts` 后，断言仍锁死旧名字。
- `test_prompt_segments`：黄金哈希随「触发词默认值改为 `["小a"]`」更新（group 2306 → 2349 字符，private 不变）。
- `test_memory_feed`：说话人标签统一 `_display_name` 后，解析点从 2 处收敛为 1 处。
