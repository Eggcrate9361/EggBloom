# EggBloom Desktop Help

## Overview
EggBloom Desktop is a local QQ bot manager with a web dashboard, protocol management, and AI chat.

## Start
Double-click `EggBloom Desktop.exe` (the shell starts the backend and opens the UI).
Running from source: `python webui/webui_server.py`, then open http://localhost:8080.

## First Run Setup
Complete the setup wizard for:
- AI provider, API URL, API key, and model
- QQ path (optional)
- Bot QQ number

You can also configure these later in Settings.

## Features
- Dashboard: bot status, uptime, message counts, memory usage
- Settings: AI, monitoring targets, group IDs, behavior
- Logs: searchable and filterable runtime logs
- Console: run commands, view learning status, group history, private memory
- Protocol: start/stop protocol layer and view logs
- Plugins: browse and toggle plugins
- WebUI: browser-based management dashboard
- Help: usage docs inside the app

## Notes
- Requires Python 3.10+ and Node.js
- Protocol layer requires QQNT and PMHQ
- Do not delete `AppData/Local/EggBloom_v3` or config and logs will be lost
