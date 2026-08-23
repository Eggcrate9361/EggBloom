const { ipcRenderer } = require('electron');

window.__EGGBLOOM_IPC__ = async (p, opts) => {  if (p === 'dialog-open-file') {    return ipcRenderer.invoke('dialog-open-file', opts);  }  return ipcRenderer.invoke('eggbloom-api', { path: p, options: opts || {} });};
window.__EGGBLOOM_EXECUTE_JS__ = (code) => ipcRenderer.invoke('eggbloom-execute-js', code);

// ── 窗口控制（自绘标题栏用） ──
window.__EGGBLOOM_WINDOW__ = {
  minimize: () => ipcRenderer.invoke('window-minimize'),
  maximize: () => ipcRenderer.invoke('window-maximize'),
  close: () => ipcRenderer.invoke('window-close'),
  onMaximizedState: (cb) => {
    const handler = (_, isMax) => { if (typeof cb === 'function') cb(isMax); };
    ipcRenderer.on('window-maximized-state', handler);
    return () => ipcRenderer.removeListener('window-maximized-state', handler);
  },
};

window.__EGGBLOOM_EGO__ = (() => {
  function request(method, args) {
    return new Promise((resolve, reject) => {
      const id = String(Date.now()) + Math.random();
      const timer = setTimeout(() => reject(new Error('egoBridge timeout: ' + method)), 5000);
      const handler = (_, replyId, payload) => {
        if (replyId !== id) return;
        ipcRenderer.removeListener('eggbloom-ego-reply', handler);
        clearTimeout(timer);
        if (payload && payload.error) reject(new Error(payload.error));
        else resolve(payload && payload.result);
      };
      ipcRenderer.once('eggbloom-ego-reply', handler);
      try { ipcRenderer.send('egoBridge', { id, method, args }); }
      catch (err) { ipcRenderer.removeListener('eggbloom-ego-reply', handler); clearTimeout(timer); reject(err); }
    });
  }
  return {
    getElement: (id) => request('getElement', [id]),
    querySelector: (sel) => request('querySelector', [sel]),
    querySelectorAll: (sel) => request('querySelectorAll', [sel]),
    execJs: (code) => request('execJs', [code]),
    api: async (path, opts) => {
      const res = await fetch('/api' + path, opts);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    },
  };
})();
