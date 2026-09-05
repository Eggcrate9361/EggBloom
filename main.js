const { app, BrowserWindow, ipcMain, Tray, Menu, nativeImage } = require('electron');
const path = require('path');
const { spawn, execSync } = require('child_process');
const http = require('http');
const fs = require('fs');

// 单实例锁 — 防止重复启动
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) { app.quit(); } else {
  app.on('second-instance', () => { if (win && !win.isDestroyed()) { if (win.isMinimized()) win.restore(); win.show(); win.focus(); } });
}

try {
  app.commandLine.appendSwitch('disable-disk-cache');
  app.commandLine.appendSwitch('disable-http-cache');
} catch (e) {}

// 启动前清理本应用遗留的 python 进程（只按应用目录过滤，绝不无差别杀全机 python/node）
function killOldProcesses() {
  try {
    // 只匹配本包/源码目录下的 python 进程（bundled python 位于包根 python\ 或 resources\app\python）
    const roots = [path.join(__dirname, '..', '..'), __dirname].map(p => p.replace(/\\/g, '\\\\')).join('|');
    const ps = `powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -match '${roots}' -and $_.Name -match 'python' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"`;
    execSync(ps, { timeout: 5000, stdio: 'ignore' });
  } catch (e) {}
}

const LOG_PATH = path.join(app.getPath('userData'), 'server-spawn.log');
function logLine(line) {
  try { fs.appendFileSync(LOG_PATH, `${new Date().toISOString()} ${line}\n`, 'utf8'); } catch (e) {}
}

let win = null;
let server = null;
let tray = null;
let isQuitting = false;
let dialogWin = null;

function waitForServer(url, timeout = 10000) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const interval = setInterval(() => {
      const req = http.get(url, (res) => {
        req.destroy();
        if (res.statusCode === 200) { clearInterval(interval); resolve(); }
      });
      req.setTimeout(1200, () => req.destroy()); // 连接挂起时强制断开，避免永不 reject
      req.on('error', () => {});
      if (Date.now() - start > timeout) { clearInterval(interval); reject(new Error('Server did not become ready in time')); }
    }, 500);
  });
}

function createTray() {
  try {
    const iconPath = path.join(__dirname, 'tray-icon.png');
    if (!fs.existsSync(iconPath)) { logLine('tray-icon.png not found at ' + iconPath); return; }
    tray = new Tray(nativeImage.createFromPath(iconPath));
    tray.setToolTip('EggBloom Desktop');
    const showWindow = () => { if (win && !win.isDestroyed()) { win.show(); win.focus(); if (win.isMinimized()) win.restore(); } };
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: '显示 EggBloom', click: showWindow },
      { type: 'separator' },
      { label: '退出', click: () => { isQuitting = true; fullQuit(); } },
    ]));
    tray.on('click', showWindow);        // 单击恢复
    tray.on('double-click', showWindow); // 双击恢复
    logLine('Tray created OK');
  } catch (e) {
    logLine('Tray error: ' + e.message);
  }
}

function showCloseDialog() {
  if (dialogWin && !dialogWin.isDestroyed()) return;
  dialogWin = new BrowserWindow({
    width: 400, height: 280, resizable: false, frame: false, transparent: true,
    alwaysOnTop: true, skipTaskbar: true, parent: win,
    webPreferences: { contextIsolation: false, nodeIntegration: true },
  });

  const iconPath = path.join(__dirname, 'icon-large.png');
  const iconB64 = fs.existsSync(iconPath) ? fs.readFileSync(iconPath).toString('base64') : '';
  const iconHtml = iconB64 ? `<img src="data:image/png;base64,${iconB64}" style="width:44px;height:44px;border-radius:10px;">` : '';

  const html = `<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:transparent; font-family:'Segoe UI',system-ui,-apple-system,sans-serif; overflow:hidden; }
  .card {
    background:linear-gradient(145deg,#0d1117 0%,#161b22 50%,#0d1117 100%);
    border:1px solid #30363d; border-radius:18px;
    padding:30px 28px 24px; color:#e6edf3;
    box-shadow:0 12px 40px rgba(0,0,0,0.7), inset 0 1px 0 rgba(255,255,255,0.05);
  }
  .header { display:flex; align-items:center; gap:14px; margin-bottom:18px; }
  .title { font-size:16px; font-weight:600; letter-spacing:0.3px; }
  .desc { font-size:13px; color:#8b949e; line-height:1.6; margin-bottom:24px; }
  .btn-group { display:flex; gap:10px; }
  .btn {
    flex:1; padding:11px 0; border:none; border-radius:12px;
    font-size:13px; font-weight:600; cursor:pointer; transition:all .2s;
    letter-spacing:0.2px;
  }
  .btn:active { transform:scale(0.97); }
  .btn-tray {
    background:linear-gradient(135deg,#238636,#2ea043); color:#fff;
    box-shadow:0 2px 8px rgba(46,160,67,0.3);
  }
  .btn-tray:hover { background:linear-gradient(135deg,#2ea043,#3fb950); box-shadow:0 4px 12px rgba(46,160,67,0.5); }
  .btn-quit {
    background:linear-gradient(135deg,#b62324,#da3633); color:#fff;
    box-shadow:0 2px 8px rgba(218,54,51,0.3);
  }
  .btn-quit:hover { background:linear-gradient(135deg,#da3633,#f85149); box-shadow:0 4px 12px rgba(248,81,73,0.5); }
  .btn-cancel {
    background:rgba(110,118,129,0.15); color:#8b949e; flex:0.5;
    border:1px solid #30363d;
  }
  .btn-cancel:hover { background:rgba(110,118,129,0.25); color:#e6edf3; }
</style></head><body>
<div class="card">
  <div class="header">
    ${iconHtml}
    <div class="title">EggBloom Desktop</div>
  </div>
  <div class="desc">关闭窗口后程序将如何处理？<br>最小化到托盘可保持协议层和 Bot 在后台运行</div>
  <div class="btn-group">
    <button class="btn btn-tray" onclick="require('electron').ipcRenderer.invoke('dialog-close-action','tray')">最小化到托盘</button>
    <button class="btn btn-quit" onclick="require('electron').ipcRenderer.invoke('dialog-close-action','quit')">退出程序</button>
    <button class="btn btn-cancel" onclick="require('electron').ipcRenderer.invoke('dialog-close-action','cancel')">取消</button>
  </div>
</div>
</body></html>`;

  dialogWin.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html));
  dialogWin.once('ready-to-show', () => { dialogWin.show(); dialogWin.focus(); });
}

function fullQuit() {
  // 通过 API 停止协议层
  try {
    const req = http.request({ hostname: '127.0.0.1', port: 8080, path: '/api/protocol/stop', method: 'POST', timeout: 3000 }, () => {});
    req.on('error', () => {});
    req.end();
  } catch (e) {}
  setTimeout(() => {
    // 只清理本应用启动的 webui_server 进程树（bot_core 是其子进程）。
    // 不无差别杀全机 QQ.exe：用户可能同时登录多个 QQ 账号，退出 EggBloom 不应强杀它们；
    // 协议层自身驱动的 QQ 由 /api/protocol/stop 负责收尾（见上方请求）。
    try { if (server && server.pid) execSync('taskkill /F /PID ' + server.pid + ' /T 2>nul', { timeout: 3000, stdio: 'ignore' }); } catch (e) {}
    if (tray) { try { tray.destroy(); } catch (e) {} }
    app.quit();
  }, 1500);
}

function createWindow() {
  win = new BrowserWindow({
    width: 1280, height: 860, minWidth: 960, minHeight: 640,
    title: 'EggBloom Desktop', backgroundColor: '#0a0a0f',
    frame: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: false,
      nodeIntegration: true,
    },
  });
  win.setMenu(null);
  win.loadURL('http://localhost:8080?v=' + Date.now());
  // 连不上服务时轮询重试（后端守护重启期间），而不是白屏等待用户手动刷新
  win.webContents.on('did-fail-load', () => {
    if (win && !win.isDestroyed() && !isQuitting) {
      setTimeout(() => { if (win && !win.isDestroyed()) win.loadURL('http://localhost:8080?v=' + Date.now()); }, 2000);
    }
  });

  // 最大化状态变化 → 通知前端切换标题栏按钮图标
  const sendMaxState = () => {
    if (win && !win.isDestroyed()) win.webContents.send('window-maximized-state', win.isMaximized());
  };
  win.on('maximize', sendMaxState);
  win.on('unmaximize', sendMaxState);

  win.on('close', (e) => {
    if (isQuitting) return;
    e.preventDefault();
    showCloseDialog();
  });
  win.on('closed', () => { win = null; });
}

function showErrorPage(reason) {
  win = new BrowserWindow({
    width: 720, height: 420, title: '启动失败', backgroundColor: '#0a0a0f', resizable: false,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: false, nodeIntegration: true },
  });
  win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(`<h1>${reason}</h1>`));
}

app.whenReady().then(async () => {
  ipcMain.handle('eggbloom-api', async (event, { path: apiPath, options }) => {
    return new Promise((resolve, reject) => {
      const req = http.request({
        hostname: '127.0.0.1', port: 8080, path: apiPath,
        method: (options && options.method) || 'GET',
        headers: Object.assign({ 'Content-Type': 'application/json' }, (options && options.headers) || {}),
      }, (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          try { resolve(JSON.parse(data)); } catch { resolve(data); }
        });
      });
      req.on('error', (err) => reject(new Error('IPC failed: ' + err.message)));
      if (options && options.body) req.write(options.body);
      req.end();
    });
  });

  ipcMain.handle('dialog-close-action', (event, action) => {
    if (dialogWin && !dialogWin.isDestroyed()) dialogWin.close();
    dialogWin = null;
    if (action === 'tray') { if (win && !win.isDestroyed()) { if (win.isMinimized()) win.restore(); win.hide(); } }
    else if (action === 'quit') { isQuitting = true; fullQuit(); }
  });

  ipcMain.handle('dialog-open-file', async (event, opts) => {
    const { dialog, BrowserWindow } = require('electron');
    const win = BrowserWindow.getFocusedWindow() || BrowserWindow.getAllWindows()[0];
    const res = await dialog.showOpenDialog(win, Object.assign({
      title: '选择 QQ 可执行文件',
      properties: ['openFile'],
      filters: [{ name: 'QQ', extensions: ['exe'] }],
    }, opts || {}));
    return res.filePaths && res.filePaths.length ? res.filePaths[0] : '';
  });

  const candidates = [
    path.join(__dirname, 'webui', 'webui_server.py'),
    path.join(__dirname, '..', 'app-src', 'webui', 'webui_server.py'),
    path.join(process.resourcesPath || '', '..', 'app-src', 'webui', 'webui_server.py'),
  ];
  let serverPath = candidates[0];
  for (const c of candidates) {
    try { if (!c.includes('.asar') && fs.existsSync(c)) { serverPath = c; break; } } catch (e) {}
  }

  const bundledPython = path.join(__dirname, 'python', 'python.exe');           // resources/app/python 布局
  const bundledPythonRoot = path.join(__dirname, '..', '..', 'python', 'python.exe'); // Full/Standard 包：python 在包根目录（resources 上一级）
  const bundledPythonMid = path.join(__dirname, '..', 'python', 'python.exe');   // resources/python 布局（保底）
  const pythonCandidates = [
    bundledPythonRoot,   // 优先 bundled（包根目录，Full/Standard/dist 布局），避免依赖系统 Python
    bundledPython,
    bundledPythonMid,
    'pythonw.exe',
    'python.exe',
    'python',
    'python3',
  ];
  let chosenPython = null;
  for (const candidate of pythonCandidates) {
    // 文件路径先验存在性，减少 spawn 失败与等待
    if (path.isAbsolute(candidate) && !fs.existsSync(candidate)) { logLine('skip python candidate (not exists): ' + candidate); continue; }
    try {
      await new Promise((resolve, reject) => {
        const child = spawn(candidate, ['--version'], { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
        const timer = setTimeout(() => { try { child.kill(); } catch (e) {} reject(); }, 2000);
        child.on('close', (code) => { clearTimeout(timer); code === 0 ? resolve() : reject(); });
        child.on('error', () => { clearTimeout(timer); reject(); });
      });
      chosenPython = candidate; break;
    } catch (e) {}
  }

  if (!chosenPython) { showErrorPage('未找到 Python'); return; }

  // ── webui_server 守护：崩溃/被杀后自动重启，避免协议层/Bot 连锁停摆 ──
  let serverRestartDelay = 3000;  // 首次重启等待 3s，连续崩溃递增
  let serverRestartTimer = null;

  function startServer() {
    killOldProcesses();
    const appDir = path.join(__dirname, '..');
    server = spawn(chosenPython, [serverPath], { cwd: appDir, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true, detached: true });
    server.stderr.on('data', (b) => logLine('STDERR: ' + b.toString()));
    server.on('exit', (code, signal) => {
      logLine(`webui_server 退出 code=${code} signal=${signal} isQuitting=${isQuitting}`);
      if (isQuitting) return;
      server = null;
      // 退避：连续快速崩溃则拉长等待，避免死循环
      const delay = serverRestartDelay;
      serverRestartDelay = Math.min(serverRestartDelay * 2, 30000);
      logLine(`将在 ${delay}ms 后自动重启 webui_server`);
      serverRestartTimer = setTimeout(startServer, delay);
    });
    server.on('spawn', () => {
      // 启动成功则重置退避
      setTimeout(() => { serverRestartDelay = 3000; }, 15000);
    });
    return server;
  }

  startServer();

  // 先给窗口 8s 等服务就绪；超时也直接开窗（页面会自动轮询重试），避免慢启动时用户长时间无任何反馈
  try { await waitForServer('http://localhost:8080', 8000); logLine('server ready (first wait)'); }
  catch (e) { logLine('first wait timed out, opening window anyway: ' + e.message); }

  createTray();
  createWindow();
  app.on('activate', () => { if (win && !win.isDestroyed()) { win.show(); } else { createWindow(); } });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin' && isQuitting) fullQuit();
});
app.on('browser-window-created', (_, w) => { w.webContents.setWindowOpenHandler(() => ({ action: 'deny' })); });
ipcMain.handle('window-minimize', () => { const w = BrowserWindow.getFocusedWindow() || win; if (w && !w.isDestroyed()) w.minimize(); });
ipcMain.handle('window-maximize', () => { const w = BrowserWindow.getFocusedWindow() || win; if (w && !w.isDestroyed()) { if (w.isMaximized()) w.unmaximize(); else w.maximize(); } });
ipcMain.handle('window-close', () => { const w = BrowserWindow.getFocusedWindow() || win; if (w && !w.isDestroyed()) w.close(); });
