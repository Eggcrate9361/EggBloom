# 开源发布清单（GitHub）

> 目标：别人 clone 下来能装、能跑、能看懂；出了问题知道去哪说。
> 最后更新：2026-11-15（出站动作全补并重发 1.6.0 附件；上一次 2026-11-13）
> 版本号一律以 `package.json` 的 `version` 为准 —— 本文用 `<version>` 表示，不写死。
>
> **1.6.0 状态**：本次计划转公开并正式宣传，物料见 `docs/RELEASE_NOTES_v1.6.0.md`
> 与 `docs/promo-1.6.0.html`；全量 368 条用例；`scripts/check_secrets.py` 现在扫
> 「已跟踪 + 未跟踪未忽略」共 873 个文件，干净。**§1.3 历史隐私清理已收口**
> （orphan 重建公开历史，见该节执行记录），打包前仍需确认工作区干净
> （`pack_all.py --dry-run` 会报「未提交改动会让包内 source_commit 对不上」）。

---

## 0. 发布前必须先定的事

| # | 事项 | 现状 | 谁定 |
|---|---|---|---|
| 0.1 | **仓库地址 / 用户名** | `Eggcrate9361/EggBloom`（已替换 README / CONTRIBUTING / issue 模板） | ✅ |
| 0.2 | **NexSandglass 的处理** | **源码已彻底移除**（2026-10-19）。沙漏改由自研 `sandglass_lite.py` 实现，数据格式与上游兼容。上游仅作**设计参考**署名（`THIRD_PARTY.md`），不再内嵌分发 | ✅ |
| 0.3 | **LiGGUF DLL 的再分发许可** | 上游 README 只写 MIT、仓库无 LICENSE 文件；中文移植仓库 <https://github.com/Eggcrate9361/LiGGUF-CN> 已公开并带 LICENSE 全文，正文复制到 `licenses/LiGGUF.txt`，`pack_all.py` 压缩前校验 | ✅ |
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
- [x] **LiGGUF**：上游 README 末尾声明 MIT（无 LICENSE 文件）；移植仓库 `Eggcrate9361/LiGGUF-CN` 带 MIT 全文，正文见 `licenses/LiGGUF.txt`，随包分发。
- [x] `LICENSE` 年份/署名确认：已改为 `MIT / Copyright (c) 2026 Eggcrate9361`。

### 1.2 隐私

- [x] 地域词已清理（2026-10-18）：旧城市名 → `某市`；天气默认城市从旧值改为 `Beijing`。
- [x] 人名已移出代码：`known_names` 改为配置项，原名单只在本机 `bot_config.json`。
- [x] 真实 QQ / 群号已全部换成占位符（`10000` / `10003` / `20001`）。
- [x] **包级复扫**：三个 zip 的文件名与内容均无真实 QQ / 人名 / 昵称；`conversation_state`、`personality.txt` 均不在包内（2026-10-18 打包后发现 Standard 曾夹带陈旧 v3 与会话状态，已修 sync-dist 并加陈旧文件硬闸）。
- [ ] 复扫一遍（应为 0，天气城市映射表里的通用城市名除外）：
      `git grep -nE "<旧人名A>|<旧人名B>|<旧城市>|<旧BotQQ>|<旧目标QQ>|<旧群号>"`
      —— 具体值不要写进仓库，见你的私有备忘。
- [x] **1.6.0 身份号盲区（2026-11-09）**：`scripts/check_secrets.py` 的「11 位数字」模式
      只认 `1[3-9]` 开头的手机号，**9–10 位 QQ / 群号一律不报**。
      全仓精确复扫（含未跟踪文件共 859 个，排除二进制与 `dist/`）得到
      **66 个唯一 9–11 位数字值 / 约 639 处出现**；按值分类后 6 组待判断，
      其中 C1–C5 判定为真号、C6 为通用占位保留。
      **5 组已按「位数一致」替换为合成号**：43 文件 / 223 处 / 211 行。
      验证：5 个原值全仓 0 命中、43 个备份逐字节重放零差异、
      全量 352/352、`evals --offline` 40/40。底稿见本地计划目录
      `1.6.0-发布准备-身份号对照表.md` / `1.6.0-发布准备-真号替换表.md`（不进 git）。
      以上是 11-09 当时的统计；11-13 实际提交的脱敏笔为 **37 文件 / 227 处**
      （差额来自分批：有 2 个文件的功能改动与脱敏混在一起，归到功能笔提交）。
- [x] **扫描器改造（2026-11-13 完成）**：没动正则（放宽会一次报 639 处）。改的是
      取材口径 —— `check_secrets.py` 从 `git ls-files`（只列 tracked）改成
      `ls-files --cached --others --exclude-standard`，覆盖「`git add -A` 会收走的
      全部文件」。新增文件在首次 add 之前原本扫不到，闸只守着旧文件。
- [ ] **昵称 / 人名 / 转述叙述**：数字替换不覆盖文本身份词，
      残留候选见 `1.6.0-发布准备-文本身份残留清单.txt`，转公开前需逐条判断。
- [x] **基础设施标识（2026-11-15）**：本文件原先带着一条到某台服务器的 `ssh://` 地址
      （用户名、公网 IP、仓库路径都写全了），从 11-13 建根那次起
      **36/36 个提交**都带着它。原来的判据只认密钥、身份号、`C:\Users\…`，
      `ssh://` / 公网 IPv4 / POSIX 家目录**都不在范围内** —— 三道闸全绿。
      现在 `check_secrets.py` 加了独立一组 `INFRA_PATTERNS`
      （`ssh://` 形式、`user@IP`、公网 IPv4、POSIX 家目录、`root` 家目录）：
      私网、回环与文档示例段不算泄漏，带 UA 标记的行跳过 IPv4 判定
      （`Chrome/131.0.0.0` 是版本号）。判据自检：
      `python scripts/check_secrets.py --selftest`（11 条夹具）。
- [x] **历史复查**：当前树干净 ≠ 历史干净。每条要清的地址都得单独查一遍：
      `git log --all -S "<地址片段>" --oneline | wc -l`
      —— 命中数等于还带着它的提交数，只清 HEAD 没有意义。
      **2026-11-15 起有闸了**：`python scripts/check_history.py` 扫本地所有非归档 ref
      的 blob + 提交信息 + tag 附注，与工作树闸共用同一个 `scan_text()`。
      归档 ref（`legacy/` `backup/` `public/`）默认跳过，`--include-archive` 才扫 ——
      它们本来就不推出去。实测：`main` + `v1.6.0` 干净，加 `--include-archive`
      时四条归档 ref 里的旧地址一条不差全抓出来（24 处）。
- [x] **发行包闸（2026-11-15）**：`pack_all.py` 替换 `.zip.new` 之前调
      `check_package.scan_zip()`，不过闸就不替换。五条判据（内容 / 结构 / 溯源 /
      来源 / 文件名）见 `docs/发布打包.md` §5.1。三档包复验：
      `python scripts/check_package.py --dist "E:\EggBloom水星\dist"` → 0 不合格。
- [x] **四个面一条命令（2026-11-15）**：
      `python scripts/prepublish.py --all --dist "E:\EggBloom水星\dist" --strict`
      跑工作树 / 历史 / 发行包 / Release 元数据（正文、标题、附件名、仓库描述）。
      **退出码 2 = 有面没验成，不算通过** —— 「没扫」和「干净」必须能分开，
      这是 11-13 那次假绿的根因。
- [ ] 确认 `.gitignore` 覆盖：`chat_logs/`、`personality/`、`personality_versions/`、
      `logs/`、`image_cache/`、`self_learning_db/`、`plugins/state/`、`memes` 图库、
      `super_memory/models/`、`slang_*.json`。
- [ ] `git status --porcelain` 里只剩该提交的新文件（见 §3.1）。
- [ ] 钩子与源一致（新克隆/换机器后必做）：`python scripts/install_git_hooks.py --check`
- [ ] 提交规范闸自检：`python tests/test_commit_format.py` 与
      `python tools/commit_lint.py --check-policy` 均通过。

### 1.3 历史提交（最容易被忽略）

> **⚠ 转公开的硬阻塞（2026-11-09 发现，2026-11-13 换判据复查）**：
> `scripts/check_secrets.py` 只扫**当前工作树**，管不到历史；`git log` 一旦公开
> 就能翻出历史里的真实 QQ / 昵称 / 转述叙述。
>
> 11-13 复查（`提交历史体检-20261113.md`，679 个提交逐条读 subject + 正文）
> 的结论比 11-09 那次更硬：**问题不只在 diff 里，提交信息本身就有** ——
> 42 个提交信息直接写真实身份号（40 个已 push），6 个活跃期提交正文写转述，
> 86 个提交信息出现昵称。这些不需要 `git log -p`，`git log` 一眼就看见。
>
> | 口径 | 提交数（切换前） | 说明 |
> | --- | --- | --- |
> | `main` | 531 | 默认 push 会公开的 |
> | `origin/main` | 450 | 已 push，重写要强推 |
> | 全部 refs | 682 | 含 3 个本地 backup 分支 + `server/*` |
>
> 切换后 `main` 只有 1 个提交，旧 531 个在 `legacy/main`（本地，不推）。
> 发布时禁止 `git push --all` / `git push --mirror`，否则会把备份分支和
> `legacy/main` 一起推上去。**（2026-11-14：这些 ref 已删除，禁令改由
> `.githooks/pre-push` 按提交判定，见本节末尾的补记。）**
> **已选 B（2026-11-13 执行，见本节 B 段的三次重建记录）**；A 方案已评估否决。
> 验收判据只有一条，别用"我检查过了"代替：
>
> ```bash
> python tools/commit_lint.py --publish   # 公开判据（含冻结提交），必须 0 处
> ```
>
> 这条命令管的是**提交信息**，不管文件内容 —— 它和 `check_secrets.py` 是互补，
> 不是包含关系：`--publish` 读 `git log` 的 message（转述、真实号、昵称），
> `check_secrets` 读当前工作树（路径、密钥）。**两者都不查文件内容里的 QQ 号**，
> 树内容要另验：`git grep -nE "<已知真号>" <ref>` 逐个 ref 跑一遍。
> 别用 `--strict` 代替 `--publish` —— 那个只判基线之后的提交，把活跃窗口的 9 处
> 清理掉就报「通过」，而冻结部分仍在 `git log` 里。切换前实测：`--strict` 9 处 /
> 7 提交，`--publish` 387 处 / 涉及 226 个提交；切换后两者均 0 处（扫 1 个提交）。
> 那 7 个提交**全部已推**（6 个在 `origin`，`b250cbf` 在 `server`），单改任一处
> 要重排其后 57~101 个提交并强推两个 remote；且改完 `--publish` 仍是 387 处。
> 所以处置单位是**整条历史线**（B 方案），不是这 7 个提交。

**A. 重写历史（保留 `main` 的 531 个提交）**

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
# origin 已指向 Eggcrate9361/EggBloom.git，不要重复 remote add
# 重写后只推 main：git push --force-with-lease origin main（禁止 --all / --mirror）
```

**A 方案 2026-11-13 复查后降级为"不推荐"**：`--replace-text` 是逐字替换，
能处理号码，处理不了 42 条 message 里"号码 + 语境"（`私聊 <号>，把我的事记到
另一个人头上` 换掉号还剩叙述），也处理不了 6 条转述。手工改 message 则要动
531 个提交的 hash。试过一版正则改写器，实测改坏 185 条（cherry-pick 记录里的
commit sha、`mismatch_note() 里「对方的时段」` 都会被误伤），所以没有采用。

**B. 全新历史（已执行 2026-11-13，新根 `fc3cdc1`）**

```bash
python tools/public_history.py --plan     # 先看：树是否与 HEAD 一致、工作区是否干净
python tools/public_history.py --apply    # 只写 refs/heads/public/main，不动 main
python tools/public_history.py --switch   # main 指过去，旧链留在 legacy/main
git push --force-with-lease origin main   # 只推 main
```

脚本做的事：用 `HEAD` 的树建一个无父提交（`commit-tree`），message 过闸，
校验 tree hash 与**建根时 HEAD** 逐字节相等、提交数为 1；工作区不干净时直接拒绝。
旧链原样保留在 `legacy/main` + 3 个 `backup/*` 里，一个 ref 都不删。

**执行记录（2026-11-13，第三次重建后定稿）**：

| 项 | 值 |
| --- | --- |
| 新根 | `fc3cdc1`，1 个提交 |
| 旧链 | `legacy/main` = `8508e29`，531 个提交 |
| 树 hash | 新根 = `d02bc02a`，等于建根时 HEAD 的树（代码零变化） |
| 与 `legacy/main` 的 diff | **不为空**（86 文件）—— 根含脱敏与功能改动，旧链是改动前状态 |
| `--strict` / `--publish` | 均 0 处（扫 1 个提交） |
| 旧链可审计性 | `--rev-range legacy/main --publish` 报 387 处 / 226 提交 |

> **2026-11-14 补：上表里的旧链已经不在本仓库了。** `legacy/main`、三条 `backup/*`、
> 四个指向旧提交的 tag、以及 `server/*` 的远端跟踪 ref 全部删除，删除前的全量归档是
> `D:\_archive\EggBloom-allrefs-20261114.bundle`（12 MB，`git bundle verify` 可校验）。
> 上表这些数字（531 提交、387 处、226 提交）是当时的实测值，留着当记录；要复现得先
> 从那个 bundle 展开成临时 clone。
>
> 删的理由不是「看着碍眼」：公开仓库上曾经挂着 `v1.5.8` / `v1.5.10` / `v1.5.11`
> 三个标签指向旧链提交（25 / 9 / 19 个文件里有真号），而当时的推送闸只按 ref 名
> 拦 `refs/heads/*`，标签是敞开的。留着这些 ref，出问题的概率取决于「记得别推」，
> 而不是取决于闸 —— 11-14 已经证明「记得」不管用。
>
> **同日稍晚补：版本号那七个 tag 已经重建，但指向变了。** `v1.5.5`…`v1.5.11` 全部
> 指向新链的根 `3832b6b`，`v1.6.0` 指向 `main` HEAD。这是保留**版本名**，不是保留
> 版本状态 —— 1.5.x 的代码在存活的提交里复现不出来。`ui-pre-t8` 没有重建。
> 详见 `CONTRIBUTING.md` §6.8「标签重建」。
>
> **同日稍晚补：发布页清空。** GitHub 上原有 **7 条** Release，全部删除，Releases 页
> 现在是空的：
>
> | 版本 | 状态 | 附件数 | 删前是否备份 |
> |---|---|---|---|
> | `v1.5.11` / `v1.5.10` / `v1.5.8` | draft | 5 / 3 / 3 | `v1.5.10`、`v1.5.8` 有；`v1.5.11` 靠本地同日副本 |
> | `v1.5.9` / `v1.5.7` / `v1.5.6` / `v1.5.5` | **已发布** | 3 / 3 / 2 / 3 | **没有** |
>
> 备份下来的是 `v1.5.8` 与 `v1.5.10` 的 `EggBloom-Lite.zip` / `EggBloom-Standard.zip`，
> 落在 `D:\_archive\releases\<版本>\`，四个文件的 SHA256 与各自 Release 里的
> `SHA256SUMS.txt` 逐条比对通过（215.42 / 215.78 / 5.58 / 6.18 MB）。`v1.5.11` 的三档包
> 在 `E:\EggBloom水星\dist` 有 11-01 同日副本，没另存。
>
> **`v1.5.5`–`v1.5.9` 那四条已发布 Release 的附件没有先备份就被一起删了。**
> 原因是删之前没把「要处理的 3 条草稿」和「接口这次返回的全部 7 条」核对清楚，
> 脚本按「列出来的全删」执行。仓库是私有的，外部没有人下载得到，损失只在本地：
> 能对上的历史构建还有 `D:\EggBloom备份\EggBloom水星\dist`（10-18）、
> `D:\workspace\_tmp\sumscheck`（10-23）、`D:\EggBloom水星\dist`（10-29），但它们
> 是各自那天的快照，不保证与已删的那几个 zip 逐字节相同。要精确复现有两条路：
> 用对应 tag 的源码重新打包（源码在 `D:\_archive\EggBloom-allrefs-20261114.bundle`
> 里），或走 GitHub Support 申请恢复已删附件。

**为什么重建了三次。** 前两个根都废弃了，原因不同，记在这里免得重犯：

1. `1b551b6`：建根时工作区有 86 项未提交，脚本取的是 HEAD 的树，于是根拿到
   脱敏**之前**的状态 —— 43 个文件里留着真号，`git grep` 就能捞到。当时脚本给的是
   警告，被当成「不影响合规性」跳过了。
2. `b399d6c`：根里含 §6.2 规则表。那张表按编号并列列出每条规则要挡的典型值，
   聚合成了一份「该防什么」的身份索引。
3. `fc3cdc1`：把表格移出仓库后重建，树内六个已知号 `git grep` 命中为 0。

对应的加固：`--apply` 遇到脏工作区**直接返回 1**，不再只给警告。判据是「树与
建根时 HEAD 相等」，不是「与 legacy 相等」—— 后者只在根恰好等于旧链末端时成立。

注意 `--plan` 与 `--apply` 打印的 sha 必然不同（`commit-tree` 取当前时间戳），
判据是树，不要比对 sha。

**⚠ `server` 不是第二条公开源（2026-11-13 核查）**

`server`（另一台机器上的裸仓库 `eggbloom-origin.git`，**地址不入库**）在共同祖先
`a0318b6`（11-08）之后另有 **22 个提交，本地这条线没有**：

| 类别 | 内容 |
| --- | --- |
| 真实功能 | `plugins/hotrestart/`、`plugins/memewiki/`、`scripts/check_all.py`、`scripts/alarm.py` |
| 运行产物 | `bot.pid`、`webui.pid`、`SEPARATION.lock`、`constraints.archive`、`bot_core.before-sep-recall.py` |
| 备份目录 | `backup/fieldmeta-*`、`backup/pre-separation-*` |
| 敏感文件 | `personality.txt` |

来源是 `8fff496 chore(deploy): 11-08 服务器运行树基线快照（排除 .venv/运行数据）`
那笔把整个运行树塞进了库。所以：

- 推 `origin` 用新根**安全** —— `origin/main`（450）本就在本地这条线上，
  新根的树与 `legacy/main` 逐字节相等；
- **不要**用新根强推 `server` —— 会删掉 `hotrestart` / `memewiki` 的源码；
- **不要**把 `server/main` 当公开源 —— 里面有 pid 文件、锁文件和人格文件。

那 22 个提交怎么并进来（cherry-pick 两个插件，还是彻底放弃那条线）是独立议题，
不阻塞本次推送。

代价是丢掉提交历史；收益是**永远不会有人从历史里翻出你的号**。
`--apply` 只取 `HEAD` 的树，**不含未提交改动**：先把工作区审完提交，再重建根。
**尚未推送** —— `origin/main` 与 `server/main` 仍是旧链，`--force-with-lease` 未执行。

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
- [x] 版本号对齐：`package.json` = `pyproject.toml` = `<version>`（当前 1.6.0，`scripts/activate.py --check-only` 会校验）
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
git commit -m "chore(release): <MM-DD> <version>"
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

- [ ] Tag：`v<version>`（与 `package.json` 一致），annotated，日期用真实发版日
      （`GIT_COMMITTER_DATE`），别用打标签那天
- [ ] 推标签前先跑 `git push --dry-run origin --tags`：`.githooks/pre-push` 只放行
      指在 `main` 链上的 tag，旧提交包成 tag 会被拒
- [ ] Release notes 直接取 `CHANGELOG.md` 对应段落
- [ ] 只附 `EggBloom-Standard.zip` + `EggBloom-Lite.zip` + `SHA256SUMS.txt`
      （**Full 不对外分发**：内含全量二次创作动漫表情包，版权与内容都不适合上公开仓库）
- [ ] 不要附 `bot_config.json`、`data/`、任何日志
- [ ] 发完把 `latest.json` 挂在这条 Release 上：配置项 `update_manifest_url` 默认指向
      `/releases/latest/download/latest.json`，只有**已发布**（非 draft）的 Release 才
      解析得到，draft 一律 404

---

## 4. 发布后

- [ ] 置顶 Issue：安装/配置问题请在 Issue 里提问（仓库未启用 Discussions）
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

# 基础设施标识：当前树扫一遍 + 判据自检
python scripts/check_secrets.py
python scripts/check_secrets.py --selftest

# 历史里的地址（片段换成真值；当前树干净不代表历史干净）
git log --all -S "<地址片段>" --oneline | wc -l

# 2026-11-15 起：上面两步有闸了，一条命令跑完四个面（推荐）
python scripts/check_history.py                 # 历史（默认跳过归档 ref）
python scripts/check_package.py --dist "E:\EggBloom水星\dist"   # 发行包
python scripts/prepublish.py --all --dist "E:\EggBloom水星\dist" --strict
#   退出码 0 全绿 / 1 有不干净的面 / 2 有面没验成（不算通过）

# 版本
grep '"version"' package.json; grep '^version' pyproject.toml

# 测试（见 §2 的已知失败项）
python tests/test_persona_tag.py
python tests/test_dev_watch.py
python tests/test_release_surface.py            # 包闸/历史闸会不会拦、判据是不是只有一份
```

---

## 6. 测试现状

> **2026-11-15 现状：368 个用例，命令 `python run_all_tests.py`。** 1.6.0 首发时是
> 358；之后随工具链回迁新增 7 个（`test_drive_tokens` / `test_check_fieldmeta_chain` /
> `test_check_all` / `test_identity_numbers` / `test_memewiki` / `test_hotrestart` /
> `test_restart_notice`），出站动作账本新增 2 个（`test_outbound_ledger` /
> `test_meme_suggest_review`），31 类自动动作全补又新增 1 个
> （`test_action_coverage`）。逐项通过数不在
> 本文记录 —— 它是某台机器某一刻的快照（同一份代码在缺 Pillow / 缺 Playwright 的
> 机器上结果不同），对外只承诺「用例数 + 一条可复核的命令」。
>
> **2026-11-09 全量 `run_all_tests.py`：352 个用例 / 352 通过 / 0 意外失败 / 0 已知红**，
> 起止 HEAD 均 `ebc9c18`。下面是 2026-10-22 的历史快照，数字已过时，只留作背景。

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
