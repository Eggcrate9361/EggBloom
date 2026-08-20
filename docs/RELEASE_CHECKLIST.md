# EggBloom Desktop 上线清单

目标：发到网上能让陌生人点开就用，不是商店上架。
最后更新：2026-08-01

---

## 一、必须先补齐

- [x] 1. `app-src/package.json` 打包配置可用
- [x] 2. `start.bat` 改为相对路径 + 自动 venv
- [x] 3. WebUI 服务可被普通用户正常启动
  - 已验证 `python app-src\webui\webui_server.py` 可启动
  - `/api/status` 返回 200
  - `/` 返回 184KB 前端 HTML
  - `/desktop` 返回占位页（`desktop-ui/index.html` 未同步到本目录，但主入口 `/` 正常）
- [x] 4. 协议层环境自检（Node.js / ws / PMHQ / QQNT）
  - 新增 `ProtocolManager._preflight()`：检查插件文件、Node.js 可执行性、`ws` 模块、QQ 路径
  - 修复 `_kill_all` 未定义崩溃
- [x] 5. 首次配置向导（API Key / QQ 路径 / 模型 / Bot QQ）
  - 新增 `wizard.py`：3 步向导，保存到 `config.py` + `.env`
  - `MainWindow._ensure_first_run()` 在启动时自动触发
- [x] 6. Bot / 协议层异常自动重启
  - 新增 `MainWindow._bot_watchdog()`：检测 Bot 进程非零退出后自动重启
  - 协议层已有 watchdog / QQ 自愈逻辑
  - 注意：此改动未做 GUI 回归验证，仅完成静态校验
- [x] 7. 崩溃日志收集
  - `bot_core.py` 增加 `sys.excepthook` / `threading.excepthook`
  - 已验证未捕获异常写入 `AppData/Local/EggBloom_v3/qq_bot.log`
- [x] 8. 隐私 / 许可 / 使用说明
  - 新增 `docs/USAGE.md`：启动、首次配置、功能、注意事项
- [x] 9. 打包产物与校验/签名说明
  - `app-src/package.json` 已改为 `icon-large.png`
  - 新增 `docs/PACKAGING.md`：依赖、构建、签名、校验说明

## 二、强烈建议顺手做

- [x] 10. 统一启动器（一个入口启动全部）
  - `start.bat`：Python 检查、venv、WebUI、Electron 回退 `main_window.py`
- [x] 11. 协议层常驻 / 后台管理
  - `main_window.py` 新增 `pystray` 托盘：最小化后台、托盘启停 Bot / 退出
  - `requirements.txt` 补充 `pystray`、`Pillow`
- [x] 12. 端口冲突 / 多开保护
  - `main_window.py` 新增 `eggbloom.lock` 单实例锁
  - `webui_server.py` 增加 `allow_reuse_address` 和 `EADDRINUSE` 备用端口
- [x] 13. 最小帮助 / 使用说明
  - 侧栏新增“帮助”页，直接展示 `docs/USAGE.md`

## 三、可后补

- [ ] 14. 自动更新
- [x] 15. 多语言
  - 新增 `ui_themes.py`：`default/light/warm` 主题，`zh/en` 语言包
  - 设置页新增“主题 / 语言”下拉，保存后应用
  - 新增 `docs/USAGE_zh.md`、`docs/USAGE_en.md`
- [x] 16. 主题 / 皮肤
  - 新增 3 套主题并持久化到 `ui_theme`
  - 启动时按配置应用主题和 CTk appearance mode
