/* EggBloom 插件 UI 共用层：统一皮肤 + 自建下拉框 + toast/confirm
   由 /page 统一注入；插件页不需要改代码。 */
(function () {
  if (window.__EBUI_KIT__) return;
  window.__EBUI_KIT__ = true;

  var doc = document;
  doc.documentElement.setAttribute('data-eb-ui', '');

  function ready(fn) {
    if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', fn);
    else fn();
  }

  function closeAll(except) {
    var panels = doc.querySelectorAll('.eb-select-panel');
    for (var i = 0; i < panels.length; i++) {
      if (panels[i] === except) continue;
      panels[i].style.display = 'none';
      var b = panels[i].parentNode && panels[i].parentNode.querySelector('.eb-select-btn');
      if (b) b.setAttribute('aria-expanded', 'false');
    }
  }

  function enhanceSelect(sel) {
    if (!sel || sel.dataset.ebSelect === '1') return;
    if (sel.multiple || Number(sel.size) > 1) return;
    if (sel.closest && sel.closest('.eb-select')) return;

    sel.dataset.ebSelect = '1';

    var wrap = doc.createElement('div');
    wrap.className = 'eb-select';

    var btn = doc.createElement('button');
    btn.type = 'button';
    btn.className = 'eb-select-btn';
    btn.setAttribute('aria-haspopup', 'listbox');
    btn.setAttribute('aria-expanded', 'false');

    var label = doc.createElement('span');
    label.className = 'eb-select-label';
    var arrow = doc.createElement('span');
    arrow.className = 'eb-select-arrow';
    btn.appendChild(label);
    btn.appendChild(arrow);

    var panel = doc.createElement('div');
    panel.className = 'eb-select-panel';
    panel.setAttribute('role', 'listbox');
    panel.style.display = 'none';

    // 占位宽度：优先跟随原 select 的布局
    try {
      var parent = sel.parentElement;
      var inField = parent && parent.classList && parent.classList.contains('field');
      var w = sel.getBoundingClientRect().width;
      if (inField) wrap.style.width = '100%';
      else if (sel.style.width) wrap.style.width = sel.style.width;
      else if (w && w > 80) wrap.style.width = Math.round(w) + 'px';
    } catch (e) { /* ignore */ }

    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(sel);
    wrap.appendChild(btn);
    wrap.appendChild(panel);
    sel.classList.add('eb-native-hidden');

    var active = -1;
    var searchEl = null;

    function opts() {
      var out = [];
      for (var i = 0; i < sel.options.length; i++) {
        var o = sel.options[i];
        out.push({ i: i, text: (o.textContent || '').trim(), disabled: !!o.disabled, value: o.value });
      }
      return out;
    }

    function syncLabel() {
      var o = sel.options[sel.selectedIndex];
      var t = o ? (o.textContent || '').trim() : '';
      label.textContent = t || '请选择';
      btn.classList.toggle('eb-placeholder', !t || (o && o.value === ''));
      btn.disabled = !!sel.disabled;
      if (sel.disabled) close();
    }

    function render(filter) {
      panel.innerHTML = '';
      var all = opts();
      var f = (filter || '').toLowerCase();
      var shown = [];
      for (var i = 0; i < all.length; i++) {
        if (f && all[i].text.toLowerCase().indexOf(f) === -1) continue;
        shown.push(all[i]);
      }
      if (!shown.length) {
        var empty = doc.createElement('div');
        empty.className = 'eb-select-empty';
        empty.textContent = '没有匹配项';
        panel.appendChild(empty);
        active = -1;
        return;
      }
      for (var j = 0; j < shown.length; j++) {
        (function (item, pos) {
          var div = doc.createElement('div');
          div.className = 'eb-select-option';
          div.setAttribute('role', 'option');
          div.textContent = item.text;
          if (item.disabled) {
            div.style.opacity = '.35';
            div.style.cursor = 'not-allowed';
          }
          if (item.i === sel.selectedIndex) div.classList.add('eb-selected');
          if (pos === active) div.classList.add('eb-active');
          div.addEventListener('mousedown', function (ev) {
            ev.preventDefault();
            if (item.disabled) return;
            sel.selectedIndex = item.i;
            sel.dispatchEvent(new Event('change', { bubbles: true }));
            sel.dispatchEvent(new Event('input', { bubbles: true }));
            syncLabel();
            closePanel();
          });
          panel.appendChild(div);
        })(shown[j], j);
      }
    }

    function place() {
      panel.classList.remove('eb-up');
      try {
        var r = btn.getBoundingClientRect();
        if (window.innerHeight - r.bottom < 230 && r.top > 250) panel.classList.add('eb-up');
      } catch (e) { /* ignore */ }
    }

    function openPanel() {
      if (sel.disabled) return;
      closeAll(panel);
      syncLabel();
      active = sel.selectedIndex;
      var all = opts();
      if (all.length > 8) {
        searchEl = doc.createElement('input');
        searchEl.className = 'eb-select-search';
        searchEl.placeholder = '搜索…';
        searchEl.setAttribute('autocomplete', 'off');
        searchEl.addEventListener('input', function () {
          active = -1;
          render(searchEl.value);
        });
        searchEl.addEventListener('keydown', onKey);
        panel.appendChild(searchEl);
      } else {
        searchEl = null;
      }
      render('');
      panel.style.display = 'block';
      btn.setAttribute('aria-expanded', 'true');
      place();
      if (searchEl) setTimeout(function () { searchEl.focus(); }, 0);
    }

    function closePanel() {
      panel.style.display = 'none';
      btn.setAttribute('aria-expanded', 'false');
    }

    function move(delta) {
      var all = opts();
      if (!all.length) return;
      var cur = active;
      for (var n = 0; n < all.length; n++) {
        cur = (cur + delta + all.length) % all.length;
        if (!all[cur].disabled) { active = cur; break; }
      }
      var nodes = panel.querySelectorAll('.eb-select-option');
      for (var i = 0; i < nodes.length; i++) nodes[i].classList.remove('eb-active');
      // active 是 options 索引；重渲染按当前过滤后的位置标 active
      var f = searchEl ? searchEl.value : '';
      render(f);
      var shown = panel.querySelectorAll('.eb-select-option');
      for (var j = 0; j < shown.length; j++) if (shown[j].textContent === all[active].text) shown[j].classList.add('eb-active');
    }

    function onKey(ev) {
      var k = ev.key;
      if (k === 'ArrowDown') { ev.preventDefault(); move(1); }
      else if (k === 'ArrowUp') { ev.preventDefault(); move(-1); }
      else if (k === 'Enter') {
        ev.preventDefault();
        if (active >= 0) {
          sel.selectedIndex = active;
          sel.dispatchEvent(new Event('change', { bubbles: true }));
          sel.dispatchEvent(new Event('input', { bubbles: true }));
          syncLabel();
          closePanel();
        }
      } else if (k === 'Escape') { ev.preventDefault(); closePanel(); btn.focus(); }
    }

    btn.addEventListener('click', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      if (panel.style.display === 'block') closePanel();
      else openPanel();
    });
    btn.addEventListener('keydown', function (ev) {
      if (ev.key === 'ArrowDown' || ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        openPanel();
      } else if (ev.key === 'ArrowUp') {
        ev.preventDefault();
        openPanel();
        active = opts().length - 1;
        move(0);
      }
    });
    panel.addEventListener('keydown', onKey);
    sel.addEventListener('change', syncLabel);
    sel.addEventListener('focus', syncLabel);
    try {
      new MutationObserver(function () { syncLabel(); })
        .observe(sel, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled'] });
    } catch (e) { /* ignore */ }

    syncLabel();
  }

  function enhanceAll(root) {
    var scope = root || doc;
    var list = scope.querySelectorAll('select');
    for (var i = 0; i < list.length; i++) enhanceSelect(list[i]);
  }

  function toast(msg, type) {
    var host = doc.querySelector('.eb-toast-host');
    if (!host) {
      host = doc.createElement('div');
      host.className = 'eb-toast-host';
      (doc.body || doc.documentElement).appendChild(host);
    }
    var el = doc.createElement('div');
    el.className = 'eb-toast' + (type ? ' eb-' + type : '');
    el.textContent = String(msg == null ? '' : msg);
    host.appendChild(el);
    requestAnimationFrame(function () { el.classList.add('eb-show'); });
    setTimeout(function () {
      el.classList.remove('eb-show');
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 220);
    }, 2600);
  }

  function confirmDialog(message, opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      var backdrop = doc.createElement('div');
      backdrop.className = 'eb-modal-backdrop';
      var box = doc.createElement('div');
      box.className = 'eb-modal';
      box.setAttribute('role', 'dialog');
      box.setAttribute('aria-modal', 'true');
      var h = doc.createElement('h3');
      h.textContent = opts.title || '确认';
      var p = doc.createElement('p');
      p.textContent = String(message || '');
      var actions = doc.createElement('div');
      actions.className = 'eb-modal-actions';
      var cancel = doc.createElement('button');
      cancel.type = 'button';
      cancel.textContent = opts.cancelText || '取消';
      var ok = doc.createElement('button');
      ok.type = 'button';
      ok.className = 'eb-primary';
      ok.textContent = opts.okText || '确定';
      actions.appendChild(cancel);
      actions.appendChild(ok);
      box.appendChild(h);
      box.appendChild(p);
      box.appendChild(actions);
      backdrop.appendChild(box);
      doc.body.appendChild(backdrop);

      function done(v) {
        if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
        doc.removeEventListener('keydown', onDocKey);
        resolve(v);
      }
      function onDocKey(ev) {
        if (ev.key === 'Escape') done(false);
        else if (ev.key === 'Enter') done(true);
      }
      cancel.addEventListener('click', function () { done(false); });
      ok.addEventListener('click', function () { done(true); });
      backdrop.addEventListener('mousedown', function (ev) { if (ev.target === backdrop) done(false); });
      doc.addEventListener('keydown', onDocKey);
      setTimeout(function () { ok.focus(); }, 0);
    });
  }

  window.EBUI = {
    toast: toast,
    confirm: confirmDialog,
    refresh: function () { enhanceAll(doc); },
    enhanceSelect: enhanceSelect
  };

  ready(function () {
    enhanceAll(doc);
    try {
      var mo = new MutationObserver(function (muts) {
        for (var i = 0; i < muts.length; i++) {
          var added = muts[i].addedNodes || [];
          for (var j = 0; j < added.length; j++) {
            var n = added[j];
            if (n.nodeType !== 1) continue;
            if (n.tagName === 'SELECT') enhanceSelect(n);
            else if (n.querySelectorAll) enhanceAll(n);
          }
        }
      });
      mo.observe(doc.body, { childList: true, subtree: true });
    } catch (e) { /* ignore */ }
  });
})();
