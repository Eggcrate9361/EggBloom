# 贡献指南 / Contributing Guide

感谢你对 EggBloom Desktop 的关注与贡献！🎉

---

## 📋 行为准则

本项目遵循 [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/) 行为准则。参与即表示同意遵守。

---

## 🚀 如何贡献

### 1. 报告 Bug

- 先搜索 [Issues](https://github.com/Eggcrate9361/EggBloom/issues) 确认未被报告
- 使用 Bug Report 模板创建新 Issue
- 必须包含：
  - 复现步骤
  - 预期行为 vs 实际行为
  - 环境信息（Python 版本、OS、协议驱动版本）
  - 相关日志（脱敏后）

### 2. 提出新功能

- 先开 Issue 讨论设计方案，避免无效开发
- 说明使用场景、预期收益、可能的风险
- 涉及 UI 变更请提供草图/原型

### 3. 提交代码 (Pull Request)

#### 分支策略
- `main`：稳定发布分支，**仅接受 Release PR**
- `develop`：开发主线，**日常 PR 合并至此**
- `feature/*`：功能分支
- `fix/*`：修复分支
- `hotfix/*`：紧急修复分支（基于 main）

#### PR 流程
1. Fork 仓库并创建特性分支：`git checkout -b feature/awesome-feature`
2. 编写代码，**遵循现有代码风格**（见下文）
3. 添加/更新测试（如适用）
4. 运行本地检查：
   ```bash
   # 代码风格检查
   ruff check .
   
   # 类型检查（如配置了 pyright/mypy）
   # pyright
   
   # 运行测试
   python -m pytest tests/ -v
   ```
5. 提交 PR 到 `develop` 分支
6. 填写 PR 模板，关联相关 Issue
7. 等待 Code Review，根据反馈修改
8. 通过后由维护者合并

---

## 🎨 代码规范

### Python

- **版本**：Python 3.13
- **风格**：遵循 PEP 8，使用 `ruff` 统一格式化
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

### JavaScript / HTML / CSS (WebUI)

- **无构建步骤**：单文件 SPA，原生 ES6 模块
- **风格**：2 空格缩进，单引号，分号可选
- **CSS**：使用 CSS 变量（`--color-*`），避免硬编码颜色
- **命名**：BEM 规范（`.block__element--modifier`）

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

## 🧪 测试规范

- 单元测试放在 `tests/` 目录，对应模块结构
- 使用 `pytest` + `pytest-asyncio`
- 测试命名：`test_<function>_<scenario>`
- 覆盖率目标：核心模块 ≥ 80%
- 集成测试标记：`@pytest.mark.integration`（需手动运行）

```bash
# 运行所有测试
python -m pytest tests/ -v

# 仅运行单元测试（排除集成测试）
python -m pytest tests/ -v -m "not integration"

# 生成覆盖率报告
python -m pytest tests/ --cov=bot_core --cov=config --cov=protocols
```

---

## 📦 依赖管理

- **生产依赖**：`pyproject.toml` 中 `dependencies` 列表，**必须固化精准版本号**
- **开发依赖**：`[project.optional-dependencies] dev`
- **新增依赖流程**：
  1. 评估必要性（标准库优先、体积、维护状态、许可证兼容性）
  2. 在 `pyproject.toml` 添加精准版本（`==x.y.z`）
  3. 改了依赖就在 `pyproject.toml` 里同步版本（本项目用 pyproject，没有 uv.lock / requirements.txt）
  4. 说明理由在 PR 中

---

## 🔒 安全规范

- **绝不提交敏感信息**：API Key、Token、密码、真实 QQ 号、群号
- 配置文件使用 `.example` 模板，真实配置由 `.gitignore` 排除
- API Key 存储：Windows 凭据管理器（`cred_store.py`），不落盘明文
- 依赖扫描：定期运行 `pip-audit` 或 `safety check`
- 报告安全漏洞：私信维护者或邮件 `security@eggbloom.example`（请勿公开 Issue）

---

## 📝 文档更新

- 代码变更同步更新相关文档：
  - `README.md`：用户可见功能变更
  - `CHANGELOG.md`：每版本变更汇总（由维护者维护）
  - 函数/类 docstring：API 变更
  - `CONTRIBUTING.md`：流程/规范变更
- 新增配置项必须在 `bot_config.json.example` 和 `.env.example` 中补充说明

---

## 🏷 版本发布

由维护者执行，流程：

1. `develop` → `main` 合并 PR（版本号遵循 SemVer）
2. 打 Tag：`git tag -a v0.x.y -m "Release v0.x.y"`
3. GitHub Actions 自动构建 Release（含标准版安装包、Lite 版源码包）
4. 更新 `CHANGELOG.md`
5. 发布 GitHub Release

---

## 💬 沟通渠道

- **Issue Tracker**：Bug 报告、功能讨论、设计评审
- **Discussions**：一般问答、经验分享、非结构化讨论
- **PR Review**：代码级技术讨论

---

## ❓ 常见问题

**Q: 可以直接向 main 分支提 PR 吗？**
A: 不可以，请向 `develop` 分支提交，`main` 仅接受 Release PR。

**Q: 我改了配置文件格式，需要注意什么？**
A: 
1. 同时更新 `bot_config.json.example` 和 `config.py` 的 `DEFAULT_CONFIG`
2. 在 `config.py` 的 `load_config` 中处理迁移逻辑（旧字段兼容）
3. 更新 `README.md` 配置说明

**Q: 如何添加新的协议驱动？**
A: 
1. 在 `protocols/` 新建 `xxx_adapter.py`，继承 `OneBotStdAdapter`
2. 在 `protocols/__init__.py` 注册
3. 在 `config.py` 的 `DEFAULT_CONFIG["protocol_drivers"]` 添加默认配置
4. 更新 `README.md` 协议驱动表格

**Q: 依赖版本冲突怎么办？**
A: 优先降级/升级到兼容版本，实在冲突可考虑 `pip install --no-deps` 或 vendor 方式，但需在 PR 说明理由。

---

## 🙏 致谢

所有贡献者的名字将出现在 Release Notes 中。感谢每一行代码、每一个 Issue、每一条建议！

---

> **EggBloom Desktop** — 让 AI 陪伴更自然、更贴心、更安全。