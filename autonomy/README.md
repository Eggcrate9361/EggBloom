# autonomy —— 对话驱动的能力获取

> 参考了 DSH SuperAgent 底座的四个结构原则：
> **事件触发、隔离执行、持久任务、可审计交付**。
> 这里不复制它的代码，只把原则落到 EggBloom 的本地工具链上。

## 七段

```
capability_gap.py  察觉：工具缺失 / 依赖失败 / 自述不足
probe.py          探测：EnvReport（OS/Python/磁盘/网络/已装）
plan.py           计划：pip / model / bin / plugin 四类落点 + 磁盘预检
acquire.py        执行：断点续传、sha256、独立 venv、pip install
verify.py         自验：文件/时长/PNG/ASR 闭环/插件只读 action
wire.py           接线：_installed.json、插件清单、回滚快照
report.py         交付：装成了/没装成，结论 + 一个可验证细节
loop.py           串起来；失败两次熔断 30 天
runner.py         长任务入口：由 job_start 拉起，job_wait 一次等到结束
```

## 真实后端（不是 AIGC）

| need | 默认装什么 | 用来做什么 |
|---|---|---|
| `ocr` | `rapidocr_onnxruntime`（本地） | 读图里的字；没装/没字再退主模型视觉 |
| `ffmpeg` / `video` | `imageio-ffmpeg`（带二进制） | 抽视频 4 帧；`harness/video_frames` 会接 autonomy venv |
| `asr` | `faster-whisper` | 语音转文字 |
| `tts` | `edge-tts` | 文字转语音 |

OCR 与 ffmpeg 都只做「读」：OCR 读文字、ffmpeg 抽帧。发视频走 `bot_core.send_video`
的 OneBot video 段，也不是生成。

## 1.6.0 P3：判据归位 + 三段闭环

| 文件 | 职责 |
|---|---|
| `policy.py` | 权限等级表 / `guard()` / 心跳提示词，**唯一真相源**（原在 `plugins/autonomy/policy.py`） |
| `ledger.py` | 会话状态与待回复账本（原在 `harness/proactivity/ledger.py`） |
| `heartbeat.py` | 心跳材料 → 提示词 → 裁决；分离态由 `separation.bootstrap()` 登记工具 |
| `devloop.py` | 改已有代码 → 重启 → 回归 三段 DAG，默认只预演 |

两个老路径只做转发，**不要在兼容壳里加逻辑**。`plugins/autonomy/` 故意不放
`__init__.py`：`plugin_manager` 把 `plugins/` 插进 `sys.path`，补了它就会以顶层
`autonomy` 包遮住本包。判据、心跳、账本都从根下 `autonomy/` 导入。

`devloop` 的硬约束：默认 `confirm=False` 一律 `PreviewBackend`（`applied` 恒 False）；
`confirm=True` 且未显式注入 backend 直接拒绝；本层不自带改码 / 重启 / 回归实现。
任何一段失败即 `backend.rollback()` 并提前终止，逐段 trace 随运行态落盘。

`harness/plugin_forge.py` 的创作提交 / 审批也接了本包的 workflow 编排层
（五节点静态自验，详见该文件头部）。

## 与分离态的关系

- 自主层**不需要**分离态也能跑，但只有分离态会在 agent 工具组里常驻（`GROUP_AUTONOMY`）；
- 产品态不自动开；要装东西得人明确要求；
- 所有安装记录写 `<data>/autonomy/_installed.json`，没有记录的东西视为不存在；
- 每次动作写 `logs/separation-audit.jsonl`（与分离审计同一份）。

## 测试

```bash
python tests/test_autonomy.py
python tests/test_job_persistence.py
python tests/test_ocr.py
python tests/test_video_interface.py
```

默认全离线：网络、下载、识别、安装都注入假实现。
`tests/test_autonomy_live.py` 起本机 HTTP 走真实下载/续传；
真实 RapidOCR / 主模型视觉 / ffmpeg 抽帧已在本机临时 venv 实测过。
