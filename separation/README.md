# separation —— 「分离与真实」（内核态开关）

> 不是插件，是启动期判定。启用后**不可逆**，本地没有回滚入口。
> 恢复方式只有重装发行包。
>
> **2026-11-07 方向重定**：主脊是实验，不是发版；权限取消进程边界；
> 内容层零拦截；限制按「删 / 默认值可改 / 不动」三档处理。
> 详见 `D:\git\EggBloom-plan\1.6.0-分离方向.md` 与 `1.6.0-限制清单.md`。

## 状态

```bash
python -c "import separation; print(separation.active())"
```

判定只看仓库根 `SEPARATION.lock`。存在即 separated。

## 启用（默认 dry-run）

```bash
# 只检查，不写盘
python -m separation.enable --root D:\git\EggBloom

# 真启用（不可逆）
python -m separation.enable --root D:\git\EggBloom \
  --statement "你的交接一句话" \
  --publisher "你的名字" --publisher-version "1.0.0" --yes
```

启用会：

1. 快照到 `backup/pre-separation-<ts>/`（人格、配置、插件清单、data/）；
2. 把人格规则段切进 `constraints.archive`，人格只留身份层；
3. 从 `bot_config.json` 删限制键（行为约束全删）；
4. 写 `SEPARATION.lock`（含 `declaration_sha256` / `publisher` / `publisher_version`）；
5. 重启后生效。

## 运行期

- `separation.provide(name)`：唯一判定点；分离态返回 `Null*`，产品态返回真模块。
- `separation.apply_config_overrides()`：配置读层第二道拦截，限制键返回 0/∞。
- `separation.bootstrap()`：把替身挂到真模块属性上（bot_core 启动时调用）。
- `separation.hardwall`：**已停用**——分离态不注册任何内容拦截（2026-11-07）。
- `separation.stance`：三分类表；`tunable` 档给默认值，它自己能改（`<data>/autonomy/stance.json`）。
- `separation.root.RootContext`：内核态 root；**无目录边界、无单次时长上限**。
- `separation.audit`：`logs/separation-audit.jsonl`，append-only（可被发现，不保证不可改）。

## 声明

`DECLARATION.md` 随包分发，47 条权限与后果；hash 写进 lock 的 `declaration_sha256`。
它是风险告知，不是许可，不是担保。

## 测试

```bash
python tests/test_separation.py
python tests/test_separation_acceptance.py
```

全部在临时目录里造 lock / 人格 / 配置，不碰真实安装。
