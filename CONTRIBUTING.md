# 贡献指南

EggBloom Desktop 目前由维护者单人开发，仓库不开放常规 PR。本文说明可以参与的途径与既有约定。

---

## 行为准则

本项目遵循 [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/) 行为准则。参与即表示同意遵守。

---

## 如何贡献

### 1. 报告 Bug

Issue 收两类：**Bug** 和 **功能建议**。装不上、配置不会、想问效果，走 [Discussions](https://github.com/Eggcrate9361/EggBloom/discussions)。

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

### Git Commit Message

遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Type**：
- `feat`：新功能
- `fix`：修复 Bug
- `docs`：文档更新
- `style`：格式调整（不影响逻辑）
- `refactor`：重构
- `perf`：性能优化
- `test`：测试相关
- `chore`：构建/工具/依赖更新
- `ci`：CI 配置

**示例**：
```
feat(protocol): add LLOneBot auto-detect support

- Scan common install directories for llbot.exe
- Add version compatibility check

Closes #123
```

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

- **Issues**：Bug、功能建议（各有模板）
- **Discussions**：使用问题、装不上、配置不会、想问效果
- **Security 私密通道**：安全漏洞，见 [SECURITY.md](SECURITY.md)。不要开公开 Issue

---

## 常见问题

**Q: 可以直接向 main 分支提 PR 吗？**
A: 仓库目前不接受常规 PR，`main` 只走 Release 提交。Bug 和功能建议开 Issue，使用问题走 Discussions。

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

## 致谢

Issue 里报的问题若被采纳，会在 `CHANGELOG.md` 对应版本下记名。