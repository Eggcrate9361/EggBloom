# -*- coding: utf-8 -*-
"""生成「七大原语」样张（供评审）：直接从真实前端抽出 :root 与 #design-primitives，
再套上贴近 EggBloom 真实数据的示例标记 → 渲染 PNG。

用法（**必须系统 Python**，Playwright 1.62 + chromium-1228）：
    python scripts/make-primitives-preview.py
产物：OUT 目录下的 primitives_preview.html / .png（脚本顶部 OUT 常量可改）

为什么要抽真实 CSS 而不是手写一份：
手写的样张会与实现漂移；抽出来的样张**就是**线上将要生效的那份样式。

注意：全程不用 f-string —— 样例里有 JS 模板字面量 `${...}`，f-string 会把它
当成 Python 表达式（这里踩过一次：NameError: name 'Math' is not defined）。
"""
from pathlib import Path
import re
import io

from playwright.sync_api import sync_playwright

SRC = Path(r"D:\git\EggBloom\webui\webui_artistic_v3.html")
OUT = Path(r"D:\workbuddy_tmp")

s = io.open(SRC, encoding="utf-8").read()

m_root = re.search(r":root\{.*?\}", s, re.S)
m_prim = re.search(r'<style id="design-primitives">(.*?)</style>', s, re.S)
m_body = re.search(r"body\{.*?\}", s, re.S)
assert m_root and m_prim, "抽不到 :root 或 原语层"

CSS = "\n".join([
    m_root.group(0),
    m_prim.group(1),
    "html,body{margin:0;padding:0;background:var(--bg);color:var(--ink);"
    "font-family:var(--font-d);height:auto;overflow:auto;user-select:none}",
    ".wrap{padding:var(--s7) var(--s8);max-width:1280px;margin:0 auto;"
    "display:flex;flex-direction:column;gap:var(--s8)}",
    ".grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:var(--s6)}",
    ".grid2{display:grid;grid-template-columns:1fr 1fr;gap:var(--s6)}",
    ".note{font-family:var(--font-m);font-size:var(--z2);color:var(--ink4);letter-spacing:.04em}",
    ".c4{grid-template-columns:1fr 120px 90px 110px}",
])

BODY = """
<div class="wrap">

<section>
  <div class="sec-h"><span class="lab">运行指标</span><i class="rule"></i><span class="meta">刚刚刷新</span></div>
  <div class="grid4">
    <div class="p-stat"><span class="lab">今日群消息</span><span class="val">1,284</span><span class="ctx">较昨日 +12%</span></div>
    <div class="p-stat"><span class="lab">活跃群</span><span class="val">7</span><span class="ctx">白名单 12 个</span></div>
    <div class="p-stat"><span class="lab">本体记忆</span><span class="val">4,080</span><span class="ctx">条</span></div>
    <div class="p-stat"><span class="lab">缓存命中</span><span class="val">86.4%</span><span class="ctx">前缀缓存</span></div>
  </div>
</section>

<section>
  <div class="sec-h"><span class="lab">进程</span><i class="rule"></i><span class="meta">kv 原语</span></div>
  <div class="grid2">
    <dl class="p-kv">
      <dt>后端端口</dt><dd>3005</dd>
      <dt>前端端口</dt><dd>8080</dd>
      <dt>模型</dt><dd>deepseek-v4-flash</dd>
    </dl>
    <dl class="p-kv">
      <dt>协议驱动</dt><dd>SnowLuma v1.14.10</dd>
      <dt>人格档位</dt><dd>平静 · accent #c9a96e</dd>
      <dt>群白名单</dt><dd>已启用 · 12 个群</dd>
    </dl>
  </div>
</section>

<section>
  <div class="sec-h"><span class="lab">操作</span><i class="rule"></i><span class="meta">cmd 原语 · 等宽大写方角</span></div>
  <div style="display:flex;gap:var(--s3);align-items:center;flex-wrap:wrap">
    <button class="p-cmd primary">保存配置 <kbd>CTRL+S</kbd></button>
    <button class="p-cmd">重新加载插件</button>
    <button class="p-cmd">导出日志</button>
    <button class="p-cmd" disabled>清空记忆</button>
  </div>
</section>

<section>
  <div class="sec-h"><span class="lab">配置</span><i class="rule"></i><span class="meta">fld 原语 · 底线输入框</span></div>
  <div class="grid2">
    <div class="p-fld"><label>群白名单</label><input value="123456, 234567, 345678"><span class="hint">留空 = 不限制（所有群都参与）</span></div>
    <div class="p-fld"><label>模型名</label><input value="deepseek-v4-flash"><span class="hint">走 sensenova 中转</span></div>
  </div>
</section>

<section>
  <div class="sec-h"><span class="lab">群列表</span><i class="rule"></i><span class="meta">row 原语 · 1px 下线</span></div>
  <div class="p-row head c4"><span>群名</span><span>成员</span><span>状态</span><span>最近说话</span></div>
  <div class="p-row c4">
    <span>核院 2026 级</span><span class="num">128</span>
    <span class="p-sig ok"><i></i>参与中</span><span class="dim num">2 分钟前</span>
  </div>
  <div class="p-row c4">
    <span>动漫社摸鱼群</span><span class="num">64</span>
    <span class="p-sig live"><i></i>正在回复</span><span class="dim num">刚刚</span>
  </div>
  <div class="p-row c4">
    <span>某个陌生群</span><span class="num">301</span>
    <span class="p-sig err"><i></i>白名单外</span><span class="dim num">—</span>
  </div>
</section>

<section>
  <div class="sec-h"><span class="lab">状态</span><i class="rule"></i><span class="meta">sig 原语 · 无胶囊无底色</span></div>
  <div style="display:flex;gap:var(--s5);flex-wrap:wrap;align-items:center">
    <span class="p-sig ok"><i></i>后端在线</span>
    <span class="p-sig live"><i></i>正在生成</span>
    <span class="p-sig warn"><i></i>重试中 2/3</span>
    <span class="p-sig err"><i></i>协议断开</span>
    <span class="p-sig"><i></i>未启用</span>
  </div>
</section>

<section>
  <div class="sec-h"><span class="lab">会话日志</span><i class="rule"></i><span class="meta">log 原语 · 四列</span></div>
  <div class="p-log">
    <div class="ln"><span class="no">1</span><span class="ts">14:02:11</span><span class="lv ok">INFO</span><span class="tx">群[123456] 合并消息: 今天天气不错啊...</span></div>
    <div class="ln"><span class="no">2</span><span class="ts">14:02:11</span><span class="lv">INFO</span><span class="tx">工具调用: ['ws_grep']</span></div>
    <div class="ln"><span class="no">3</span><span class="ts">14:02:12</span><span class="lv warn">WARN</span><span class="tx">[状态] 写盘失败（连续第 1 次，已跳过本轮）: 文件被占用</span></div>
    <div class="ln"><span class="no">4</span><span class="ts">14:02:13</span><span class="lv err">ERROR</span><span class="tx">群[123456] 引用消息 get_msg 失败(retcode=1200)</span></div>
    <div class="ln"><span class="no">5</span><span class="ts">14:02:14</span><span class="lv ok">INFO</span><span class="tx">群[123456] 回复已发送（温度 1.24 · 情绪 活跃）</span></div>
  </div>
</section>

<section>
  <div class="sec-h"><span class="lab">数据即图形</span><i class="rule"></i><span class="meta">只用 --accent，靠透明度分层</span></div>
  <div class="grid2">
    <div>
      <div class="note" style="margin-bottom:var(--s2)">24 小时消息量（spark）</div>
      <div class="spark" id="spark"></div>
    </div>
    <div>
      <div class="note" style="margin-bottom:var(--s2)">Token 预算余量（meter）</div>
      <div class="meter" id="meter"></div>
    </div>
  </div>
</section>

</div>

<script>
var H = [8,4,2,1,1,2,5,18,46,38,30,42,55,48,36,40,52,61,72,66,50,34,22,12];
var mx = Math.max.apply(null, H);
document.getElementById('spark').innerHTML = H.map(function (v) {
  var h = Math.max(4, Math.round(v / mx * 100));
  var o = (0.18 + v / mx * 0.72).toFixed(2);
  return '<i style="height:' + h + '%;opacity:' + o + '"></i>';
}).join('');
var M = [1, .82, .64, .46, .28, .10];
document.getElementById('meter').innerHTML = M.map(function (o) {
  var w = (35 + o * 63).toFixed(1);
  var op = (0.25 + o * 0.75).toFixed(2);
  return '<i style="width:' + w + '%;opacity:' + op + '"></i>';
}).join('');
</script>
"""

HTML = (
    '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
    "<style>\n" + CSS + "\n</style></head><body>" + BODY + "</body></html>"
)

html_path = OUT / "primitives_preview.html"
html_path.write_text(HTML, encoding="utf-8")

png_path = OUT / "primitives_preview.png"
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--force-color-profile=srgb", "--font-render-hinting=none"])
    pg = b.new_page(viewport={"width": 1400, "height": 1000}, device_scale_factor=2)
    pg.goto(html_path.resolve().as_uri(), wait_until="load")
    pg.wait_for_timeout(1500)
    pg.screenshot(path=str(png_path), full_page=True)
    b.close()

print("OK ", html_path)
print("OK ", png_path)
