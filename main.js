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

/* ── 端口自适应（清单 3.3）──
   webui_server 在 8080 被占时会顺延并写 last_port.txt（PROJECT 根），
   但 main.js 曾把 8080 写死在 5 处 → 一旦顺延，窗口按 did-fail-load
   每 2 秒重试 8080，**永远白屏**而後端其實正常。
   现在统一走 readPort()：读不到文件回落 8080；IPC 与退出路径每次现读
   （缓存 2s，避免每次 API 调用都碰盘）；did-fail-load 重试前重读，
   后端顺延后窗口能在下一次重试时跟上。 */
const PORT_FILE = path.join(__dirname, 'last_port.txt');
function readPort() {
  try {
    const v = parseInt(fs.readFileSync(PORT_FILE, 'utf8').trim(), 10);
    if (Number.isInteger(v) && v >= 1024 && v <= 65535) return v;
  } catch (e) {}
  return 8080;
}
let PORT = readPort();
let _portCache = { v: PORT, t: 0 };
function currentPort() {
  const now = Date.now();
  if (now - _portCache.t > 2000) { _portCache = { v: readPort(), t: now }; }
  return _portCache.v;
}

function waitForServer(urlOrFn, timeout = 10000) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const interval = setInterval(() => {
      /* 每次探测都重新取端口：后端可能刚写完 last_port.txt */
      const url = typeof urlOrFn === 'function' ? urlOrFn() : urlOrFn;
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

function createWindow() {
  win = new BrowserWindow({
    width: 1280, height: 860, minWidth: 960, minHeight: 640,
    title: 'EggBloom Desktop', backgroundColor: '#0a0a0f',
    icon: path.join(__dirname, 'icon.png'),
    frame: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: false,
      nodeIntegration: true,
    },
  });
  win.setMenu(null);
  win.loadURL('http://localhost:' + PORT + '?v=' + Date.now());
  // 连不上服务时轮询重试（后端守护重启期间），而不是白屏等待用户手动刷新。
  // 重试前重读 last_port.txt：后端顺延端口时窗口能在下一次重试跟上（清单 3.3）
  win.webContents.on('did-fail-load', () => {
    if (win && !win.isDestroyed() && !isQuitting) {
      setTimeout(() => {
        if (!win || win.isDestroyed()) return;
        const p = readPort();
        if (p !== PORT) { PORT = p; logLine('port shifted -> ' + p); }
        win.loadURL('http://localhost:' + PORT + '?v=' + Date.now());
      }, 2000);
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
    /* ★ 关闭确认做进**主窗页面内**（独立透明小窗在浅色调下没实体感、
       字体回落、还会全屏挡住其他应用——三宗罪，用户逐一踩过）。
       页面里的模态用界面自己的字体/配色/交互，天然统一。 */
    try { win.webContents.send('close-ask'); } catch (err) {}
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
        hostname: '127.0.0.1', port: currentPort(), path: apiPath,
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
    /* stdout 必须是 'ignore'，不能是 'pipe'。
       原因：后端的 log() 每行都 print()，而这里从来没人读 server.stdout。
       Node 不读 → 管道缓冲区（Windows 约 64KB）写满 → print() 阻塞 →
       后端主线程卡死（print 在写 qq_bot.log 之前，日志也会一起停）。
       实测：pythonw + 未消费的 stdout 管道，写 20000 行即挂住。
       后端日志落 qq_bot.log，界面看 WebUI 日志页，不靠这个管道。 */
    server = spawn(chosenPython, [serverPath], { cwd: appDir, stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true, detached: true });
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
  // 端口每次探测现读：后端可能刚顺延并写完 last_port.txt（清单 3.3）
  try { await waitForServer(() => 'http://localhost:' + currentPort(), 8000); logLine('server ready (first wait)'); PORT = currentPort(); }
  catch (e) { logLine('first wait timed out, opening window anyway: ' + e.message); }

  createTray();
  createWindow();
  app.on('activate', () => { if (win && !win.isDestroyed()) { win.show(); } else { createWindow(); } });
});

function fullQuit() {
  // 通过 API 停止协议层
  try {
    const req = http.request({ hostname: '127.0.0.1', port: readPort(), path: '/api/protocol/stop', method: 'POST', timeout: 3000 }, () => {});
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


app.on('window-all-closed', () => {
  if (process.platform !== 'darwin' && isQuitting) fullQuit();
});
app.on('browser-window-created', (_, w) => { w.webContents.setWindowOpenHandler(() => ({ action: 'deny' })); });
ipcMain.handle('window-minimize', () => { const w = BrowserWindow.getFocusedWindow() || win; if (w && !w.isDestroyed()) w.minimize(); });
ipcMain.handle('window-maximize', () => { const w = BrowserWindow.getFocusedWindow() || win; if (w && !w.isDestroyed()) { if (w.isMaximized()) w.unmaximize(); else w.maximize(); } });
ipcMain.handle('window-close', () => { const w = BrowserWindow.getFocusedWindow() || win; if (w && !w.isDestroyed()) w.close(); });
