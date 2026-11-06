# AIGC 远端推理方案（可选扩展，不是主线）

> 2026-11-06 定稿。适用 1.6.0 P1.5 的全模态工作面（生图 / 语音 / 视频 / 素材）。  
> **优先级（2026-11-06 00:35 拍板）：本地模型为主。** 1.6.0 的默认形态是  
> 「模型跑在 bot 这台机器上」（`data/aigc/venv` + `data/models/`，见 §0.0）。  
> 本文描述的是**同一套任务层**换成远端后端时的做法，用于以后搬到推理机 /  
> 云 GPU，**现在不实现**。保留它的原因：契约不依赖部署位置，先定下来，  
> 将来搬的时候 bot 侧只换一个 backend，页面和接口一行不用改。  
> 本文只回答：接口长什么样、bot 侧改哪几个文件、远端怎么搭、失败怎么办。

---

## 0.0 默认形态：本地模型（当前主线）

```
页面 → /api/aigc/* → aigc_task(任务层) → local backend
                                          ├─ tts: piper（data/aigc/venv 子进程）
                                          ├─ asr: faster-whisper（同上）
                                          └─ image: ComfyUI（本机 127.0.0.1:8188）
```

- 依赖与权重都不进主解释器：venv 在 `data/aigc/venv`，权重在  
  `data/models/{piper,whisper}/`，各自带 `_installed.json`
- 装机一条命令：`python scripts/install_aigc.py --asr base`
- 关掉即零请求（`aigc_enable=false` 时连后端函数都不调用，验收第 6 条）
- 下面第 1 节之后的内容，**只在要把模型搬到另一台机器时才需要看**

---

## 0. 一句话结论

**别按各家原生 API 去接，先把「任务」这一层做成契约，远端实现契约。**

我们现在已经有 `harness/aigc_task.py`（状态机 / 产物目录 / 配额 / 后端注册表，  
31 条用例）。远端只要实现**同一套语义**的 4 个端点，bot 侧只加一个  
`RemoteBackend`，页面、`/api/aigc/*`、配额、日志一行都不用改。

|   |   |
| - | - |



```
页面 → webui_server(/api/aigc/*) → aigc_task(任务层) → [local|remote] backend
                                                          │
                                                          └─ HTTP → 远端网关 → ComfyUI / piper / whisper
```

三层本地模型为主职责不混：**接口层只做 HTTP**、**任务层只做状态**、**后端层只做调用**。  
这也是本仓库吃过教训后的惯例（`_llm_request_headers` 那次就是"两条链路各写一套"）。

---

## 1. 为什么值得单独做契约，而不是"直接调 ComfyUI"

| 直接调原生 API                                                                                | 走契约                          |
| ---------------------------------------------------------------------------------------- | ---------------------------- |
| 生图是「提交 prompt_id → 轮询 history → 按 filename 取图」，语音是「HTTP 一段 wav」，ASR 是「上传文件等文本」——**三种形状** | 三种都是「提交 → 看状态 → 拿产物」**一种形状** |
| 换远端（换机器 / 换 provider / 换模型）要改 bot 代码                                                     | 换远端只改 base_url               |
| 超时、重试、断线各自实现一遍，必然漂移                                                                      | 只实现一次                        |
| 前端要知道每种后端在哪一步                                                                            | 前端只认 task id                 |

对齐的正是 `aigc_task.py` 头注释里那句：**「这一层只管任务，不管模型」**。

---

## 2. 远端契约（v1）

基址 `aigc_remote_url`，全部 `Content-Type: application/json`，全部带  
`Authorization: Bearer <token>`。路径前缀 `/v1`。

### 2.1 健康检查

```
GET /v1/health
→ 200 {"ok": true,
       "version": "1.0",
       "gpu": {"name": "RTX 4090", "vram_total_mb": 24564, "vram_free_mb": 20311},
       "models": {"image": ["sdxl-base"], "tts": ["zh_CN-huayan-medium"],
                  "asr": ["small", "base"]},
       "queue": {"running": 1, "pending": 0}}
```

bot 用它做三件事：页面显示「远端在线 / 离线」、决定某个 kind 有没有可用后端、  
失败时给出「远端没有这个模型」而不是一句「连接失败」。

### 2.2 提交任务

```
POST /v1/tasks
Idempotency-Key: <bot 侧任务 id>
{"kind": "tts" | "asr" | "image" | "video",
 "params": {...},
 "deadline_seconds": 300}

→ 202 {"task_id": "r-7f3a…", "status": "pending"}
→ 400 {"error": {"code": "bad_params", "message": "…"}}
→ 409 同 Idempotency-Key 已存在 → 返回已有 task_id（幂等，不重复跑）
```

`params` 保持**与本地后端同一份字段名**（这样切远端不用改调用方）：

| kind    | params                                                     |
| ------- | ---------------------------------------------------------- |
| `tts`   | `text`, `voice?`, `length_scale?`                          |
| `asr`   | `audio_url` **或** `audio_b64`, `language?`, `model?`       |
| `image` | `prompt`, `negative?`, `width`, `height`, `steps`, `seed?` |
| `video` | 1.6.x 再定                                                   |

⚠ ASR 与「本地后端吃本地路径」不同：远端拿不到 bot 的盘。所以远端参数用  
**`audio_url` 或 `audio_b64`**，由 `RemoteBackend` 负责把本地文件变成其中一种  
（≤8MB 走 base64，大了先传到远端 `/v1/uploads`，见 §2.5）。

### 2.3 查任务

```
GET /v1/tasks/{task_id}
→ 200 {"task_id": "…", "status": "pending|running|done|failed|rejected",
       "progress": 0.42, "note": "采样 12/20",
       "error": null,
       "artifacts": [{"name": "out.wav", "url": "/v1/artifacts/…",
                      "bytes": 207404, "sha256": "…", "mime": "audio/wav"}],
       "meta": {"duration": 4.7, "sample_rate": 22050, "voice": "…",
                "model": "small", "cost_ms": 812}}
```

- 状态词**必须**和 `aigc_task` 的五个词一致（`pending/running/done/failed/rejected`），  
  否则 bot 侧要写一张映射表 —— 那张表迟早会漏一个值（仓库里 `observe` 漏判  
  就是这么来的）。
- `progress` 单调不减；不知道就报 `null`，别报假数。
- `artifacts[].url` 可以是相对路径（拼 base_url）或绝对 URL。

### 2.4 删除任务

```
DELETE /v1/tasks/{task_id} → 204
```

### 2.5 上传（只在需要时）

```
POST /v1/uploads  (multipart 或裸二进制)
→ 200 {"upload_id": "u-…", "url": "/v1/uploads/u-…"}
```

仅 ASR/视频这类"要送大文件"的场景用。TTS 与生图不需要。

### 2.6 产物获取：三种模式，按部署形态选

| 模式         | 做法                                                   | 适合          | 代价                            |
| ---------- | ---------------------------------------------------- | ----------- | ----------------------------- |
| **拉取（默认）** | `artifacts[].url`，bot 下载到 `data/media/<kind>/<day>/` | 跨机、跨网       | 占带宽；要校验 sha256                |
| 内联         | `artifact_b64` 直接放响应里                                | 语音小文件（<2MB） | 响应体膨胀，大图不可用                   |
| 共享目录       | 远端写 NFS/SMB，bot 直接读路径                                | 同一局域网且已挂载   | 依赖 OS 权限与中文路径（本机踩过 espeak 的坑） |

**推荐拉取**：bot 侧已有 `_save_artifact()` 的落盘逻辑，下载完写进同一条路径，  
`/api/aigc/file?id=…` 直接能放。sha256 不一致 → 任务判 `failed`，不重试（见 §4）。

---

## 3. bot 侧要改什么（最小集，5 个文件）

### 3.1 新增 `harness/aigc_remote.py`（约 200 行）

```python
class RemoteBackend:
    """把一个远端任务包装成本地 backend 契约：fn(task, params, ctx) -> {artifact, ext, meta}"""

    def __init__(self, base_url, token, kind, *, timeout=600, upload_threshold=8 << 20):
        ...

    def __call__(self, task, params, ctx):
        tid = self._submit(task, params)          # Idempotency-Key = task["id"]
        while True:                               # 轮询，指数退避 0.5→5s，封顶 deadline
            st = self._get(tid)
            ctx.report(st.get("progress") or 0, st.get("note") or "")
            if st["status"] in ("done", "failed", "rejected"):
                break
            time.sleep(self._next_sleep())
        if st["status"] != "done":
            raise RuntimeError(st.get("error") or st["status"])
        art = st["artifacts"][0]
        local = self._download(art)               # 校验 sha256 + 大小
        return {"artifact": local,
                "ext": os.path.splitext(local)[1],
                "meta": {**st.get("meta", {}), "remote_task": tid,
                         "remote_bytes": art["bytes"]}}
```

关键点（都是踩过的坑，写进注释）：

- **轮询不阻塞消息链路**：`aigc_task` 本来就在 daemon 线程里跑 backend，  
  所以这里 `sleep` 是安全的；但**不要**在 webui 请求线程里同步等。
- **断线续查**：网络抖动时不要重新提交（会重复计费/重复跑），保留 `tid` 继续 `GET`。
- **时钟**：超时判断用 `time.monotonic()`，别用 `time.time()` —— 远端时钟偏移  
  与本机 NTP 跳变都会让"还剩多久"算错（仓库里已有一处吃过墙钟的亏）。

### 3.2 `harness/aigc_backends.py`：加远端装配

```python
def bootstrap(service=None, root="", force=False):
    caps = capabilities(root)              # 本机
    rcaps = remote_capabilities(root)      # 远端 /v1/health（带 60s 缓存）
    for kind in (KIND_IMAGE, KIND_TTS, KIND_ASR):
        if _backend_choice(kind) == "remote" and kind in rcaps["kinds"]:
            service.register_backend(kind, RemoteBackend(...))
        elif _backend_choice(kind) == "local" and caps.get(...):
            service.register_backend(kind, local_backend)
```

- `_backend_choice(kind)` 读 `aigc_<kind>_backend`：`remote` / `local` / `""`（不接）。
- **同一 kind 只挂一个后端**。同时挂两个会变成"谁先注册谁生效"，不可预测。
- 远端不可达时：**不降级到本地**（除非显式配 `aigc_fallback_local=true`）。  
  理由：模型不同、产物风格不同，静默换后端比报错更难查。

### 3.3 `webui_server.py`：只加一个状态端点

`/api/aigc/caps` 的返回里已经有 `caps`；把远端探测结果并进去：

```json
{"ok": true,
 "caps": {"venv": false, "piper": false, "faster_whisper": false,
          "remote": {"ok": true, "url": "http://10.0.0.5:8800",
                     "gpu": "RTX 4090", "models": {"tts": ["zh_CN-huayan-medium"], "asr": ["small"]},
                     "latency_ms": 12}}}
```

页面「语音」页签据此显示 `远端在线 · piper: zh_CN-huayan-medium`。  
**没有远端也没本地时不显示"规划中"，显示"未配置后端"+ 该怎么配的那一行。**

### 3.4 配置项（在现有 `aigc_*` 上追加）

```jsonc
"aigc_enable": false,
"aigc_tts_backend": "remote",        // remote / local / ""（不接）
"aigc_asr_backend": "remote",
"aigc_image_backend": "remote",
"aigc_remote_url": "http://10.0.0.5:8800",
"aigc_remote_token": "",             // 只在本机 bot_config.json，不进仓库
"aigc_remote_timeout": 600,          // 秒，单任务上限
"aigc_remote_verify_tls": true,      // 用 https 时别关
"aigc_fallback_local": false,        // 远端挂了要不要退回本机（默认不）
```

沿用 `scripts/export_config_docs.py` 生成到 `docs/配置项说明.md`，  
配置页也要跟（一致性由 `audit_config_parity.py` 兜）。

### 3.5 测试（不碰网络）

`tests/test_aigc_remote.py`：

| 判据                  | 做法                                                 |
| ------------------- | -------------------------------------------------- |
| 提交带 Idempotency-Key | 起一个假 HTTP server（`http.server`），断言头部               |
| 409 幂等              | 假服务回 409 + 已有 id，断言 bot 不重复提交、继续轮询                 |
| 状态词五选一              | 假服务返回上游乱写的 `"success"` → bot 明确报"未知状态"，不猜测         |
| 断线续查                | 中途回 500 两次再回 done → 断言只用了一个 task_id                |
| 产物校验                | sha256 对不上 → `failed` + 原因含 "sha256"               |
| 超时                  | deadline 很小 + 假服务永远 running → 断言按 monotonc 计时退出    |
| 远端全 Off             | `/v1/health` 拒连 → caps.remote.ok=false，提交返回"远端不可达" |

现有 `tests/test_aigc_backends.py`（27 条，本地后端）保留，本地后端作为  
**dev 兜底**继续存在（默认不挂）。

---

## 4. 失败与降级（明确写死，不留"看情况"）

| 情形                            | 行为                              | 理由                    |
| ----------------------------- | ------------------------------- | --------------------- |
| 远端连不上（连接被拒/DNS）               | 任务 `failed`，原因「远端不可达: <url>」    | 立刻可见；不排队堆积            |
| 远端 5xx                        | 同一任务**最多重试 1 次**（只对 5xx，不对 4xx） | 5xx 可能是瞬时；4xx 重试只会重复错 |
| 轮询超时（> `aigc_remote_timeout`） | `failed`，原因「远端超时（600s）」         | 远端队列卡死时不能把 bot 拖死     |
| 产物 sha256 不符                  | `failed`，**删掉半成品**              | 半截文件比失败更坏（会被当成真产物发出去） |
| 远端说 `rejected`（超额/未启用）        | 原样映射 `rejected` + 原因            | 与本地配额语义一致             |
| 远端重启导致 task 丢失（404）           | `failed`，原因「远端任务已丢失（可能重启）」      | 不自动重提交：可能是用户主动取消的那条   |
| 网络抖动但 task 还在                 | 继续轮询（退避 5s 封顶）                  | 不重复提交                 |


**不做的**：自动换机器、自动降级到本地、静默重试三次。这三种都会让"为什么这次  
出来的图和上次不一样"变成不可回答的问题。

---

## 5. 远端那台机器怎么搭

### 5.1 推荐拓扑

```
[bot 主机 Windows]                        [推理机 Linux + GPU]
 bot_core / webui_server                    aigc_gateway (FastAPI :8800)
   └ aigc_task ──HTTP──►  ───────────────►    ├─ ComfyUI  (:8188, 常驻)
                                            ├─ piper    (进程内调用, CPU 也行)
                                            └─ whisper  (faster-whisper, GPU 更快)
```

- **网关是唯一入口**，只暴露 8800；ComfyUI 只监听 127.0.0.1。
- 组网：内网直连即可；跨公网一律走 **WireGuard / Tailscale**，不要做端口映射 + 口令。
- 网关自身无状态（任务状态可只存内存 + 落一份 json，重启丢任务可接受，  
  bot 侧会判 `failed`，见 §4）。

### 5.2 网关骨架（FastAPI，约 200 行）

```python
@app.post("/v1/tasks", status_code=202)
def submit(body: TaskIn, idem: str = Header(None, alias="Idempotency-Key")):
    with IDEM_LOCK:
        if idem and idem in IDEM:
            return {"task_id": IDEM[idem], "status": "pending"}
    tid = "r-" + uuid4().hex[:12]
    if idem: IDEM[idem] = tid
    TASKS[tid] = {"status": "pending", "progress": 0.0, "artifacts": []}
    threading.Thread(target=_run, args=(tid, body), daemon=True).start()
    return {"task_id": tid, "status": "pending"}

@app.get("/v1/tasks/{tid}")
def get(tid: str):
    t = TASKS.get(tid) or abort(404)
    return {"task_id": tid, **t}
```

`_run` 里分派三个实现：

- `image`：ComfyUI `/prompt` → 轮询 `/history/{pid}` → 取 `/view?filename=…`
- `tts`：`piper` 写 wav（注意 espeak 数据目录要 ASCII 路径，见 §6）
- `asr`：`faster-whisper` 转写 → 写 txt

### 5.3 远端机器要求（给个下限）

| 用途           | 最低                                      | 舒服         |
| ------------ | --------------------------------------- | ---------- |
| 只做 TTS + ASR | 4 核 / 8GB / 无 GPU（base 模型 CPU 约 1:6 实时） | 8 核 / 16GB |
| 加生图          | 12GB 显存（SDXL 1024×1536 需 ~10GB）         | 24GB       |
| 磁盘           | 模型 + 产物缓存 50GB                          | 200GB      |

---

## 6. 已知坑（直接抄结论）

1. **中文路径**：espeak-ng 是 C 实现、窄字符 fopen，路径里有中文就报  
   `Illegal byte sequence`，且报错信息会伪装成构建机路径（`D:/a/piper1-gpl/...`），  
   极具误导性。远端如果是 Linux 一般没事；如果远端也是 Windows，把数据目录  
   放到纯 ASCII 路径。
2. **pip 源**：清华源对 pip 26 的 UA 返回 403（表现为 `from versions: none`），  
   装依赖用官方 `https://pypi.org/simple`。
3. **HF 权重**：`huggingface.co` 国内不通，用 `hf-mirror.com`；`hf_xet` 反而更慢，  
   设 `HF_HUB_DISABLE_XET=1`。大文件用 curl 直下比 `huggingface_hub` 快 2~3 倍。
4. **不要用 base64 传大图**：JSON 膨胀 33%，且会让 webui 的响应缓冲翻倍。
5. **产物命名**：远端返回 `name` 只作提示，**落盘文件名一律用 bot 侧 task_id**，  
   避免远端路径穿越（`../`）与中文名问题。

---

## 7. 与 1.6.0 的交付边界


| 版本 | 做什么 | 不做什么 |
|---|---|---|
| **1.6.0** | 契约 v1 + `aigc_remote.py` + `/api/aigc/*` + 页面四页签（语音可用）+ caps 里显示远端状态 + 7 条远端用例 | 队列调度、多机、SSE 流式进度、远端计费 |
| 1.6.1 | SSE / 长轮询、产物 sha256 强校验默认开、远端配额下沉 | 模型热切换 |
| 1.6.x | 视频页签、多机选择（按 kind 选机器） | 自建训练/微调 |

**本地后端（`aigc_worker.py` / piper / faster-whisper）保留但默认不挂**，
只作为开发机自测与"远端挂了临时顶一下"的手段。它的 27 条用例继续跑，
保证这条路不会烂掉。

---

## 8. 需要你拍板的四件事

| # | 事项 | 我的建议 |
|---|---|---|
| 1 | 远端 OS / 显卡 | Linux + 一张 ≥12GB 显存的卡（生图才是瓶颈；TTS/ASR 核显都行） |
| 2 | 是否常驻 | 常驻。按需开机会让"第一次生成"多等 1~2 分钟，体验断崖 |
| 3 | 网络 | 内网直连；跨公网走 Tailscale，不开公网端口 |
| 4 | 产物存哪 | 远端只留 24h 缓存，长期留在 bot 主机的 `data/media/`（备份策略不变） |

另：本机现在有 `data/aigc/venv`（约 150MB）和 `data/models/piper`（63MB）。
按本方案它们只是 dev 兜底 —— **留着**（不占多少）还是**删掉**，你定，我照做。
