# 贡献指南

EggBloom Desktop 目前由维护者单人开发，仓库不开放常规 PR。本文说明可以参与的途径与既有约定。

---

## 行为准则

本项目遵循 [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/) 行为准则。参与即表示同意遵守。

---

## 如何贡献

### 1. 报告 Bug

Issue 收两类：**Bug** 和 **功能建议**。装不上、配置不会、想问效果，也开 Issue（仓库未启用 Discussions）。

- 先搜索 [Issues](https://github.com/Eggcrate9361/EggBloom/issues) 确认未被报告
- 使用 Bug Report 模板创建新 Issue
- 必须包含：
  - 复现步骤
  - 预期行为 vs 实际行为
  - 环境信息（Python 版本、OS、协议驱动版本）
  - 相关日志（脱敏后）

### 2. 提出新功能

用 [功能建议模板](https://github.com/Eggcrate9361/EggBloom/issues/new?template=feature_request.yml) 开 Issue。

- 先说场景再说方案。模板第一栏就是「你遇到的问题」，写清现状为什么不够用
- 涉及 UI 变更请提供草图/原型
- 能自己改的小改动，直接提 PR 比开 Issue 快

### 3. 提交代码 (Pull Request)

#### 分支策略

只有 `main` 一条长期分支。仓库目前不接受常规 PR，改动由维护者直接提交。

如果将来开放 PR，约定如下：
- `main`：稳定发布分支，只接受 Release 相关提交
- `feature/*`：功能分支
- `fix/*`：修复分支
- `hotfix/*`：紧急修复分支（基于 main）

#### PR 流程

1. Fork 仓库并创建特性分支：`git checkout -b feature/awesome-feature`
2. 编写代码，**遵循现有代码风格**（见下文）
3. 添加测试（见「测试」节）
4. 运行本地检查：

   ```bash
   ruff check .
   python tests/test_xxx.py
   ```

5. 提交 PR，关联相关 Issue
6. 等待 Code Review，根据反馈修改

---

## 代码规范

### Python

- **版本**：Python 3.13
- **风格**：遵循 PEP 8。`ruff` 只开 `select = ["F"]`（Pyflakes 错误级），不是全量规则；行宽 120
- **类型注解**：新增公共 API 必须加类型注解
- **命名**：
  - 模块/包：`snake_case`
  - 类：`PascalCase`
  - 函数/变量：`snake_case`
  - 常量：`UPPER_SNAKE_CASE`
  - 私有：`_leading_underscore`
- **导入顺序**：
  1. 标准库
  2. 第三方库
  3. 本地模块（相对导入）
- **文档字符串**：公共函数/类必须有 docstring（Google/NumPy 风格均可）

```python
# 示例
def calculate_tokens(messages: list[dict], model: str = "deepseek-chat") -> int:
    """计算消息列表的估算 token 数。

    Args:
        messages: OpenAI 格式消息列表
        model: 模型名称，用于选择编码器

    Returns:
        估算 token 总数

    Raises:
        ValueError: 模型不支持时抛出
    """
    ...
```

### WebUI（webui/webui_artistic_v4.html）

- **单文件**：HTML + CSS + JS 全在一个文件里，约 540 KB，无构建步骤、无 `type="module"`
- **新旧共存**：v3 与 v4 都在 `webui_server.py` 的映射表里，改 v4 不要动 v3
- **CSS 变量**：全部颜色/字号走 `:root` 里的变量（`--bg` / `--ink` / `--line` / `--accent`），不要硬编码色值
- **命名**：BEM（`.block__element--modifier`）
- **字体**：像素硬边字体，按 12px 网格对齐
- 改完必须用 `shoot_webui.py` 截图核对，不要只看代码

### 6 提交规范（Commit Policy）

> 小节号（§6.2 规则表、§6.5 历史处理）被 `tools/commit_lint.py`、
> `tests/test_commit_format.py`、`docs/RELEASE_CHECKLIST.md` 直接引用，**不要重排**。
> 编号 6 沿用「§6.3 不重写历史」这个早就写进注释与提交信息的叫法，与上文的小节层级
> 无关；本节以下的小节号只在本文内部有意义。
> 最后修订 2026-11-13。上一版（2026-11-05）为什么失效，见仓库外
> `提交历史体检-20261113.md` §3.1：判据写成「行首匹配 6 个短语」，
> 句中出现的 6 个违规一条都扫不到，闸还报「全部通过」。
> 下表「对应体检项」一列里的 `§N` 指**体检报告**的节号，不是本文的。

#### 6.1 标题格式

```
<type>(<scope>): <MM-DD> <做了什么>
```

`<MM-DD>` 是改动发生日期（月-日），紧跟在冒号后面；`<scope>` 可省，跨模块用
逗号并列、不加空格。例：

```
fix(group): 10-24 静默时段只管得住主动开口，管不住群里被 @
fix(meme,reply): 10-31 群表情包采集只认 message_type → 收不进来
```

格式自 **2026-10-24** 起统一执行。更早的提交格式不一 —— 有的带日期、有的不带、
有的以时间开头（`68f52c6` 是 `21:30 …`）。历史不改，见 §6.5。

**Type**：`feat` 新功能 · `fix` 修 Bug · `docs` 文档 · `style` 格式（不影响逻辑）·
`refactor` 重构 · `perf` 性能 · `test` 测试 · `chore` 构建/工具/依赖 ·
`ci` CI · `build` 打包 · `revert` 回滚

**示例**：

```
fix(config): 10-24 日志级别配了不生效，因为读取方根本不存在

logger_setup.get_logger 里写着 from config import get_log_level，
但 config.py 里从来没有这个函数，ImportError 被 except 吞掉，
日志级别恒为 INFO，没有一处报错。

补齐 get_log_level()，非法值回 INFO 但在日志里点名。
```

#### 6.2 规则表

九条规则的编号、级别与生效日写在下方 `commitlint` 区块里（`machine_rules` /
`rule_since`），判据实现见 `tools/commit_lint.py` 的 `RULE_META`。

**规则说明表本身不在仓库里。** 那张表按编号并列写出每条规则要挡的典型值
（昵称、示例号、跑测措辞），集中成一份「该防什么」的索引，等于把散落在各处的
身份词聚成一份清单 —— 而这份清单恰恰是脱敏要避免的东西。要查时看本地
`docs/规范表.md`（已 gitignore，不进提交），或直接读 `RULE_META`。

`机器` 由 `tools/commit_lint.py` 判定（提交时钩子阻断，`tests/test_commit_format.py`
复核历史）；`人` 写进 §6.7 自检清单，靠复核。**每个编号都对应体检报告里的一类问题**，
改规则时请连"为什么"一起改，别只改正则。

**生效日为什么不一样。** `CM-02/03/06/08` 在 11-05 那版规范里已经写了（只是闸没判对），
所以追到 11-05；`CM-04/05/07` 是 11-13 体检才提出的**新**规则，不能拿去判过去的提交 ——
那等于事后立法，也让「闸报 0」失去意义。生效日只有 `commitlint` 区块一个来源。

`warn` 不阻断单次提交，但发布闸（`--strict`）里一律按 error 算 —— 见 §6.8。

<!-- commitlint:begin -->
```json
{
  "types": ["feat", "fix", "docs", "style", "refactor", "perf", "test", "chore", "ci", "build", "revert"],
  "scope_pattern": "[a-z0-9][a-z0-9_,-]*",
  "subject_template": "<type>(<scope>): <MM-DD> <做了什么>",
  "effective": "2026-10-24",
  "body_effective": "2026-11-05",
  "baseline": "65a6d26",
  "rule_since": {
    "CM-01": "2026-10-24",
    "CM-02": "2026-11-05",
    "CM-03": "2026-11-05",
    "CM-04": "2026-11-13",
    "CM-05": "2026-11-13",
    "CM-06": "2026-11-05",
    "CM-07": "2026-11-13",
    "CM-08": "2026-11-05",
    "CM-09": "2026-11-13"
  },
  "machine_rules": ["CM-01", "CM-02", "CM-03", "CM-04", "CM-05", "CM-06", "CM-07", "CM-08", "CM-09"]
}
```
<!-- commitlint:end -->

上面这个区块是**规范的机器可读部分**，`commit_lint.py` 启动时读它，不在代码里
另外定义一份类型表或生效日期。改类型、改生效日期、改基线，只改这里。
`--check-policy` 会校验：区块能解析、`machine_rules` 与实现双向一致、
`baseline` 这个提交真的存在（历史 orphan 重写后必须同步改成新根，否则冻结/活跃
划分会失效）。

#### 6.3 正文

正文按「现象 → 根因 → 修法」写，现象用**日志原文、报错、代码现状**说话。
不是按文件罗列改动，那读 `git show --stat` 就够了。

**禁止转述**（CM-02）：谁说的不重要，看到什么才重要。

```
反例：
  用户说「群聊里 bot 总在反问」。

正例：
  群聊连发三条，末句全是问号：「讲到哪了，极限还是求导」。

  反问闸（reply_gate）只在 guard_mode=regen 时重生，默认 off 只记日志，
  所以问句照发。
```

引用对话时只写**能当证据的那一句**，且不写谁说的：

```
反例：真机 00:40：用户说「生日结束了吧」，bot 回「未找到进程: 了吧」。
正例：私聊 00:40 收到「生日结束了吧」，被当成进程名解析，回「未找到进程: 了吧」。
```

**也不写「谁提的」**（CM-02 后半）：`用户 09:04 截图`、`用户日志`、`用户指定`
都把现象挂到了人身上，而读者要的是那个现象。时间、载体、报错本身留着。

```
反例：## 现场（用户 09:04 截图 + 日志）
正例：## 现场（09:04 截图 + 日志）
```

**不写职场拟人化措辞**（CM-09）：把程序写成被派活的下属、把需求方写成上级，
读者拿到的是角色扮演，不是技术事实。

```
反例：feat(agent): 11-02 通读/审查类请求第一轮就派活（别把轮次烧在 glob 上）
正例：feat(agent): 11-02 通读/审查类请求第一轮就交给子 agent（别把轮次烧在 glob 上）

反例：按用户指定只改协议页
正例：改动限定在协议页（protocol_panel.py）
```

`指定` 只在**归因到人**时判（`用户指定` / `按指定` / `指定要`）。
`指定路径`、`指定目标`、`-DistPath 指定` 是正常工程用语，不判 —— 全词禁会误伤，
历史里实测 6 处全是合法写法。

这条自 **2026-11-05** 起执行，更早的转述属历史（§6.5）。CM-09 自 11-13 起。

#### 6.4 禁止写进提交信息的东西

提交信息是**永久且公开**的，一 push 就进 `git log`，不用翻 diff。以下五类一律不进：

| 禁止 | 为什么 | 替代写法 |
|---|---|---|
| 真实 QQ / 群号 / 会话 ID / UIN | §4：42 个提交直接写号，40 个已 push | `某个 9 位号`、`群 2xxxx`、`合成号 1000000001` |
| 昵称 / 称呼 | §4：86 个提交含真实昵称与群名片（具体值见闸内 `_RE_NICK`） | `A`/`B`、`对方`、`某个成员` |
| 一次跑测结果 | §2：读者无法复核，且跑完就过期 | 写**判据**（「20 条夹具，覆盖 X/Y/Z」），不写分数 |
| 密钥 / 凭据 / 密码 | 泄露即失效，且历史里删不掉 | `已轮换`，值本身不进任何地方 |
| 对话原话（整段） | 转公开后是隐私与笑话素材 | 只留技术事实，见 §6.3 |

脱敏时**位数保持一致**（9 位号换 9 位），否则测试夹具里的长度断言会一起错，
这是 1.6.0 替换表定下的规则。

**号码这一类由机器判，判据只有一份**：`tools/identity_numbers.py`。提交信息闸
（CM-03）与源码扫描（`scripts/check_secrets.py`）都调它，两边口径不会再分家。
判据分两层：

* **拦名单**（`tools/identity_blocklist.txt`，只存 `sha256(盐 + 号)` 的前 16 位）：
  已知真实号，出现在任何位置都拦。换号 / 加群之后跑
  `python tools/identity_numbers.py --check-local` 看漏没漏，`--add <号> "说明"` 补一条。
  存哈希不存明文，因为这份清单本身进公开仓库 —— 但哈希只是遮蔽不是加密，
  别把它当安全边界。
* **身份位白名单**：`user_id=` / `group_ids:` / `target_qqs: […]` 右侧的 8-11 位数
  必须落在合成段里（`1000000xxx` 合成 uid、`9000000xx` 合成群号、`10086xxx`
  假对话、键盘序、全 9）。**新增夹具直接用合成段**，别手写一个「看着像假」的号 ——
  白名单是白名单，不是「报错了再往 `SYNTH_EXPLICIT` 里塞一条」。

11-13 那次的病根是判据分家：提交信息闸只读 message、源码扫描只认手机号段和
config 里的键值对，泄漏恰好落在两边都不管的位置（`tests/` 里 `user_id=` 右侧）。
`tests/test_identity_numbers.py` 会断言「号码规则不许散回各处」，并每次跑一遍
全树扫描 —— 活的工作树必须 0 命中。

#### 6.5 历史处理（不可逆操作）

1. **已 push 的提交不改写。** `origin/main` 上的提交一旦重写，所有克隆分叉，
   代价远大于一条措辞不理想的标题。`1fedafc`（10-26 漏日期前缀）等 5 笔就是
   这样留作反例的。
2. **本地的、未 push 的提交可以改**：`git commit --amend` / `git rebase -i`
   只在推之前有效，推之后就按第 1 条。
3. **例外必须登记**，不许写进代码里的白名单字符串：登记在
   `tools/commit_lint_debt.json`，每条要有 `sha` / `rule` / `why` / `decision`
   （`rewrite` / `orphan` / `keep`）/ `review_by`（复核日期）。
   `tests/test_commit_format.py` 会验证每条**仍然真的违规** —— 过期条目必须删，
   删不掉就说明规则或登记错了。
4. **唯一允许批量重写的窗口**：转公开之前，且必须同时满足
   ①`refs/backup/*` 全量备份已建并验证可 `git log`；②重写后 `git log` 复扫 0 命中；
   ③ 通知所有克隆方（当前只有维护者本人）。窗口关闭后按第 1 条办。
5. **禁止 `git push --all` / `--mirror`。** 这条由 `.githooks/pre-push` 兜底：
   origin 只收「`main` 及其祖先」，其余 ref 一律拒（`EGGBLOOM_ARCHIVE_PUSH=1` 才放行）。
   2026-11-14 之前这道闸只按 ref 名拦 `refs/heads/*`，**标签是敞开的** —— 公开仓库上
   确实挂着 `v1.5.8` / `v1.5.10` / `v1.5.11` 三个标签指向旧链提交（25 / 9 / 19 个
   文件里有真号），11-14 已删。判据改成按提交判之后，「把旧提交重新包装成 tag
   推上去」这条路也堵了。
   只推 `main` 与指在这条链上的 `v*` tag。
6. **不要为「修正提交信息」单独开一条提交。** 提交是不可变对象，新提交改不了
   旧提交；写一条"以后注意措辞"的提交，只是让历史里多一个同样要遵守规则的对象，
   而它为了说明问题还得复述那些词 —— 于是需要豁免，豁免本身又要被复核。
   这就是"这类问题改不完"的机制：判据一宽就冒出新的一批实例。同一份 679 个提交，
   实测三次口径：体检按 6 个短语近义词数出 72 个转述，换成全文正则变 78；
   按 4 个已知号数出 42 个，换成 8–11 位通用判据变 47；再加"职场口吻"一类又多 10。
   逐条修是发散序列，永远没有"改完"那一刻。
   要治只有两头，都有界：**闸**约束还没写的，**换历史线**一次清掉已写的。
   判断标准：一条提交如果只改了措辞约定、没改判据，它不该存在 ——
   `CONTRIBUTING.md` 的 `commitlint` 区块与 `tools/commit_lint.py` 是同一件事，一次做完。

#### 6.6 分支与推送

| 分支 | 用途 | 推送 | 生命周期 |
|---|---|---|---|
| `main` | 唯一长期分支，发布线 | 只推它 | 永久 |
| `wip/<主题>` | 本地半成品，允许脏提交 | **不推** | 合回 main 后删 |
| `release/<version>` | 打包/发版冻结窗口 | 可推 | tag 后删 |
| `backup/*` | 重写/大改前的快照 | **绝不推** | 保留到确认无回滚需求 |

- 一次 push 只推一个 ref：`git push origin main`。唯一的批量例外是发版补标签
  （`git push origin --tags`）：标签只有名字、没有工作树，一次推完不会把多个分支
  的状态混在同一份 reflog 里。
- 发版只打 annotated tag：`git tag -a vX.Y.Z -m "Release vX.Y.Z"`，
  禁止轻量 tag（`git tag vX.Y.Z` 没有作者与时间，审计时无法追）。
- 合并用 `--no-ff`，保留「这组提交是一件事」的结构；
  禁 `--squash` 合并到 `main`（会把多笔有信息量的提交压成一条）。
- 任何 `--force` / `--force-with-lease` 推 `main` 都属于 §6.5.4 的重写窗口内动作，
  窗口外禁止。

#### 6.7 提交前自检（可执行）

```bash
python scripts/check_secrets.py            # 工作树/暂存区敏感信息
python tools/commit_lint.py --check-policy # 文档 ↔ 闸 一致
python tools/commit_lint.py --rev-range HEAD~1..HEAD   # 这一笔自己看一遍
```

钩子装一次即可（`.githooks/` 是版本化的源，`scripts/install_git_hooks.py` 复制到
`.git/hooks/`）：

```bash
python scripts/install_git_hooks.py
git config core.hooksPath            # 应为空：用 .git/hooks，不切 hooksPath
```

人判的三条（机器判不了，别假装能判）：

1. **一条提交一件事**：改了两个互不相关的东西就拆开，否则回滚时必须整笔回滚。
2. **正文能回答「不这么改会怎样」**：答不上来说明还没想清楚，先别提交。
3. **删掉的代码要说为什么删**，不能只写「清理」。

#### 6.8 发布闸

两个口径，判的是**不同对象**，别混：

```bash
python tools/commit_lint.py --all-refs --strict   # 日常：只管基线之后的提交
python tools/commit_lint.py --publish             # 公开：整条历史，必须 0
```

- `--strict`：活跃窗口（`baseline..HEAD`）内 `warn` 升 `error`，登记表不作数。
- `--publish`：不看活跃/冻结、不看规则生效日、不认登记表，昵称也按 error 算。

**为什么需要两个。** `--strict` 只判基线之后的提交：把活跃窗口那 9 处清理掉，
它就报「通过」，而冻结部分的 378 处照样躺在 `git log` 里 —— 转公开时读者看到的是
**整条历史**，不是窗口。只把窗口内的账做平就宣布干净，就是本规范开头批评的
那种自证。当前实测：`main` 10 个提交，两个口径均为 0 处。

**旧链去哪了（2026-11-14）。** 已经全部删干净，公开仓库现在只有 `main` 一条 ref：

* 本地：`legacy/main`（531 个提交）、三条 `backup/*`、两条 `dev/*`、`public/main`、
  **所有 tag**（`v1.5.5` / `v1.5.6` / `v1.5.7` / `v1.5.8` / `v1.5.9` / `v1.5.10` /
  `v1.5.11` / `ui-pre-t8` / `backup-before-doc-merge-192730`）、`server/*` 的远端跟踪 ref；
  版本号那七个当天晚些时候重建过，但指向变了 —— 见本节末「标签重建」；
* 远端：同样这些 tag 也 `push --delete` 掉了。

理由是标签事故：`v1.5.8` / `v1.5.10` / `v1.5.11` 指向旧链提交，25 / 9 / 19 个文件里
有真号；`v1.5.9` 的 172 个提交里有 25 处号、20 处昵称；`v1.5.7` 也有 1 处。
只要 tag 还在，公开仓库就一直连回那条旧链 —— 留着就得靠「记得别推」来防守，
而 11-14 那次事故证明「记得」不管用。

删除前做了全量归档（**这是唯一的副本，仓库里已经恢复不出来了**）：

```
D:\_archive\EggBloom-allrefs-20261114.bundle   12 MB，含全部分支/标签/远端跟踪 ref
git bundle verify <文件>                        # 校验
git clone <文件> /tmp/legacy && cd /tmp/legacy  # 要复查旧链就从这个 clone 里看
```

所以 `--rev-range legacy/main --publish`（当时报 387 处 / 226 个提交）在本仓库里
跑不了了，要复现得先展开这个 bundle。

**标签重建（2026-11-14 晚）。** 清空当天就把版本名补回来了，但含义变了，别当成
「1.5.x 的代码还能在这里被翻出来」：

| 标签 | 指向 | 说明 |
|---|---|---|
| `v1.5.5` … `v1.5.11` | `3832b6b`（公开历史起点） | 七个版本**全部指向同一个提交** |
| `v1.6.0` | `main` HEAD | 当前发布线 |

新链只有 10 个提交、根在 2026-11-13。1.5.x 那套代码是 531 个提交里的一堆中间状态，
没有任何一个存活提交能代表其中某一版，所以只能全部挂在根快照上。这是**版本名的
保留**，不是版本状态的保留 —— 想在某个 tag 上复现当时的代码，答案是不行。

标签时间用的是 `CHANGELOG.md` 里各版本的真实发版日（`GIT_COMMITTER_DATE`），
不是重建日；作者与提交对象由 annotated tag 记着，能追。

代价说清楚：一个提交上摞七个 tag，`git describe` 会取到 `v1.5.11` 之类而不是
`v1.6.0`，反查「当前在哪个版本」要用 `git tag --contains` 加版本号排序，别用
`describe`。真在意这个就等 1.6.0 打包发出去后，把 1.5.x 那七个删掉。

`ui-pre-t8` 没有重建 —— 它不是版本号，是 T8 重构前的一个工作点，指向的提交已经
不存在，留着只是一个空名字。

**发布页同一天清空。** GitHub 上 7 条 Release 全部删除（3 条草稿 + 4 条已发布），
Releases 页现在是空的。其中 `v1.5.5`–`v1.5.9` 那四条已发布的附件**没有先备份**就
跟着删了 —— 记录与补救路径写在 `docs/RELEASE_CHECKLIST.md` 的 11-14 补记里，
这里只留一句结论：删之前要按接口返回的全量核对，别按自己记着的条数核对。

**§6.5.4 重写窗口的验收判据是 `--publish` 归零**，不是 `--strict` 归零。

这个闸**不在 CI 名单里**，原因写清楚免得被当成漏配：CI 的 `actions/checkout`
是浅克隆（`fetch-depth` 默认 1），`git log` 只有 1 个提交，扫不出历史；
而 GitHub 的合并提交本身也不合本规范的标题格式，加进去只会红。
所以分工是：**新增的靠 commit-msg 钩子拦，历史的靠 `--publish` 拦**。

### 什么不进仓库

**计划、方案、路线图、版本规划不进版本库。** 它们放仓库外（维护者桌面的
`EggBloom-计划归档/`），仓库里只留三种东西：

| 留下 | 例子 |
|---|---|
| 代码注释里的"为什么" | `_write_guard` 里每一道闸的理由 |
| README 里的接口与边界 | `plugins/bili/README.md` 的能力矩阵 |
| 测试里的判据 | "没数据时必须返回空，不许编" |

理由：计划会过期、会和实现对不上，而仓库里的过时计划比没有计划更糟——
读者会当真。`2026-10-30` 起执行（`docs/FIXPLAN_1.5.8_REPACK.md` 与
`plugins/这样脆弱的东西/ROADMAP.md` 同日移出）。

---

## 测试

测试是**独立脚本**，不是 pytest 用例。每个文件自带 `__main__`，直接跑：

```bash
python tests/test_xxx.py
```

退出码 0 为通过。全部跑一遍：

```bash
for f in tests/test_*.py; do python "$f" || echo "FAIL: $f"; done
```

- 新测试放在 `tests/`，单文件命名 `test_<模块>_<主题>.py`
- 断言风格沿用现状：`check("描述", 条件)` 记计数，`main()` 末尾按 `FAIL` 返回退出码。不要混用 `assert`（只有 `test_plugin_bridge.py` 用了）
- 失败的打印要说清「期望 vs 实际」
- 依赖 GUI、网络、本机状态的测试不要进 CI
- `tests/_sandbox.py` 提供隔离环境，会碰落盘状态的测试必须用

CI 只跑 `.github/workflows/ci.yml` 里列出的那 8 个文件，其余靠手动。名单是**确认一个加一个**扩的，长期红的 CI 等于没有 CI。

### 改完必跑：`scripts/check_all.py`

单点自检散在几个脚本里，全靠人记得逐个敲；而这条链上的失败几乎全是**静默**的——内联 script 语法坏了整块顶层函数不执行、字段链漏一步「恢复默认」按钮整片消失、v3→v4 页面退化却照样渲染得挺好看。所以合成一条：

```bash
python scripts/check_all.py            # 全跑
python scripts/check_all.py -k secrets # 只跑某项（可重复）
python scripts/check_all.py --list     # 看分项名
python scripts/check_all.py --fast     # 跳过浏览器项
```

**别用系统 `python3` 跑它。** 它用 `sys.executable` 起子脚本，子项看到的就是你调它的那个解释器；系统解释器里没装 playwright，`config_parity` 会直接 `SKIP`，看着像环境坏了，其实是调错了入口。用项目虚拟环境的解释器（Windows 是 `.venv\Scripts\python.exe`）。

退出码：`0` 全通过；`1` 有失败（真跑了没过，或子脚本缺失 / 解释器起不来）；`2` 有跳过——缺 node / playwright，或你加了 `--fast`，**该项根本没跑，跳过不算通过**。发布前必须补依赖跑到 `0`。

---

## 依赖管理

- **生产依赖**：`pyproject.toml` 中 `dependencies` 列表，**必须固化精准版本号**
- **开发依赖**：`[project.optional-dependencies] dev`
- **新增依赖流程**：
  1. 评估必要性（标准库优先、体积、维护状态、许可证兼容性）
  2. 在 `pyproject.toml` 添加精准版本（`==x.y.z`）
  3. 改了依赖就在 `pyproject.toml` 里同步版本（本项目用 pyproject，没有 uv.lock / requirements.txt）
  4. 说明理由在 PR 中

---

## 安全规范

- **绝不提交敏感信息**：API Key、Token、密码、真实 QQ 号、群号
- 配置文件使用 `.example` 模板，真实配置由 `.gitignore` 排除
- API Key 存储：Windows 凭据管理器（`cred_store.py`），不落盘明文
- 依赖扫描：定期手动跑 `pip-audit`（未纳入 dev 依赖，需自装）
- 报告安全漏洞：走 GitHub 私密通道（仓库 → Security → Report a vulnerability），见 [SECURITY.md](SECURITY.md)。不要开公开 Issue

---

## 文档更新

- 代码变更同步更新相关文档：
  - `README.md`：用户可见功能变更
  - `CHANGELOG.md`：每版本变更汇总（由维护者维护）
  - 函数/类 docstring：API 变更
  - `CONTRIBUTING.md`：流程/规范变更
- 新增配置键必须同步 `bot_config.json.example`。这是最常漏的一步，漏了 example 就是死文件
- 新增环境变量才动 `.env.example`（它只放 API Key / 端点 / 模型名这类，不放普通配置键）

---

## 版本发布

由维护者执行：

1. 版本号遵循 SemVer
2. 打 Tag：`git tag -a vX.Y.Z -m "Release vX.Y.Z"`
3. 跑 `scripts/pack_all.py` 出包（Lite / Standard / Full，见 [docs/发布打包.md](docs/发布打包.md)）
4. 更新 `CHANGELOG.md`
5. 建 GitHub Release 并上传三个 zip 与 `SHA256SUMS.txt`

CI（`.github/workflows/ci.yml`）只跑冒烟测试，不出包。

---

## 沟通渠道

- **Issues**：Bug、功能建议、使用问题（各有模板；仓库未启用 Discussions）
- **Security 私密通道**：安全漏洞，见 [SECURITY.md](SECURITY.md)。不要开公开 Issue

---

## 常见问题

**Q: 可以直接向 main 分支提 PR 吗？**
A: 仓库目前不接受常规 PR，`main` 只走 Release 提交。Bug、功能建议和使用问题都开 Issue。

**Q: 我改了配置文件格式，需要注意什么？**
A: 
1. 同时更新 `bot_config.json.example` 和 `config.py` 的 `DEFAULT_CONFIG`
2. 在 `config.py` 的 `load_config` 中处理迁移逻辑（旧字段兼容）
3. 更新 `README.md` 配置说明

**Q: 如何添加新的协议驱动？**
A: 
1. 在 `protocols/` 新建 `xxx_adapter.py`，继承 `ProtocolAdapter`
2. 文件末尾调 `registry.register("xxx", XxxAdapter)`，并在 `protocols/__init__.py` 里 import 该模块（import 即注册）
3. 在 `config.py` 的 `DEFAULT_CONFIG["protocol_drivers"]` 添加默认配置
4. 更新 README 里支持驱动的型号列表

**Q: 依赖版本冲突怎么办？**
A: 优先降级/升级到兼容版本，实在冲突可考虑 `pip install --no-deps` 或 vendor 方式，但需在 PR 说明理由。

---

## 许可

本项目以 [MIT](LICENSE) 分发。

提交 Issue、Pull Request 或任何补丁，即表示你同意把该贡献按 MIT 授权给本项目，
并确认你有权这样授权（例如：代码不是从许可证不兼容的项目复制的）。
文档、截图、人格文本若另有来源，请在提交时注明。

---

## 致谢

Issue 里报的问题若被采纳，会在 `CHANGELOG.md` 对应版本下记名。
