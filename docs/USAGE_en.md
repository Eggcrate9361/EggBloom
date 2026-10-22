# EggBloom Desktop — User Guide

> For operators: how to install, configure, and what each page does.
> For development see [`PLUGIN_DEV.md`](PLUGIN_DEV.md); for builds see [`发布打包.md`](发布打包.md).

## 1. What it is

An AI assistant that lives in QQ groups and direct messages, with a local web console.

- Protocol: OneBot v11 (SnowLuma / NapCat / LLOneBot, or any standard implementation)
- Models: DeepSeek / OpenAI / Ollama / LM Studio / llama-server, plus 14 preset providers
- Shape: Electron shell + **a separate Python backend** (two processes — see §7)

**This repository ships no QQ protocol implementation** and distributes no protocol driver.
Connecting to QQ may violate the QQ Terms of Service; use at your own risk.

## 2. Requirements

| Scenario | Requirement |
|---|---|
| Release build (Standard / Full) | Windows 10/11, extract and run — Python runtime is bundled |
| From source (Lite) | Python **3.13** (`requires-python >=3.13,<3.14`) |
| Protocol driver | Installed separately, with OneBot v11 HTTP/WS enabled |
| Building / packaging | Node.js (Electron shell) + 7z |

## 3. Start

**A. Release build (recommended)**

1. Extract `EggBloom-Standard.zip` (the zip contains a single top-level folder)
2. Double-click `EggBloom Desktop.exe`
3. The shell launches the Python backend and opens the UI; or open `http://localhost:8080`

**B. From source**

```bash
git clone https://github.com/Eggcrate9361/EggBloom.git
cd EggBloom
pip install -e .
python webui/webui_server.py
```

**C. `start.bat`** shipped with release builds: same as B, plus environment checks
(not present in the source repo).

## 4. First-run setup

The wizard asks for six fields:

| Field | Notes |
|---|---|
| AI provider | DeepSeek / OpenAI / Ollama / others (14 presets) |
| API URL | Change this when using a proxy or self-hosted endpoint |
| API key | Stored in **Windows Credential Manager**, never written to disk in plaintext |
| Model name | Determines the context window — see §6.4 |
| QQ path | Optional; needed only to launch the QQ client |
| Bot QQ number | The bot's own account |

You can skip the wizard and fill these in later under **Settings**. Start the WS server on your
protocol driver first, then paste its address into the **Protocol** page.

## 5. The 15 console pages

| Page | Purpose |
|---|---|
| Dashboard | Overview: status, message volume, today's tokens |
| Protocol | Connections and drivers: status, start/stop, logs |
| Groups | Group list and switches: allowlist, card names, mute/kick |
| Memes | Asset library: browse, enable/disable, delete |
| Image gen | ComfyUI generation |
| Group behavior | Speaking timing: reply window, lurking, scoring |
| Token | Usage and budget: totals, cache hits, context pressure |
| Settings | Parameters: API, behavior, watched targets, feature switches |
| Bot | Account and status |
| Logs | Conversation records: searchable, filterable |
| Notifications | Monitoring and alerts |
| Console | Low-level command entry |
| Memory | Sandglass and persona: tiered memory, recall by date |
| Plugins | Hot-loaded extensions, self-authored plugin approval |
| Agent | Sessions, streaming runs, tool-call replay |

The legacy v3 UI is still available for side-by-side comparison; `ui_version.txt` selects the
default, and release builds deliberately omit that file.

## 6. Common tasks

### 6.1 Change model or provider

Set `api_provider` and `model_name` under **Settings**. API keys are stored **per provider**,
so switching never reuses the previous provider's key.

### 6.2 Tune how chatty the bot is

**Group behavior** exposes scoring parameters and the reply window. Change the reply window
first — it is the easiest to observe.

### 6.3 Read usage

**Token** shows two different things: **totals** (what you spent today) and **context pressure**
(how full this request is). Pressure is judged on two tracks:

- Window track: 60% of the model window warns, 85% is high;
- Cost track: a single prompt at 32k warns, 128k is high.

The cost track exists because with a 1M window the ratio track would not warn until 600k tokens —
by which point a single call is already expensive.

### 6.4 Context window

`model_caps.py` resolves the window from the model name (`deepseek-flash` → 1M).
Override it with `EGGBLOOM_CTX_WINDOW` if needed. The same value drives tool-result budgets and
the context gate; no manual tuning required.

### 6.5 Plugins

Browse and toggle under **Plugins**. AI-authored plugins land in `plugins/_pending/`
(underscore prefix — never auto-loaded). An admin is notified and approves in the **Agent** page
before the plugin moves into `plugins/` and hot-loads.

## 7. Data, logs, and "why didn't my code change take effect"

| Content | Path |
|---|---|
| Config | `bot_config.json` (program dir first, `%LOCALAPPDATA%\EggBloom_v3` as fallback) |
| Runtime log | `%LOCALAPPDATA%\EggBloom_v3\qq_bot.log` |
| Token stats | `logs/token_stats.jsonl` under the program dir |
| API keys | Windows Credential Manager (`cred_store.py`) |
| Memory | Sandglass: `NEXSANDBASE_HOME` → `<program dir>/.neurobase` → `~/.neurobase` |

**Source changes take two steps**, and skipping either fails silently:

```
source repo  --scripts/sync-dist.ps1-->  release dir resources\app
                                            │
                                            └── restart the Python backend (required)
```

- Electron is only a shell; it holds no business logic. Restarting it alone changes nothing.
- Sync uses robocopy `/E` (add-only). Pass `-DistPath "<release root>"` explicitly.
- No need to clear `__pycache__`: Python recompiles when source mtime changes.
- One command that syncs and verifies: `python scripts/activate.py --dist "<release root>"`

## 8. Troubleshooting

**Port 8080 won't open** — the backend process isn't running. Check the tail of `qq_bot.log`.

**Code changes have no effect** — see §7; nine times out of ten the backend wasn't restarted.

**HTTP 400 context length exceeded** — 4xx is treated as non-retryable by
`harness.retry_policy`, so the whole reply is dropped rather than retried. Usual causes:
oversized persona file, several large images in one turn, or a tool chain that filled the context.
The tool loop now has a total gate that forces a final answer near the limit, but persona and
image sizes are still yours to control.

**Protocol won't connect** — confirm the driver's WS server is running and the address/port match.
The **Protocol** page shows per-driver status and logs.

**Moving to a new machine** — carry `bot_config.json` and `%LOCALAPPDATA%\EggBloom_v3`.
API keys live in Credential Manager and must be re-entered.

## 9. Layout

```
EggBloom/
├── bot_core.py              Bot core (messages, AI dialogue, event branches)
├── model_caps.py            Single source for context window / output limits
├── harness/                 Agent framework (event bus, tool registry, budgets, MCP)
├── plugins/                 Hot-loaded plugins
├── protocols/               OneBot v11 adapters
├── webui/                   Console frontend + HTTP server
├── sandglass_lite.py        Sandglass memory engine
└── scripts/                 Sync, packaging, verification, dev tools
```
