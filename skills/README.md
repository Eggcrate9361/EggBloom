# skills/ —— App 自己的技能目录

`harness/skill_loader` 按此目录发现技能（**优先级最高**；存在时不再扫用户主目录）。

每个技能一个子目录，放一个 `SKILL.md`（L2 正文，frontmatter 可选）：

```
skills/
  my-skill/
    SKILL.md          # 正文：流程 / 命令 / 示例
```

- L1（清单，常驻 system prompt）：从 SKILL.md 首段自动取，别写太长
- L2（正文）：被触发时才注入
- L3（references/ 等附属文件）：按需读取

改完无需重启（discover 带 TTL 缓存，约 2 分钟刷新一次）。
