> ⚠️ **可信度声明**：本文由子代理机械分析产出（2026-10-03），为前端重做的**迁移红线清单**。
> 红线 = JS 依赖的 id / class / data-* / 内联事件函数名 —— 重做视觉时这些名称与层级不得变。

---

# EggBloom WebUI 前端重做 · DOM 契约迁移红线清单

> 目标文件：`E:\EggBloom水星\EggBloom Desktop-new\resources\app\webui\webui_artistic_v3.html`
> 本文档由只读静态分析生成，是「保持功能 100% 不变」的前端重做的验收依据。
> 生成方式：Python 脚本对源码做花括号配对 + 偏移量归属 + 选择器字符串抽取，全部结论可复核。

## 0. 文件事实（先纠正三处与任务描述不符之处）

| 项 | 任务描述 | 实测 |
|---|---|---|
| 文件大小 | 400 KB | **399,825 字节**（379,480 字符）✅ 一致 |
| 总行数 | 4339 行 | **4371 行**（`split("\n")`），`Get-Content` 报 4370 行。**4339 未能确认**，任何依赖行号的迁移脚本必须以 4371 为准 |
| `<style>` | 约 67 KB | 开标签 **L1**，闭标签 **L506**，内容 **67,516 字符** ✅ 一致 |
| `<script>` | 约 302 KB | 开标签 **L576**，闭标签 **L4370**，内容 **302,583 字符** ✅ 一致 |
| 页面数 | 14 个 | **15 个** ⚠️ 任务清单漏了 `commands`（远程控制）。`pageMounts` 里有 15 项 |
| 内联 `style=` | 430 处 | **430 处** ✅ 一致（`<style>` 块内 0 处，`<script>` 内 427 处，shell HTML 3 处）|
| `class="card"` | 19 处 | **19 处** ✅ |
| `class="panel"` | 18 处 | **18 处** ✅ |
| `panel-title` | 24 处 | **41 处** ⚠️（其中 `class="panel-title"` 与 `panel-title` 变体合计 41 次出现）|

**极其重要的结构事实：本文件是半压缩的。** 第 829 / 835 / 837 / 839 / 841 行每行长达 3–5 万字符；
`mountProtocol` 在 L3361 单行内占 25,589 字符。**任何按行阅读或按行正则的做法都会串页**，
必须按「字符偏移量 → 花括号配对 → 归属函数」处理。本清单所有行号都附带列号 `L行:列`。

第 1 部分里 `id` 的定义/引用都给出 `L行:列`，可直接跳转定位。


## 阅读指引（先看这 6 条结论）

1. **文件是半压缩的**：L829 / L835 / L837 / L839 / L841 每行 3–5 万字符，`mountProtocol` 单行 25,589 字符。
   本文所有定位都给到 `L行:列`，不要用「按行」的方式改。
2. **页面是 15 个不是 14 个**：任务清单漏了 `commands`（远程控制）。`pageMounts` 有 15 项。
3. **有 9 个 id 被 JS 引用但 DOM 里根本不存在**（§4.1）——这是判定「功能 100% 不变」时最需要产品确认的清单。
4. **Chart.js 从未加载、`loadCharts()` 从未被调用**（§4.2）——图表能力本来就是死的。不要「顺手修好」。
5. **迁移顺序建议**（按函数体字符数=工作量降序）：
   `protocol`(25,589) → `groupmanage`(17,022) → `memes`(15,035) → `imagegen`(11,978) → `behavior`(6,708)
   → `tokens`(3,779) → `settings`(2,574) → `bot`(2,554) → `chatlogs`(2,401) → `dashboard`(1,785)
   → `monitor`(1,733) → `console`(1,459) → `memory`(1,070) → `commands`(551) → `plugins`(361)。
   注意 `protocol` 单页占全文业务代码的 ~1/6，建议独立排期、拆多轮做。
6. **三件「改样式就会静默坏功能」的事**：
   ① `tokens` 页 4 张卡靠 `querySelectorAll('.grid-4 .panel .meta')[0..3]` **下标**取值（§4.5）；
   ② `settings` / `groupmanage` 有 **50 处读 `.style.display` 当状态**（§4.6）；
   ③ 仪表盘日志打字机读 `row.lastChild`，后面**多加一个元素就坏**（§4.4）。

## 目录

- [第 1 部分：全局红线](#第-1-部分全局红线所有页面共用)
  - 1.1 总览计数 / 1.2 取值辅助函数 / 1.3 全部被 JS 引用的 id / 1.4 class 选择器
  - 1.5 data-* 属性 / 1.6 事件绑定方式 / 1.7 全局函数名红线
- [第 2 部分：逐页红线](#第-2-部分逐页红线重点)（15 个小节，含交互验收清单）
- [第 3 部分：外部依赖与全局状态](#第-3-部分外部依赖与全局状态)
  - 3.1 外部资源 / 3.2 定时器 / 3.3 页面切换机制 / 3.4 CSS 变量契约 / 3.5 全局状态变量
- [第 4 部分：迁移风险点](#第-4-部分迁移风险点每条都带行号)（4.1–4.10）
- [附录 A：分析方法与可复核性](#附录-a分析方法与可复核性)

---

# 第 1 部分：全局红线（所有页面共用）

## 1.1 总览计数

| 契约类型 | 数量 |
|---|---|
| `id=` 定义点（含动态拼接的模板、`el.id=` 赋值） | **402 处**，去重 **398 个** |
| 被 JS 引用的 `id`（去重） | **248 个**，引用点 **394 处** |
| 同时「有定义 + 被 JS 引用」= 真红线 | **239 个** |
| **被 JS 引用但全文件找不到定义** | **9 个**（见 §4.1） |
| 有定义但 JS 从不引用（仅 CSS 或纯静态） | **159 个** |
| JS 选择器字符串调用点 | **184 处** |
| JS 选择器里出现的 `class`（去重） | **43 个** |
| JS 选择器里出现的 `#id`（去重） | **53 个** |
| JS 选择器里出现的 `[data-*]`（去重） | **17 个** |
| `data-*` 属性名（全来源并集，去重） | **49 个** |
| 内联 HTML 事件属性 `on*=`（全文件共 11 处，均在 `<script>` 内） | **11 处**（onclick 9 / onerror 2） |
| `.on<evt> =` 属性式绑定 | **44 处**（click 37 / change 7） |
| `addEventListener(...)` | **139 处** |
| 事件委托（读 `e.target`） | **32 处** |
| `setInterval` / `setTimeout` / `requestAnimationFrame` 调用 | **52 处** |
| 外部资源引用 | Google Fonts 3 个（2 × preconnect + 1 × stylesheet）；**无 chart.min.js**（见 §4.2） |

## 1.2 取值辅助函数（这是「id 也是红线」的根因）

三个全局箭头函数把「字符串 → DOM」封装起来，因此任何 `$('x')` 都等价于 `getElementById('x')`：

| 定义 | 位置 | 作用 |
|---|---|---|
| `const $ = id => document.getElementById(id);` | **L659:1** | 参数就是 **裸 id**（不含 `#`）|
| `const qs = (s, r = document) => r.querySelector(s);` | **L660:1** | 参数是 **CSS 选择器** |
| `const qsa = (s, r = document) => Array.from(r.querySelectorAll(s));` | **L661:1** | 参数是 **CSS 选择器** |
| `const API = (p, opt = {}) => fetch('/api' + p, opt)...` | **L662:1** | 所有接口实际前缀 `/api` |

> 迁移红线：`$()` 与 `qs()/qsa()` 的参数语法**不同**（前者裸 id，后者含 `#`/`.`）。
> 重写时若把 `$()` 调用改成 `querySelector()` 而忘记补 `#`，功能会静默失效。

## 1.3 全部被 JS 引用的 id（红线，去重 248 个）

列含义：**定义**=HTML/模板里的定义位置（`—` 表示未找到定义，见 §4.1）；**引用**=JS 取值位置。

| id | 定义位置 | JS 引用位置（`种类@L行:列`，函数） |
|---|---|---|
| `sUseLocalLlm` | `L1033:49` | `$()@L1850:20`〔updateLlmModeUI〕· `$()@L1901:24`〔validateLLMForm〕· `$()@L2122:71`〔bindSettingsEvents〕· `$()@L2123:69`〔bindSettingsEvents〕· `$()@L2205:20`〔bindSettingsEvents〕· `$()@L2229:30`〔bindSettingsEvents〕 …+3 |
| `logStream` | `L837:2198` | `getElementById@L830:926`〔__logRender〕· `getElementById@L837:3881`〔getSelectedChatlogText〕· `getElementById@L837:4488`〔injectChatlogCopyButton〕· `$()@L807:8567`〔loadLogs〕· `$()@L807:9113`〔loadLogs〕· `$()@L837:2458`〔mountChatlogs〕 |
| `sImageLlamaPort` | `L1106:30` | `$()@L1917:21`〔validateLLMForm〕· `$()@L2237:32`〔bindSettingsEvents〕· `$()@L2445:26`〔bindSettingsEvents〕· `$()@L2478:26`〔bindSettingsEvents〕· `$()@L2506:31`〔bindSettingsEvents〕· `$()@L2528:31`〔bindSettingsEvents〕 |
| `sUseLocalImageLlm` | `L1085:51` | `$()@L1918:8`〔validateLLMForm〕· `$()@L2214:31`〔bindSettingsEvents〕· `$()@L2219:29`〔bindSettingsEvents〕· `$()@L2223:25`〔bindSettingsEvents〕· `$()@L2441:26`〔bindSettingsEvents〕· `$()@L2474:26`〔bindSettingsEvents〕 |
| `memeGrid` | `L3111:13` | `getElementById@L3144:27`〔_render〕· `getElementById@L3261:27`〔bindGridActions〕· `getElementById@L3330:107`〔mountMemes〕· `getElementById@L3331:113`〔mountMemes〕· `getElementById@L3332:27`〔mountMemes〕 |
| `oboOverlay` | `L506:377` | `$()@L3939:12`〔oboOpen〕· `$()@L3945:12`〔oboClose〕· `$()@L3973:12`〔oboRender〕· `$()@L4049:12`〔oboBind〕· `closest(#protoLog, #cmdOutput, #consoleOutput, .modal-overlay, #oboOverlay)@L592:14`〔fxTag〕 |
| `sApiKey` | `L1013:28` | `$()@L1621:27`〔loadSettingsData〕· `$()@L2172:24`〔bindSettingsEvents〕· `$()@L2178:20`〔bindSettingsEvents〕· `$()@L2545:24`〔bindSettingsEvents〕· `$()@L2578:18`〔updateApiKeyStatus〕 |
| `sLlamaExePath` | `L1049:30` | `$()@L2262:19`〔bindSettingsEvents〕· `$()@L2303:19`〔bindSettingsEvents〕· `$()@L2351:27`〔bindSettingsEvents〕· `$()@L2356:29`〔bindSettingsEvents〕· `$()@L2358:29`〔bindSettingsEvents〕 |
| `sLlamaPort` | `L1057:30` | `$()@L1912:18`〔validateLLMForm〕· `$()@L2264:26`〔bindSettingsEvents〕· `$()@L2305:26`〔bindSettingsEvents〕· `$()@L2396:31`〔bindSettingsEvents〕· `$()@L2420:31`〔bindSettingsEvents〕 |
| `sModel` | `L1017:31` | `$()@L1906:93`〔validateLLMForm〕· `$()@L2170:23`〔bindSettingsEvents〕· `$()@L2550:89`〔bindSettingsEvents〕· `$()@L2556:72`〔updateProviderModels〕· `$()@L2596:19`〔applyProviderToLLM〕 |
| `sModelManual` | `L1020:28` | `$()@L1771:41`〔collectSettings〕· `$()@L1862:19`〔buildLlmTestPayload〕· `$()@L1904:20`〔validateLLMForm〕· `$()@L2166:24`〔bindSettingsEvents〕· `$()@L2560:20`〔applyModelOptions〕 |
| `dashLog` | `L829:8728` | `$()@L792:225`〔loadDashLog〕· `$()@L806:907`〔getSelectedDashLogText〕· `$()@L807:46`〔getSelectedDashLogText〕· `$()@L807:409`〔injectDashCopyButton〕 |
| `memoryQqSearch` | `L839:13842` | `$()@L839:14163`〔search〕· `$()@L839:14186`〔search〕· `$()@L839:14214`〔search〕· `$()@L839:14407`〔mountMemory〕 |
| `pluginsGrid` | `L839:14778`· `L3694:322` | `$()@L807:18841`〔loadPlugins〕· `$()@L807:19025`〔loadPlugins〕· `$()@L3699:7`〔loadPlugins〕· `$()@L3754:16`〔loadPlugins〕 |
| `sLlamaModelName` | `L1061:30` | `$()@L2069:39`〔bindSettingsEvents〕· `$()@L2265:21`〔bindSettingsEvents〕· `$()@L2306:21`〔bindSettingsEvents〕· `$()@L2371:26`〔bindSettingsEvents〕 |
| `sLlamaModelPath` | `L1053:30` | `$()@L2069:17`〔bindSettingsEvents〕· `$()@L2263:21`〔bindSettingsEvents〕· `$()@L2304:21`〔bindSettingsEvents〕· `$()@L2363:27`〔bindSettingsEvents〕 |
| `sProvider` | `L1002:29` | `$()@L1612:24`〔loadSettingsData〕· `$()@L2158:26`〔bindSettingsEvents〕· `$()@L2544:26`〔bindSettingsEvents〕· `$()@L2597:22`〔applyProviderToLLM〕 |
| `cmdInput` | `L2673:221` | `$()@L807:21029`〔send〕· `$()@L807:21546`〔send〕· `$()@L807:21624`〔loadCommands〕 |
| `cmdOutput` | `L2673:370` | `$()@L807:20918`〔loadCommands〕· `$()@L807:21091`〔send〕· `closest(#protoLog, #cmdOutput, #consoleOutput, .modal-overlay, #oboOverlay)@L592:14`〔fxTag〕 |
| `consoleInput` | `L839:573` | `$()@L839:763`〔send〕· `$()@L839:1287`〔send〕· `$()@L839:1373`〔mountConsole〕 |
| `lloExePath` | `L3375:34` | `$()@L3481:18`〔mountProtocol〕· `$()@L3569:43`〔classifyPickedPath〕· `$()@L3614:20`〔runDetect〕 |
| `monitorCards` | `L835:2965` | `getElementById@L807:3711`〔loadMonitor〕· `querySelector(#monitorCards .card)@L807:4245`〔loadMonitor〕· `querySelector(#monitorCards .card)@L807:5884`〔updateMonitor〕 |
| `ncQqPath` | `L3363:643` | `$()@L3479:1038`〔mountProtocol〕· `$()@L3569:61`〔classifyPickedPath〕· `$()@L3625:37`〔runDetect〕 |
| `sGroupToolList` | `L1194:12` | `$()@L1657:27`〔loadSettingsData〕· `$()@L1707:16`〔renderGroupToolList〕· `$()@L2066:16`〔bindSettingsEvents〕 |
| `sImageLlamaModelName` | `L1110:30` | `$()@L2070:44`〔bindSettingsEvents〕· `$()@L2446:21`〔bindSettingsEvents〕· `$()@L2479:21`〔bindSettingsEvents〕 |
| `sImageLlamaModelPath` | `L1102:30` | `$()@L2070:17`〔bindSettingsEvents〕· `$()@L2444:21`〔bindSettingsEvents〕· `$()@L2477:21`〔bindSettingsEvents〕 |
| `settingsPanels` | `L856:36` | `getElementById@L908:29`〔initSettingsSearch〕· `getElementById@L2124:29`〔bindSettingsEvents〕· `querySelector(#settingsPanels)@L860:22`〔mountSettings〕 |
| `comfyResult` | `L839:7524` | `$()@L839:9457`〔mountImagegen〕· `$()@L839:12521`〔mountImagegen〕 |
| `consoleOutput` | `L839:263` | `$()@L839:829`〔send〕· `closest(#protoLog, #cmdOutput, #consoleOutput, .modal-overlay, #oboOverlay)@L592:14`〔fxTag〕 |
| `dashBotInfo` | `L829:7758` | `getElementById@L790:212`〔loadDashStatus〕· `getElementById@L791:186`〔loadDashStatus〕 |
| `dashBotState` | `L829:7706` | `getElementById@L790:154`〔loadDashStatus〕· `getElementById@L791:128`〔loadDashStatus〕 |
| `dashCopyBtn` | `L806:294`· `L807:552`· `L806:293`· `L807:551` | `getElementById@L806:183`〔loadDashLog〕· `getElementById@L807:455`〔injectDashCopyButton〕 |
| `decodeSplash` | `L506:97` | `$()@L829:3436`· `$()@L829:4840`〔boot〕 |
| `decodeText` | `L506:143` | `$()@L829:1102`· `$()@L829:5441`〔boot〕 |
| `exitIdleList` | `L1334:12` | `querySelector(#exitIdleList)@L1693:30`〔loadSettingsData〕· `querySelector(#exitIdleList .editable-list-text)@L2154:40`〔bindSettingsEvents〕 |
| `galleryGrid` | **—（无定义）** | `$()@L807:10739`〔loadGallery〕· `$()@L807:10918`〔loadGallery〕 |
| `gmFriendTab` | `L2826:11` | `getElementById@L3054:23`〔switchTab〕· `getElementById@L3066:18`〔mountGroupManager〕 |
| `gmGroups` | `L2825:32` | `getElementById@L2840:26`〔loadGroups〕· `getElementById@L2869:24`〔bindCardActions〕 |
| `gmSearch` | `L2923:94` | `querySelector(#gmSearch)@L2936:24`〔render〕· `querySelector(#gmSearch)@L3003:12`〔openMembers〕 |
| `gmTabFriend` | `L2822:34` | `getElementById@L3052:22`〔switchTab〕· `getElementById@L3076:12`〔mountGroupManager〕 |
| `gmTabGroup` | `L2821:34` | `getElementById@L3051:22`〔switchTab〕· `getElementById@L3075:12`〔mountGroupManager〕 |
| `imgSaveCfg` | `L839:7276` | `$()@L839:10912`〔mountImagegen〕· `$()@L839:11629`〔mountImagegen〕 |
| `imgWorkflow` | `L839:6711` | `$()@L839:10756`〔mountImagegen〕· `$()@L839:10774`〔mountImagegen〕 |
| `llmModeLocal` | `L996:27` | `$()@L1856:43`〔updateLlmModeUI〕· `$()@L2121:21`〔bindSettingsEvents〕 |
| `llmModeRemote` | `L995:35` | `$()@L1856:16`〔updateLlmModeUI〕· `$()@L2120:22`〔bindSettingsEvents〕 |
| `localImageLlmFields` | `L1088:10` | `$()@L1640:33`〔loadSettingsData〕· `$()@L2224:28`〔bindSettingsEvents〕 |
| `localLlmFields` | `L1039:10` | `$()@L1632:28`〔loadSettingsData〕· `$()@L2206:23`〔bindSettingsEvents〕 |
| `memeImportFile` | `L3104:77` | `getElementById@L3150:71`〔_render〕· `getElementById@L3304:31`〔mountMemes〕 |
| `memeSearch` | `L3086:31` | `getElementById@L3202:30`〔_loadSidebar〕· `getElementById@L3303:12`〔mountMemes〕 |
| `mpClearKey` | `L1557:167` | `querySelector(#mpClearKey)@L1568:30`〔openProviderModal〕· `querySelector(#mpClearKey)@L1568:68`〔openProviderModal〕 |
| `mpProtocol` | `L1545:28` | `querySelector(#mpProtocol)@L1562:13`〔openProviderModal〕· `querySelector(#mpProtocol)@L1573:24`〔openProviderModal〕 |
| `ncExePath` | `L3363:349` | `$()@L3479:987`〔mountProtocol〕· `$()@L3623:38`〔runDetect〕 |
| `oboEgoStatus` | `L4032:81` | `$()@L4039:15`〔oboRender〕· `$()@L4040:34`〔oboRender〕 |
| `oboKey` | `L4000:109` | `$()@L4055:62`〔oboBind〕· `$()@L4055:74`〔oboBind〕 |
| `oboNext` | `L506:837` | `$()@L3976:31`〔oboRender〕· `$()@L4050:31`〔oboBind〕 |
| `oboPrev` | `L506:708` | `$()@L3976:12`〔oboRender〕· `$()@L4050:12`〔oboBind〕 |
| `oboProtoCur` | `L4009:81` | `$()@L4019:15`〔oboRender〕· `$()@L4020:34`〔oboRender〕 |
| `oboQq` | `L3999:110` | `$()@L4055:16`〔oboBind〕· `$()@L4055:27`〔oboBind〕 |
| `oboQqPath` | `L4002:85` | `$()@L4055:157`〔oboBind〕· `$()@L4055:172`〔oboBind〕 |
| `oboSkiplnk` | `L506:782` | `$()@L3976:50`〔oboRender〕· `$()@L4050:50`〔oboBind〕 |
| `oboUrl` | `L4001:81` | `$()@L4055:110`〔oboBind〕· `$()@L4055:122`〔oboBind〕 |
| `page-content` | `L574:206` | `getElementById@L600:23`〔fxWatch〕· `$()@L3804:445`〔router〕 |
| `protoAutoCfg` | `L3424:32` | `$()@L3479:57`〔mountProtocol〕· `$()@L3479:260`〔mountProtocol〕 |
| `protoAutoList` | `L3428:14` | `$()@L612:15`· `$()@L629:35` |
| `protoAutoToggle` | `L3426:149` | `$()@L827:23`〔loadProtocolPage〕· `$()@L3479:440`〔mountProtocol〕 |
| `protoLog` | `L3452:12` | `$()@L829:27`〔loadProtocolPage〕· `closest(#protoLog, #cmdOutput, #consoleOutput, .modal-overlay, #oboOverlay)@L592:14`〔fxTag〕 |
| `recallPostList` | `L1234:12` | `querySelector(#recallPostList)@L1690:30`〔loadSettingsData〕· `querySelector(#recallPostList .editable-list-text)@L2140:40`〔bindSettingsEvents〕 |
| `recallPreList` | `L1228:12` | `querySelector(#recallPreList)@L1689:29`〔loadSettingsData〕· `querySelector(#recallPreList .editable-list-text)@L2134:40`〔bindSettingsEvents〕 |
| `recallPrivateList` | `L1250:12` | `querySelector(#recallPrivateList)@L1691:33`〔loadSettingsData〕· `querySelector(#recallPrivateList .editable-list-text)@L2147:40`〔bindSettingsEvents〕 |
| `sApiKeyClear` | `L1011:29` | `$()@L2189:23`〔bindSettingsEvents〕· `$()@L2588:20`〔updateApiKeyStatus〕 |
| `sApiKeyEdit` | `L1010:29` | `$()@L1623:26`〔loadSettingsData〕· `$()@L2177:22`〔bindSettingsEvents〕 |
| `sApiUrl` | `L1005:28` | `$()@L1902:17`〔validateLLMForm〕· `$()@L2595:17`〔applyProviderToLLM〕 |
| `sGroupToolAllowEnable` | `L1190:51` | `$()@L1652:23`〔loadSettingsData〕· `$()@L2064:19`〔bindSettingsEvents〕 |
| `sImageLlamaDriver` | `L1090:31` | `$()@L2442:17`〔bindSettingsEvents〕· `$()@L2475:17`〔bindSettingsEvents〕 |
| `sImageLlamaExePath` | `L1098:30` | `$()@L2443:19`〔bindSettingsEvents〕· `$()@L2476:19`〔bindSettingsEvents〕 |
| `sLlamaDriver` | `L1041:31` | `$()@L2261:17`〔bindSettingsEvents〕· `$()@L2302:17`〔bindSettingsEvents〕 |
| `sLlamaScanRoot` | `L1065:30` | `$()@L2339:26`〔bindSettingsEvents〕· `$()@L2339:62`〔bindSettingsEvents〕 |
| `sModelError` | `L1022:31` | `$()@L2639:19`〔fetchRemoteModels〕· `$()@L2666:19`〔fetchRemoteModels〕 |
| `sModelManualHint` | `L1021:31` | `$()@L2168:22`〔bindSettingsEvents〕· `$()@L2560:48`〔applyModelOptions〕 |
| `sSave` | `L849:37` | `$()@L1925:13`〔markSettingsDirty〕· `$()@L2071:14`〔bindSettingsEvents〕 |
| `sTestLlmResult` | `L1027:26` | `$()@L2115:69`〔bindSettingsEvents〕· `$()@L2117:81`〔bindSettingsEvents〕 |
| `settingsNav` | `L855:33` | `getElementById@L907:26`〔initSettingsSearch〕· `querySelector(#settingsNav)@L859:19`〔mountSettings〕 |
| `snwExePath` | `L3401:34` | `$()@L3495:18`〔mountProtocol〕· `$()@L3619:20`〔runDetect〕 |
| `tkRecent` | `L2776:52` | `querySelector(#tkRecent)@L2781:47`〔load〕· `querySelector(#tkRecent)@L2801:21`〔load〕 |
| `apiKeyStatusText` | `L1009:28` | `$()@L2576:15`〔updateApiKeyStatus〕 |
| `bAiPrivateMaxTokens` | `L2698:115` | `getElementById@L2740:48`〔mountBehavior〕 |
| `bGroupIdleExitMinSilence` | `L2679:111` | `getElementById@L2731:54`〔mountBehavior〕 |
| `bGroupIdleExitProbability` | `L2683:112` | `getElementById@L2733:56`〔mountBehavior〕 |
| `bGroupLastExitCooldown` | `L2682:108` | `getElementById@L2732:51`〔mountBehavior〕 |
| `bGroupMsgBufferWindow` | `L2687:111` | `getElementById@L2735:50`〔mountBehavior〕 |
| `bGroupRecentMsgsWindow` | `L2690:109` | `getElementById@L2736:51`〔mountBehavior〕 |
| `bGroupReplyMaxLength` | `L2691:107` | `getElementById@L2737:49`〔mountBehavior〕 |
| `bGroupSessionIdleTimeout` | `L2694:110` | `getElementById@L2738:53`〔mountBehavior〕 |
| `bGroupSessionMaxReplies` | `L2678:110` | `getElementById@L2730:52`〔mountBehavior〕 |
| `bPokeTimeout` | `L2686:109` | `getElementById@L2734:39`〔mountBehavior〕 |
| `bPrivateReplyInterval` | `L2699:108` | `getElementById@L2741:49`〔mountBehavior〕 |
| `bPrivateReplyMaxLength` | `L2695:107` | `getElementById@L2739:51`〔mountBehavior〕 |
| `bSave` | `L2701:90` | `getElementById@L2727:23`〔mountBehavior〕 |
| `bTriggerBotNick` | **—（无定义）** | `getElementById@L2753:48`〔mountBehavior〕 |
| `bTriggerQuote` | **—（无定义）** | `getElementById@L2755:45`〔mountBehavior〕 |
| `bTriggerSave` | **—（无定义）** | `getElementById@L2747:23`〔mountBehavior〕 |
| `bTriggerTextAt` | **—（无定义）** | `getElementById@L2754:47`〔mountBehavior〕 |
| `bTriggerWords` | **—（无定义）** | `getElementById@L2749:26`〔mountBehavior〕 |
| `botPathMeta` | `L3350:960` | `$()@L3350:2229`〔mountBot〕 |
| `botQqPathBtn` | `L3350:898` | `$()@L3350:2026`〔mountBot〕 |
| `botStartBtn` | `L3350:773` | `$()@L3350:1230`〔mountBot〕 |
| `botStopBtn` | `L3350:833` | `$()@L3350:1633`〔mountBot〕 |
| `ccCancel` | `L3812:47` | `querySelector(#ccCancel)@L3817:23`〔showConfirm〕 |
| `ccConfirm` | `L3813:34` | `querySelector(#ccConfirm)@L3818:24`〔showConfirm〕 |
| `chatlogCopyBtn` | `L837:4649`· `L837:4648` | `getElementById@L837:4549`〔injectChatlogCopyButton〕 |
| `cmdSend` | `L2673:323` | `$()@L807:21577`〔loadCommands〕 |
| `connectionDot` | `L1036:36` | `$()@L2255:25`〔bindSettingsEvents〕 |
| `connectionText` | `L1037:37` | `$()@L2256:26`〔bindSettingsEvents〕 |
| `consoleSend` | `L839:673` | `$()@L839:1322`〔mountConsole〕 |
| `dashProtoInfo` | `L829:8114` | `getElementById@L790:331`〔loadDashStatus〕 |
| `dashProtoState` | `L829:8060` | `getElementById@L790:269`〔loadDashStatus〕 |
| `decodeGrid` | **—（无定义）** | `$()@L829:1133` |
| `emImport` | `L1444:28` | `querySelector(#emImport)@L1449:27`〔openAddModal〕 |
| `emOk` | `L1445:36` | `querySelector(#emOk)@L1448:23`〔openAddModal〕 |
| `emTa` | `L1442:32` | `querySelector(#emTa)@L1447:23`〔openAddModal〕 |
| `emptyImportBtn` | `L3148:314` | `getElementById@L3149:27`〔_render〕 |
| `exitPreview` | `L1338:27` | `$()@L2151:23`〔bindSettingsEvents〕 |
| `exitShortList` | `L1328:12` | `querySelector(#exitShortList)@L1692:31`〔loadSettingsData〕 |
| `gmAuto` | `L2817:48` | `getElementById@L3068:26`〔mountGroupManager〕 |
| `gmClose` | `L2925:38` | `querySelector(#gmClose)@L2932:12`〔openMembers〕 |
| `gmFriends` | `L2826:54` | `getElementById@L3007:24`〔loadFriends〕 |
| `gmGroupTab` | `L2825:11` | `getElementById@L3053:23`〔switchTab〕 |
| `gmMemberList` | `L2927:15` | `querySelector(#gmMemberList)@L2933:22`〔openMembers〕 |
| `gmRefresh` | `L2816:36` | `getElementById@L3065:12`〔mountGroupManager〕 |
| `gmRefreshMembers` | `L2924:40` | `querySelector(#gmRefreshMembers)@L3002:12`〔openMembers〕 |
| `gmStatus` | `L2818:27` | `getElementById@L2829:42`〔status〕 |
| `gmTip` | `L2824:11` | `getElementById@L3077:22`〔mountGroupManager〕 |
| `imageLlmTestResult` | `L1118:28` | `$()@L2437:23`〔bindSettingsEvents〕 |
| `imgBrowseBtn` | `L839:6889` | `$()@L839:10107`〔mountImagegen〕 |
| `imgGenBtn` | `L839:7347` | `$()@L839:11763`〔mountImagegen〕 |
| `imgLlmUrl` | `L839:2656` | `$()@L2233:25`〔bindSettingsEvents〕 |
| `imgModel` | `L839:2415` | `$()@L2232:24`〔bindSettingsEvents〕 |
| `imgTestBtn` | `L839:3949` | `$()@L839:9392`〔mountImagegen〕 |
| `lloApiUrl` | `L3379:32` | `$()@L3482:17`〔mountProtocol〕 |
| `lloReportUrl` | `L3382:32` | `$()@L3483:25`〔mountProtocol〕 |
| `lloWebuiToken` | `L3387:34` | `$()@L3508:15`〔mountProtocol〕 |
| `lloWebuiUrl` | `L3385:32` | `$()@L3527:51`〔mountProtocol〕 |
| `lloWsPort` | `L3381:32` | `$()@L3485:25`〔mountProtocol〕 |
| `lloWsUrl` | `L3380:32` | `$()@L3484:16`〔mountProtocol〕 |
| `localChatSection` | `L1030:10` | `$()@L1853:20`〔updateLlmModeUI〕 |
| `localImageLlmVramWarning` | `L1081:10` | `$()@L2207:23`〔bindSettingsEvents〕 |
| `localLlmTestResult` | `L1075:28` | `$()@L2254:23`〔bindSettingsEvents〕 |
| `logAuto` | `L837:1879` | `getElementById@L837:3000`〔mountChatlogs〕 |
| `logClear` | `L837:1808` | `getElementById@L837:2571`〔mountChatlogs〕 |
| `logCopy` | `L837:1933` | `getElementById@L837:3306`〔mountChatlogs〕 |
| `memeCount` | `L3107:38` | `getElementById@L3346:28`〔updateMemeCount〕 |
| `memeDirList` | `L3089:13` | `getElementById@L3180:25`〔_loadSidebar〕 |
| `memeEmotions` | `L3093:13` | `getElementById@L3219:25`〔_loadSidebar〕 |
| `memeFilterStatus` | `L3110:13` | `getElementById@L3136:31`〔_render〕 |
| `memeGroupStatus` | `L3095:13` | `getElementById@L3233:25`〔_loadSidebar〕 |
| `memeImportDir` | `L3105:32` | `getElementById@L3311:33`〔mountMemes〕 |
| `memeImportUrl` | `L3106:32` | `getElementById@L3319:33`〔mountMemes〕 |
| `memeTagCloud` | `L3091:13` | `getElementById@L3196:25`〔_loadSidebar〕 |
| `memeViewGrid` | `L3100:32` | `getElementById@L3330:12`〔mountMemes〕 |
| `memeViewMasonry` | `L3101:32` | `getElementById@L3331:12`〔mountMemes〕 |
| `memoryCards` | `L839:13516` | `$()@L807:11427`〔loadMemory〕 |
| `memoryDecodeAll` | **—（无定义）** | `$()@L807:17453`〔openMemoryDetail〕 |
| `memoryDetail` | `L839:14032` | `$()@L807:13967`〔openMemoryDetail〕 |
| `memoryDetailContent` | `L807:14396` | `$()@L807:14453`〔openMemoryDetail〕 |
| `memoryEncodeAll` | **—（无定义）** | `$()@L807:17552`〔openMemoryDetail〕 |
| `memoryList` | `L839:13626` | `$()@L807:12369`〔loadMemory〕 |
| `memoryQqList` | `L839:13997` | `$()@L807:13010`〔loadMemory〕 |
| `memoryQqSearchBtn` | `L839:13940` | `$()@L839:14323`〔mountMemory〕 |
| `modelQuickSelect` | `L506:1647` | `getElementById@L1932:24`〔initModelQuickSelect〕 |
| `mpApiKey` | `L1556:27` | `querySelector(#mpApiKey)@L1569:25`〔openProviderModal〕 |
| `mpCancel` | `L1559:47` | `querySelector(#mpCancel)@L1563:8`〔openProviderModal〕 |
| `mpConfirm` | `L1560:34` | `querySelector(#mpConfirm)@L1565:8`〔openProviderModal〕 |
| `mpModels` | `L1554:30` | `querySelector(#mpModels)@L1571:25`〔openProviderModal〕 |
| `mpName` | `L1541:27` | `querySelector(#mpName)@L1566:23`〔openProviderModal〕 |
| `mpUrl` | `L1543:27` | `querySelector(#mpUrl)@L1567:22`〔openProviderModal〕 |
| `ncApiUrl` | `L3363:870` | `$()@L3479:1088`〔mountProtocol〕 |
| `ncReportUrl` | `L3363:980` | `$()@L3479:1146`〔mountProtocol〕 |
| `ncWebuiToken` | `L3367:32` | `$()@L3529:15`〔mountProtocol〕 |
| `ncWebuiUrl` | `L3365:30` | `$()@L3526:50`〔mountProtocol〕 |
| `ncWsPort` | `L3363:1207` | `$()@L3479:1255`〔mountProtocol〕 |
| `ncWsUrl` | `L3363:1094` | `$()@L3479:1198`〔mountProtocol〕 |
| `notifySaveBtn` | `L835:2818` | `getElementById@L837:52`〔bindNotifyActions〕 |
| `notifyTestBtn` | `L835:2880` | `getElementById@L837:940`〔bindNotifyActions〕 |
| `oboBody` | `L506:641` | `$()@L3974:55`〔oboRender〕 |
| `oboGoEgo` | `L4033:83` | `$()@L4036:14`〔oboRender〕 |
| `oboGoProto` | `L4012:83` | `$()@L4015:14`〔oboRender〕 |
| `oboPreviewBtn` | `L506:1513` | `$()@L4067:12`〔oboBind〕 |
| `oboProg` | `L506:594` | `$()@L3974:76`〔oboRender〕 |
| `oboSub` | `L506:554` | `$()@L3974:35`〔oboRender〕 |
| `oboTitle` | `L506:513` | `$()@L3974:14`〔oboRender〕 |
| `pageSub` | `L574:155` | `$()@L3804:295`〔router〕 |
| `pageTitle` | `L574:101` | `$()@L3804:258`〔router〕 |
| `pfbCancel` | `L3643:38` | `querySelector(#pfbCancel)@L3650:12`〔openProtoFileBrowser〕 |
| `pfbList` | `L3645:15` | `querySelector(#pfbList)@L3651:27`〔openProtoFileBrowser〕 |
| `pfbPath` | `L3640:33` | `querySelector(#pfbPath)@L3652:27`〔openProtoFileBrowser〕 |
| `pfbSelect` | `L3642:40` | `querySelector(#pfbSelect)@L3653:27`〔openProtoFileBrowser〕 |
| `pfbUp` | `L3641:32` | `querySelector(#pfbUp)@L3686:12`〔openProtoFileBrowser〕 |
| `pluginTagBar` | `L3694:118` | `$()@L3712:18`〔loadPlugins〕 |
| `pluginsCount` | `L3694:215` | `$()@L3736:19`〔loadPlugins〕 |
| `pluginsRefresh` | `L839:14708`· `L3694:260` | `$()@L3800:13`〔loadPlugins〕 |
| `pmClose` | `L1833:80` | `querySelector(#pmClose)@L1835:8`〔openPreviewModal〕 |
| `protoAutoApply` | `L3430:39` | `$()@L627:16` |
| `protoAutoStatus` | `L3425:61` | `$()@L626:16` |
| `protoCheckPanel` | `L3362:12` | `$()@L712:13`〔renderProtoCheck〕 |
| `protoQqPathBtn` | `L3441:37` | `$()@L3541:15`〔mountProtocol〕 |
| `protoRiskPanel` | `L3361:1010` | `$()@L697:13`〔renderProtoRiskPanel〕 |
| `recallPostPreview` | `L1223:27` | `$()@L2131:29`〔bindSettingsEvents〕 |
| `recallPrePreview` | `L1222:27` | `$()@L2130:28`〔bindSettingsEvents〕 |
| `recallPrivatePreview` | `L1247:27` | `$()@L2144:32`〔bindSettingsEvents〕 |
| `remoteApiSection` | `L999:10` | `$()@L1852:21`〔updateLlmModeUI〕 |
| `sFetchModels` | `L1018:37` | `$()@L2541:20`〔bindSettingsEvents〕 |
| `sOpenImageLlm` | `L1117:35` | `$()@L2436:24`〔bindSettingsEvents〕 |
| `sOpenLocalLlm` | `L1074:35` | `$()@L2253:24`〔bindSettingsEvents〕 |
| `sOpenWizard` | `L997:33` | `$()@L2118:17`〔bindSettingsEvents〕 |
| `sReload` | `L850:35` | `$()@L2098:14`〔bindSettingsEvents〕 |
| `sScanLocalLlm` | `L1071:29` | `$()@L2333:24`〔bindSettingsEvents〕 |
| `sSearch` | `L847:28` | `$()@L903:15`〔initSettingsSearch〕 |
| `sStartImageLlm` | `L1115:29` | `$()@L2434:25`〔bindSettingsEvents〕 |
| `sStartLocalLlm` | `L1072:29` | `$()@L2251:25`〔bindSettingsEvents〕 |
| `sStopImageLlm` | `L1116:29` | `$()@L2435:24`〔bindSettingsEvents〕 |
| `sStopLocalLlm` | `L1073:29` | `$()@L2252:24`〔bindSettingsEvents〕 |
| `sTest` | `L851:35` | `$()@L2114:14`〔bindSettingsEvents〕 |
| `sTestImageLlm` | `L1114:37` | `$()@L2433:24`〔bindSettingsEvents〕 |
| `sTestLlmForm` | `L1026:35` | `$()@L2116:18`〔bindSettingsEvents〕 |
| `sTestLocalLlm` | `L1070:37` | `$()@L2250:24`〔bindSettingsEvents〕 |
| `snwApiUrl` | `L3405:32` | `$()@L3496:17`〔mountProtocol〕 |
| `snwReportUrl` | `L3408:32` | `$()@L3497:25`〔mountProtocol〕 |
| `snwWebuiToken` | `L3414:34` | `$()@L3521:15`〔mountProtocol〕 |
| `snwWebuiUrl` | `L3411:32` | `$()@L3519:51`〔mountProtocol〕 |
| `snwWsPort` | `L3407:32` | `$()@L3499:25`〔mountProtocol〕 |
| `snwWsUrl` | `L3406:32` | `$()@L3498:16`〔mountProtocol〕 |
| `spCancel` | `L3831:49` | `querySelector(#spCancel)@L3837:10`〔showPrompt〕 |
| `spConfirm` | `L3832:36` | `querySelector(#spConfirm)@L3838:10`〔showPrompt〕 |
| `spInput` | `L3830:29` | `querySelector(#spInput)@L3834:24`〔showPrompt〕 |
| `splashBarFill` | `L506:315` | `$()@L829:4876`〔boot〕 |
| `splashSub` | `L506:233` | `$()@L829:4915`〔boot〕 |
| `tbClose` | `L506:1167` | `getElementById@L776:24`〔initTitlebar〕 |
| `tbMax` | `L506:1102` | `getElementById@L775:22`〔initTitlebar〕 |
| `tbMin` | `L506:1046` | `getElementById@L774:22`〔initTitlebar〕 |
| `tkBar` | `L2772:93` | `querySelector(#tkBar)@L2789:21`〔load〕 |
| `wzCancel` | `L1992:26` | `querySelector(#wzCancel)@L2031:8`〔openSetupWizard〕 |
| `wzFetch` | `L1989:26` | `querySelector(#wzFetch)@L2013:8`〔openSetupWizard〕 |
| `wzKey` | `L1985:27` | `querySelector(#wzKey)@L2000:22`〔openSetupWizard〕 |
| `wzModel` | `L1988:81` | `querySelector(#wzModel)@L1998:25`〔openSetupWizard〕 |
| `wzMsg` | `L1990:30` | `querySelector(#wzMsg)@L1999:22`〔openSetupWizard〕 |
| `wzProvider` | `L1982:28` | `querySelector(#wzProvider)@L1995:24`〔openSetupWizard〕 |
| `wzSave` | `L1993:34` | `querySelector(#wzSave)@L2033:8`〔openSetupWizard〕 |
| `wzUrl` | `L1986:27` | `querySelector(#wzUrl)@L1997:22`〔openSetupWizard〕 |

## 1.4 全部被 JS 引用的 class 选择器（红线，去重 43 个）

按 JS 选择器中出现次数降序。「CSS 规则」列=该 class 在 `<style>` 中是否有样式定义（行号）。

| class | JS 引用次数 | 是否在 CSS 中有规则（行号） |
|---|---|---|
| `.card` | 9 | 有：`83`, `278`, `280`, `386`, `387`, `477` |
| `.emotion-tab` | 6 | 有：`223`, `225`, `242` |
| `.editable-list-text` | 5 | 有：`113`, `116`, `215` |
| `.log-check` | 5 | 有：`180` |
| `.panel` | 5 | 有：`83`, `388`, `389`, `477`, `478` |
| `.card-value` | 4 | 有：`83` |
| `.egg-option` | 4 | 有：`422`, `424`, `425` |
| `.grid-4` | 4 | 有：`83` |
| `.log` | 4 | 有：`83` |
| `.log-row` | 4 | 有：`179`, `180`, `393`, `394` |
| `.log-time` | 4 | 有：`83` |
| `.meta` | 4 | 有：`83` |
| `.selected` | 4 | 有：`180` |
| `.settings-nav-item` | 4 | 有：`83`, `85`, `143`, `180`, `280`, `282` |
| `.btn` | 3 | 有：`83`, `105`, `145`, `147`, `149`, `151` |
| `.mem-enc` | 3 | 有：`83` |
| `.pfb-row` | 3 | **无 CSS 规则（纯 JS 状态钩子）** |
| `.side-rail-item` | 3 | 有：`4`, `21`, `26`, `31`, `63`, `67` |
| `.tool-item` | 3 | 有：`363`, `366`, `367`, `371`, `372`, `388` |
| `.egg-select-wrap` | 2 | 有：`396`, `404`, `410`, `415` |
| `.obo-page` | 2 | 有：`329`, `331`, `332` |
| `.panel-title` | 2 | 有：`83` |
| `.settings-actions` | 2 | 有：`128`, `180` |
| `.settings-panel` | 2 | 有：`88`, `89`, `142`, `200`, `201`, `204` |
| `.cmd-bar` | 1 | 有：`83`, `137` |
| `.decoded` | 1 | 有：`83` |
| `.editable-list-check` | 1 | 有：`115`, `209`, `212` |
| `.editable-list-del` | 1 | 有：`116`, `118`, `216` |
| `.field-desc` | 1 | 有：`94` |
| `.field-hint` | 1 | 有：`204` |
| `.field-label` | 1 | 有：`92` |
| `.is-hover` | 1 | 有：`424` |
| `.is-selected` | 1 | 有：`424` |
| `.lightbox` | 1 | 有：`253` |
| `.lightbox-backdrop` | 1 | 有：`255` |
| `.log-type` | 1 | 有：`180` |
| `.mem-stream` | 1 | **无 CSS 规则（纯 JS 状态钩子）** |
| `.meme-card` | 1 | 有：`228`, `232`, `235`, `238` |
| `.modal-overlay` | 1 | 有：`128`, `160`, `218` |
| `.settings-panel-title` | 1 | 有：`89`, `90`, `92` |
| `.tag-chip` | 1 | 有：`351`, `356`, `357` |
| `.tag-pill` | 1 | 有：`237`, `250`, `252`, `253` |
| `.toggle` | 1 | 有：`83`, `158`, `185` |

**CSS 里存在但 JS 选择器从不引用**（重做容器结构时可自由改动，但会改变视觉）：共 217 个 class。
其中规则数最多的：`.btn`(28条)、`.side-rail-item`(22条)、`.aos-animate`(18条)、`.card`(17条)、`.badge`(12条)、`.input`(11条)、`.toast`(11条)、`.on`(10条)、`.ac`(9条)、`.active`(9条)、`.log`(8条)、`.settings-nav-item`(8条)。

### 1.4.1 被 JS `classList` 读写的状态 class（必须保留对应 CSS 规则）

| class | 操作 | 次数 |
|---|---|---|
| `.ac` | `classList.toggle` | 5 |
| `.active` | `classList.add` | 2 |
| `.active` | `classList.remove` | 2 |
| `.active` | `classList.toggle` | 1 |
| `.aos-animate` | `classList.add` | 1 |
| `.aos-animate` | `classList.remove` | 1 |
| `.aos-init` | `classList.add` | 1 |
| `.cur` | `classList.add` | 1 |
| `.cur` | `classList.remove` | 1 |
| `.decoded` | `classList.add` | 3 |
| `.egg-native` | `classList.add` | 1 |
| `.egg-select-trigger` | `classList.contains` | 1 |
| `.egg-select-wrap` | `classList.contains` | 1 |
| `.fade-out` | `classList.add` | 1 |
| `.from-bottom` | `classList.add` | 1 |
| `.from-bottom` | `classList.remove` | 1 |
| `.is-disabled` | `classList.toggle` | 1 |
| `.is-empty` | `classList.toggle` | 1 |
| `.is-hover` | `classList.add` | 1 |
| `.is-hover` | `classList.contains` | 1 |
| `.is-hover` | `classList.toggle` | 1 |
| `.is-open` | `classList.add` | 1 |
| `.is-open` | `classList.remove` | 1 |
| `.is-selected` | `classList.add` | 1 |
| `.is-selected` | `classList.contains` | 1 |
| `.is-selected` | `classList.toggle` | 1 |
| `.log-check` | `classList.contains` | 3 |
| `.log-row-new` | `classList.add` | 1 |
| `.mem-enc` | `classList.contains` | 1 |
| `.on` | `classList.contains` | 1 |
| `.on` | `classList.toggle` | 9 |
| `.open` | `classList.add` | 1 |
| `.open` | `classList.remove` | 1 |
| `.page-enter` | `classList.add` | 1 |
| `.page-enter` | `classList.remove` | 1 |
| `.removing` | `classList.add` | 1 |
| `.selected` | `classList.add` | 1 |
| `.selected` | `classList.contains` | 1 |
| `.selected` | `classList.toggle` | 5 |
| `.show` | `classList.add` | 3 |
| `.show` | `classList.contains` | 1 |
| `.show` | `classList.remove` | 2 |

> 特别提醒：`.on`（10 条 CSS 规则）、`.ac`（9 条）、`.selected`、`.active`、`.show`、`.open`、`.cur`、
> `.is-selected`、`.is-hover`、`.is-open`、`.is-disabled`、`.is-empty`、`.decoded`、`.log-row-new`、
> `.page-enter`、`.fade-out`、`.removing`、`.from-bottom`、`.aos-init`、`.aos-animate`、`.egg-native`
> 这些都是**运行时通过 JS 切换**的，重做 CSS 时必须保留同名规则，否则状态视觉全部丢失。

## 1.5 被 JS 依赖的 data-* 属性（红线，并集去重 49 个）

分三类，**危险程度从高到低**：

### A. 出现在 JS 选择器字符串里的 data-*（结构性依赖，删了直接找不到元素）

| data-* | 选择器出现次数 | 引用位置 |
|---|---|---|
| `data-act` | 5 | `L2875:36`· `L2900:41`· `L2906:41`· `L2907:43`· `L3030:34` |
| `data-dir` | 1 | `L3673:42` |
| `data-file` | 1 | `L3676:42` |
| `data-lb-close` | 1 | `L3284:68` |
| `data-lb-del` | 1 | `L3285:42` |
| `data-lb-tag` | 1 | `L3287:42` |
| `data-meme-del` | 1 | `L3263:28` |
| `data-meme-tag` | 1 | `L3266:28` |
| `data-panel` | 3 | `L885:43`· `L923:43`· `L2076:45` |
| `data-plugin-open` | 4 | `L807:20510`· `L3789:10`· `L3792:10`· `L3804:763` |
| `data-plugin-open-btn` | 1 | `L3792:10` |
| `data-plugin-toggle` | 2 | `L807:20131`· `L3772:10` |
| `data-qq` | 1 | `L807:13678` |
| `data-role` | 9 | `L2960:29`· `L2963:38`· `L2968:29`· `L2971:29`· `L2974:110`· `L2977:29`· `L2980:29`· `L3037:35` |
| `data-tool` | 2 | `L1722:77`· `L1738:40` |
| `data-uid` | 5 | `L2962:32`· `L2969:85`· `L2973:32`· `L2978:85`· `L2982:32` |

> `data-aos` 的 116 处命中全部在 `<style>`（L439–L442 的 CSS 属性选择器）与内联 AOS 库（L577）中，
> **不是本应用业务契约**，是内联的 AOS 动效库自带机制。

### B. 通过 `el.dataset.X` 读写的属性（28 个驼峰名）

`dataset` 是驼峰访问，对应 HTML 里的 `data-kebab-case`。对应关系：

| dataset 访问 | 对应 HTML 属性 | 次数 |
|---|---|---|
| `dataset.act` | `data-act` | 3 |
| `dataset.aos` | `data-aos` | 1 |
| `dataset.bound` | `data-bound` | 4 |
| `dataset.checked` | `data-checked` | 4 |
| `dataset.dir` | `data-dir` | 3 |
| `dataset.emotion` | `data-emotion` | 2 |
| `dataset.enabled` | `data-enabled` | 2 |
| `dataset.file` | `data-file` | 1 |
| `dataset.gid` | `data-gid` | 1 |
| `dataset.group` | `data-group` | 2 |
| `dataset.id` | `data-id` | 2 |
| `dataset.idx` | `data-idx` | 2 |
| `dataset.index` | `data-index` | 1 |
| `dataset.m` | `data-m` | 3 |
| `dataset.memeDel` | `data-meme-del` | 1 |
| `dataset.memeTag` | `data-meme-tag` | 1 |
| `dataset.name` | `data-name` | 1 |
| `dataset.origin` | `data-origin` | 4 |
| `dataset.page` | `data-page` | 6 |
| `dataset.panel` | `data-panel` | 1 |
| `dataset.plugin` | `data-plugin` | 1 |
| `dataset.pluginOpen` | `data-plugin-open` | 3 |
| `dataset.pluginOpenBtn` | `data-plugin-open-btn` | 1 |
| `dataset.pluginToggle` | `data-plugin-toggle` | 2 |
| `dataset.qq` | `data-qq` | 1 |
| `dataset.tag` | `data-tag` | 3 |
| `dataset.tool` | `data-tool` | 1 |
| `dataset.uid` | `data-uid` | 7 |

### C. 通过 `getAttribute` / `setAttribute` 读写的 data-*

| 操作 | data-* | 位置 |
|---|---|---|
| getAttribute | `data-aos-once` | `L577:12638` |
| getAttribute | `data-aos-offset` | `L577:13421` |
| getAttribute | `data-aos-anchor` | `L577:13462` |
| getAttribute | `data-aos-anchor-placement` | `L577:13512` |
| getAttribute | `data-dash-key` | `L800:39` |
| getAttribute | `data-dash-key` | `L804:39` |
| getAttribute | `data-key` | `L835:350` |
| setAttribute | `data-aos-easing` | `L577:1902` |
| setAttribute | `data-aos-duration` | `L577:1974` |
| setAttribute | `data-aos-delay` | `L577:2050` |
| setAttribute | `data-aos` | `L593:10` |
| setAttribute | `data-aos-delay` | `L594:10` |
| setAttribute | `data-aos-duration` | `L595:10` |
| setAttribute | `data-icon` | `L788:128` |

### D. 模板里出现但 JS 未按属性名读取的 data-*（低风险，但建议保留）

`data-bot`（L3350 bot 卡片）、`data-id`、`data-dash-key`、`data-aos-*`（AOS 库）、`data-shuffle`（shell 导航文字动效）。
其中 `data-dash-key` 实际由 `getAttribute('data-key')` 配合 `data-key` 读取（L800:39、L804:39、L835:350）。

## 1.6 事件绑定方式（四种并存，迁移时必须逐一保留）

### 方式 1：内联 HTML 属性 `on*=` — 共 11 处（全部在 `<script>` 生成的 HTML 里）

| 事件 | 位置 | 代码片段 | 所在函数 | 调用的全局函数（**红线函数名**） |
|---|---|---|---|---|
| `onclick` | `L707:37` | `protoApplySwitch(\'` | `renderProtoRiskPanel` | `protoApplySwitch()` |
| `onclick` | `L829:7535` | `router(` | `mountDashboard` | `router()` |
| `onclick` | `L829:7869` | `router(` | `mountDashboard` | `router()` |
| `onclick` | `L829:8208` | `router(` | `mountDashboard` | `router()` |
| `onerror` | `L2946:123` | `this.style.opacity=.2` | `render` | （仅 `this.style`） |
| `onerror` | `L3017:123` | `this.style.opacity=.2` | `loadFriends` | （仅 `this.style`） |
| `onclick` | `L3446:82` | `protoOpen(` | `mountProtocol` | `protoOpen()` |
| `onclick` | `L3447:91` | `protoOpen(` | `mountProtocol` | `protoOpen()` |
| `onclick` | `L3448:94` | `protoOpen(` | `mountProtocol` | `protoOpen()` |
| `onclick` | `L3449:87` | `protoOpen(` | `mountProtocol` | `protoOpen()` |
| `onclick` | `L3751:102` | `testPluginApi(` | `apiStatus` | `testPluginApi()` |

### 方式 2：`.on<evt> =` 属性赋值 — 共 44 处（click 37 / change 7）

这些是用 `createElement` 动态建 DOM 后挂事件的，**不依赖 HTML 里的名字，但依赖建出来的类名/结构**：

- `.onclick` @ `L806:479`
- `.onclick` @ `L807:721`
- `.onclick` @ `L837:4821`
- `.onchange` @ `L839:10286`
- `.onclick` @ `L964:8`
- `.onclick` @ `L1451:9`
- `.onclick` @ `L1459:13`
- `.onchange` @ `L1465:13`
- `.onclick` @ `L1488:11`
- `.onclick` @ `L1489:12`
- `.onchange` @ `L1490:12`
- `.onclick` @ `L1563:35`
- `.onclick` @ `L1565:36`
- `.onclick` @ `L1835:34`
- `.onchange` @ `L1959:6`
- `.onchange` @ `L2012:10`
- `.onclick` @ `L2013:34`
- `.onclick` @ `L2031:35`
- `.onclick` @ `L2033:33`
- `.onclick` @ `L2876:12`
- `.onchange` @ `L2901:27`
- `.onclick` @ `L2908:27`
- `.onclick` @ `L2932:38`
- `.onclick` @ `L2961:10`
- `.onclick` @ `L2969:10`
- `.onclick` @ `L2972:10`
- `.onclick` @ `L2978:10`
- `.onclick` @ `L2981:10`
- `.onclick` @ `L3002:47`
- `.onchange` @ `L3031:11`
- `.onclick` @ `L3038:10`
- `.onclick` @ `L3065:39`
- `.onclick` @ `L3069:10`
- `.onclick` @ `L3075:40`
- `.onclick` @ `L3076:41`
- `.onclick` @ `L3650:40`
- `.onclick` @ `L3674:14`
- `.onclick` @ `L3677:14`
- `.onclick` @ `L3686:36`
- `.onclick` @ `L3687:11`
- `.onclick` @ `L3820:9`
- `.onclick` @ `L3821:10`
- `.onclick` @ `L3837:37`
- `.onclick` @ `L3838:38`

### 方式 3：`addEventListener` — 共 139 处

事件类型分布：

| 事件 | 次数 |
|---|---|
| `click` | 99 |
| `change` | 8 |
| `keydown` | 7 |
| `input` | 7 |
| `mousedown` | 4 |
| `scroll` | 3 |
| `DOMContentLoaded` | 3 |
| `resize` | 2 |
| `orientationchange` | 1 |
| `transitionend` | 1 |
| `dragover` | 1 |
| `drop` | 1 |
| `message` | 1 |
| `mouseup` | 1 |

### 方式 4：事件委托（读 `e.target`）— 共 32 处

**这是重做时最容易踩的坑**：委托依赖「事件源元素上的 class / data-*」，
一旦给子元素换类名或抽掉 `data-*`，委托链立刻断掉。

| 位置 | 所在函数 |
|---|---|
| `L801:366` | `loadDashLog` |
| `L807:9919` | `loadLogs` |
| `L807:9931` | `loadLogs` |
| `L807:17703` | `openMemoryDetail` |
| `L835:666` | `__logRender` |
| `L835:678` | `__logRender` |
| `L839:10325` | `mountImagegen` |
| `L839:10343` | `mountImagegen` |
| `L839:10479` | `mountImagegen` |
| `L954:12` | `refreshCards` |
| `L968:17` | `renderProviderPanel` |
| `L1486:44` | `openAddModal` |
| `L1509:17` | `mountEditableList` |
| `L1518:17` | `mountEditableList` |
| `L1564:42` | `openProviderModal` |
| `L1836:42` | `openPreviewModal` |
| `L2032:45` | `openSetupWizard` |
| `L2931:56` | `openMembers` |
| `L3271:13` | `bindGridActions` |
| `L3284:50` | `openLightbox` |
| `L3284:89` | `openLightbox` |
| `L3303:88` | `mountMemes` |
| `L3306:30` | `mountMemes` |
| `L3309:5` | `mountMemes` |
| `L3649:56` | `openProtoFileBrowser` |
| `L3822:42` | `showConfirm` |
| `L3840:44` | `showPrompt` |
| `L3873:15` | `—` |
| `L3879:15` | `—` |
| `L3888:15` | `—` |
| `L3888:27` | `—` |
| `L3888:46` | `—` |

## 1.7 全局函数名红线

### 被内联 `on*=` 属性直接调用的函数名（必须在全局作用域可见，改名即断）

| 函数名 | 被内联调用次数 | 引用位置 |
|---|---|---|
| `protoOpen()` | 4 | `L3446:82`· `L3447:91`· `L3448:94`· `L3449:87` |
| `router()` | 3 | `L829:7535`· `L829:7869`· `L829:8208` |
| `protoApplySwitch()` | 1 | `L707:37` |
| `testPluginApi()` | 1 | `L3751:102` |

### 被 `router()` 直接映射的页面挂载函数（`pageMounts`，L829）

| page key | 挂载函数 |
|---|---|
| `dashboard` | `mountDashboard()` |
| `monitor` | `mountMonitor()` |
| `chatlogs` | `mountChatlogs()` |
| `console` | `mountConsole()` |
| `imagegen` | `mountImagegen()` |
| `memory` | `mountMemory()` |
| `plugins` | `mountPlugins()` |
| `settings` | `mountSettings()` |
| `commands` | `mountCommands()` |
| `bot` | `mountBot()` |
| `protocol` | `mountProtocol()` |
| `behavior` | `mountBehavior()` |
| `groupmanage` | `mountGroupManager()` |
| `tokens` | `mountTokens()` |
| `memes` | `mountMemes()` |

> `router(page)` 的定义在 **L3804**。它做：①清空 `#page-content`；②设置 `container.dataset.page`；
> ③写 `#pageTitle` / `#pageSub`；④切 `#sideRail .side-rail-item` 的 `.ac` 类；⑤调 `pageMounts[page]`。

---

# 第 2 部分：逐页红线（重点）

## 2.0 页面定位方法与排序总表

### 定位方法（可复核）

所有范围用「找到 `function mountXxx(root)` 的 `{`，再做花括号配对求匹配 `}`」得到，
配对器会正确跳过字符串、模板串、`${}` 插值、注释。**未使用简单正则**，因此不会串页。

每个页面额外给出「渲染区字符数」= `root.innerHTML =` 之后那个表达式（模板串或 `'...' + '...'` 串联链）的字面长度。

### 排序总表（按函数体字符数降序 = 迁移工作量排序）

| # | page | 挂载函数 | 函数起止（L行:列 – L行:列） | 函数体字符 | 渲染区字符 | 模板内 id 数 |
|---|---|---|---|---|---|---|
| 1 | `protocol` | `mountProtocol()` | `L3361:1` – `L3692:1` | 25,589 | 10,233 | 71 |
| 2 | `groupmanage` | `mountGroupManager()` | `L2813:1` – `L3081:1` | 17,022 | 946 | 10 |
| 3 | `memes` | `mountMemes()` | `L3082:1` – `L3343:1` | 15,035 | 1,727 | 13 |
| 4 | `imagegen` | `mountImagegen()` | `L839:1461` – `L839:13439` | 11,978 | 6,084 | 26 |
| 5 | `behavior` | `mountBehavior()` | `L2673:553` – `L2761:1` | 6,708 | 2,618 | 13 |
| 6 | `tokens` | `mountTokens()` | `L2763:1` – `L2812:1` | 3,779 | 1,251 | 2 |
| 7 | `settings` | `mountSettings()` | `L841:271` – `L901:1` | 2,574 | 712 | 6 |
| 8 | `bot` | `mountBot()` | `L3350:1` – `L3350:2555` | 2,554 | 1,148 | 9 |
| 9 | `chatlogs` | `mountChatlogs()` | `L837:1425` – `L837:3826` | 2,401 | 821 | 7 |
| 10 | `dashboard` | `mountDashboard()` | `L829:7360` – `L830:147` | 1,785 | 1,548 | 8 |
| 11 | `monitor` | `mountMonitor()` | `L835:1504` – `L835:3237` | 1,733 | 1,646 | 8 |
| 12 | `console` | `mountConsole()` | `L839:1` – `L839:1460` | 1,459 | 678 | 3 |
| 13 | `memory` | `mountMemory()` | `L839:13440` – `L839:14510` | 1,070 | 620 | 6 |
| 14 | `commands` | `mountCommands()` | `L2673:1` – `L2673:552` | 551 | 485 | 3 |
| 15 | `plugins` | `mountPlugins()` | `L3694:1` – `L3694:362` | 361 | 298 | 4 |

> 同一批数据按**渲染区字符数**（=你实际要重写的 HTML 模板体量）降序为：
> `protocol`(10,233) → `imagegen`(6,084) → `behavior`(2,618) → `memes`(1,727) → `monitor`(1,646) → `dashboard`(1,548) → `tokens`(1,251) → `bot`(1,148) → `groupmanage`(946) → `chatlogs`(821) → `settings`(712) → `console`(678) → `memory`(620) → `commands`(485) → `plugins`(298)。
> 两种口径的差异说明：`groupmanage` / `memes` 的函数体很大但模板很小 ——
> 它们的 HTML 是**在 JS 里用 `createElement` 动态拼出来的**，重做时改的是 JS 里的建 DOM 代码，不是模板字符串。

**最重的 3 个页面（按函数体字符数）**：

1. `protocol` — **25,589 字符**（`L3361:1 – L3692:1`，71 个 id，26 个按钮；主体全挤在 L3361 一行内，迁移量最大，务必拆成多轮）
2. `groupmanage` — **17,022 字符**（L2813–L3081，动态建 DOM 型，`e.target` 委托密集）
3. `memes` — **15,035 字符**（L3082–L3343，14 个 `addEventListener`，灯箱/拖拽/网格）

**最轻的 3 个**：`plugins`(361)、`commands`(551)、`memory`(1,070)。

### 两种渲染写法（决定你怎么改模板）

| 写法 | 页面 | 含义 |
|---|---|---|
| 模板字符串 `` root.innerHTML = `...` `` | protocol, imagegen, monitor, dashboard, bot, chatlogs, settings, console, memory, commands, plugins | 可整段替换为 HTML，最易重做 |
| 字符串串联 `root.innerHTML = '...' + '...'` | behavior, memes, tokens, groupmanage | 同样可整段替换，但注意 JS 里用了 `\'` 转义引号，正则易漏 |

---

## 2.1 protocol

**用途**：协议层管理：驱动选择（NapCat / LLOneBot / SnowLuma）、各驱动的 exe 路径 / API+WS 地址 / WebUI token、自动检测、启动/停止协议端、风险面板、协议日志流、文件浏览器弹窗。

### 渲染函数与真实起止行号

| 项 | 值 |
|---|---|
| 挂载函数 | `mountProtocol(root)`  |
| 函数起止 | **`L3361:1` – `L3692:1`** |
| 函数体字符数 | **25,589** |
| 渲染写法 | `template` |
| 渲染区起止 | `L3361:49` – `L3452:189` |
| 渲染区字符数 | **10,233** |
| 直接在函数内定义的内层函数 | `pickFileFor()`、`classifyPickedPath()`、`applyProtoPick()`、`runDetect()`、`openProtoFileBrowser()`、`close()`、`render()` |

### 该页模板里的 id 清单（按出现顺序，71 个）

| # | id | 定义位置 | JS 是否引用 |
|---|---|---|---|
| 1 | `protoCards` | `L3361:178` | — 仅 CSS/静态 |
| 2 | `protoStatusValue` | `L3361:276` | — 仅 CSS/静态 |
| 3 | `protoDriverValue` | `L3361:396` | — 仅 CSS/静态 |
| 4 | `protoReadyValue` | `L3361:518` | — 仅 CSS/静态 |
| 5 | `protoBridgeValue` | `L3361:715` | — 仅 CSS/静态 |
| 6 | `protoApiValue` | `L3361:837` | — 仅 CSS/静态 |
| 7 | `protoWsValue` | `L3361:955` | — 仅 CSS/静态 |
| 8 | `protoRiskPanel` | `L3361:1010` | ✅ 有 |
| 9 | `protoCheckPanel` | `L3362:12` | ✅ 有 |
| 10 | `protoNapcatCfg` | `L3363:38` | — 仅 CSS/静态 |
| 11 | `ncExePath` | `L3363:349` | ✅ 有 |
| 12 | `ncBrowseExe` | `L3363:450` | — 仅 CSS/静态 |
| 13 | `ncDetectExe` | `L3363:517` | — 仅 CSS/静态 |
| 14 | `ncQqPath` | `L3363:643` | ✅ 有 |
| 15 | `ncBrowseQq` | `L3363:728` | — 仅 CSS/静态 |
| 16 | `ncDetectQq` | `L3363:794` | — 仅 CSS/静态 |
| 17 | `ncApiUrl` | `L3363:870` | ✅ 有 |
| 18 | `ncReportUrl` | `L3363:980` | ✅ 有 |
| 19 | `ncWsUrl` | `L3363:1094` | ✅ 有 |
| 20 | `ncWsPort` | `L3363:1207` | ✅ 有 |
| 21 | `ncWebuiUrl` | `L3365:30` | ✅ 有 |
| 22 | `ncWebuiToken` | `L3367:32` | ✅ 有 |
| 23 | `ncCopyTokenBtn` | `L3368:37` | — 仅 CSS/静态 |
| 24 | `ncOpenWebuiBtn` | `L3369:37` | — 仅 CSS/静态 |
| 25 | `ncSaveBtn` | `L3370:119` | — 仅 CSS/静态 |
| 26 | `ncDetectAll` | `L3370:186` | — 仅 CSS/静态 |
| 27 | `ncPickExeBtn` | `L3370:253` | — 仅 CSS/静态 |
| 28 | `protoLlonebotCfg` | `L3371:12` | — 仅 CSS/静态 |
| 29 | `lloExePath` | `L3375:34` | ✅ 有 |
| 30 | `lloBrowseExe` | `L3376:33` | — 仅 CSS/静态 |
| 31 | `lloDetectExe` | `L3377:39` | — 仅 CSS/静态 |
| 32 | `lloApiUrl` | `L3379:32` | ✅ 有 |
| 33 | `lloWsUrl` | `L3380:32` | ✅ 有 |
| 34 | `lloWsPort` | `L3381:32` | ✅ 有 |
| 35 | `lloReportUrl` | `L3382:32` | ✅ 有 |
| 36 | `lloWebuiUrl` | `L3385:32` | ✅ 有 |
| 37 | `lloWebuiToken` | `L3387:34` | ✅ 有 |
| 38 | `lloCopyTokenBtn` | `L3388:39` | — 仅 CSS/静态 |
| 39 | `lloSaveBtn` | `L3392:39` | — 仅 CSS/静态 |
| 40 | `lloPickExeBtn` | `L3393:37` | — 仅 CSS/静态 |
| 41 | `protoSnowlumaCfg` | `L3396:12` | — 仅 CSS/静态 |
| 42 | `snwExePath` | `L3401:34` | ✅ 有 |
| 43 | `snwBrowseExe` | `L3402:33` | — 仅 CSS/静态 |
| 44 | `snwDetectExe` | `L3403:39` | — 仅 CSS/静态 |
| 45 | `snwApiUrl` | `L3405:32` | ✅ 有 |
| 46 | `snwWsUrl` | `L3406:32` | ✅ 有 |
| 47 | `snwWsPort` | `L3407:32` | ✅ 有 |
| 48 | `snwReportUrl` | `L3408:32` | ✅ 有 |
| 49 | `snwWebuiUrl` | `L3411:32` | ✅ 有 |
| 50 | `snwWebuiUser` | `L3412:32` | — 仅 CSS/静态 |
| 51 | `snwWebuiToken` | `L3414:34` | ✅ 有 |
| 52 | `snwCopyTokenBtn` | `L3415:39` | — 仅 CSS/静态 |
| 53 | `snwSaveBtn` | `L3419:39` | — 仅 CSS/静态 |
| 54 | `snwPickExeBtn` | `L3420:37` | — 仅 CSS/静态 |
| 55 | `snwOpenWebuiBtn` | `L3421:37` | — 仅 CSS/静态 |
| 56 | `protoAutoCfg` | `L3424:32` | ✅ 有 |
| 57 | `protoAutoStatus` | `L3425:61` | ✅ 有 |
| 58 | `protoAutoToggle` | `L3426:149` | ✅ 有 |
| 59 | `protoAutoList` | `L3428:14` | ✅ 有 |
| 60 | `protoAutoApply` | `L3430:39` | ✅ 有 |
| 61 | `protoAutoClose` | `L3431:37` | — 仅 CSS/静态 |
| 62 | `protoStartBtn` | `L3438:39` | — 仅 CSS/静态 |
| 63 | `protoStopBtn` | `L3439:31` | — 仅 CSS/静态 |
| 64 | `protoAutoBtn` | `L3440:37` | — 仅 CSS/静态 |
| 65 | `protoQqPathBtn` | `L3441:37` | ✅ 有 |
| 66 | `protoPathMeta` | `L3442:30` | — 仅 CSS/静态 |
| 67 | `linkNapcat` | `L3446:32` | — 仅 CSS/静态 |
| 68 | `linkLlonebot` | `L3447:32` | — 仅 CSS/静态 |
| 69 | `linkSnowluma` | `L3448:32` | — 仅 CSS/静态 |
| 70 | `linkSnowlumaWeb` | `L3449:32` | — 仅 CSS/静态 |
| 71 | `protoLog` | `L3452:12` | ✅ 有 |

### 该页 JS 实际引用的 id（30 个，按字符范围归属本函数）

`lloApiUrl` `lloExePath` `lloReportUrl` `lloWebuiToken` `lloWebuiUrl` `lloWsPort` `lloWsUrl` `ncApiUrl` `ncExePath` `ncQqPath` `ncReportUrl` `ncWebuiToken` `ncWebuiUrl` `ncWsPort` `ncWsUrl` `pfbCancel` `pfbList` `pfbPath` `pfbSelect` `pfbUp` `protoAutoCfg` `protoAutoToggle` `protoQqPathBtn` `snwApiUrl` `snwExePath` `snwReportUrl` `snwWebuiToken` `snwWebuiUrl` `snwWsPort` `snwWsUrl` 

### 该页依赖的 data-* 属性

模板内出现：无

JS 读取：`data-dir`、`data-file`

### 该页模板内联 onclick / onerror

| 事件 | 位置 | 代码 | 调用的全局函数（**红线函数名**）|
|---|---|---|---|
| `onclick` | `L3446:82` | `protoOpen(` | `protoOpen()` |
| `onclick` | `L3447:91` | `protoOpen(` | `protoOpen()` |
| `onclick` | `L3448:94` | `protoOpen(` | `protoOpen()` |
| `onclick` | `L3449:87` | `protoOpen(` | `protoOpen()` |

> **红线**：这些函数名被写死在 HTML 属性里，必须在 `window` 全局作用域可见。本页用到：`protoOpen()`。

### 该页结构统计

| button | input | select | textarea | checkbox | .toggle | iframe | table |
|---|---|---|---|---|---|---|---|
| 26 | 20 | 0 | 0 | 1 | 0 | 0 | 0 |

### 该页 addEventListener 事件分布

`click`×3

### 该页 JS 选择器字符串（class / #id / [data-*]）

`#pfbCancel` `#pfbList` `#pfbPath` `#pfbSelect` `#pfbUp` `.pfb-row` `.pfb-row[data-dir]` `.pfb-row[data-file]` 

### 交互清单（迁移后验收清单）

> 逐条对照：每一项都要在重做后的页面上点一遍 / 填一遍，行为与旧版一致才算通过。

- [ ] 顶部 6 张状态卡：`#protoCards` 内 `#protoStatusValue` / `#protoDriverValue` / `#protoReadyValue`，第二组 `#protoBridgeValue` / `#protoApiValue` / `#protoWsValue`。
- [ ] `#protoCheckPanel`、`#protoRiskPanel`（风险面板，`renderProtoRiskPanel()` 渲染，内含内联 `onclick="protoApplySwitch('...')"` L707:37）。
- [ ] 三套驱动配置块：`#protoNapcatCfg`（`nc*` 前缀）、`#protoLlonebotCfg`（`llo*`）、`#protoSnowlumaCfg`（`snw*`），各自含 exe 路径 + 浏览 + 检测 + API/WS/上报 URL + 端口 + WebUI URL/token + 保存 + 选取按钮。
- [ ] NapCat：`#ncExePath` `#ncBrowseExe` `#ncDetectExe` `#ncQqPath` `#ncBrowseQq` `#ncDetectQq` `#ncApiUrl` `#ncReportUrl` `#ncWsUrl` `#ncWsPort` `#ncWebuiUrl` `#ncWebuiToken` `#ncCopyTokenBtn` `#ncOpenWebuiBtn` `#ncSaveBtn` `#ncDetectAll` `#ncPickExeBtn`。
- [ ] LLOneBot：`#lloExePath` `#lloBrowseExe` `#lloDetectExe` `#lloApiUrl` `#lloWsUrl` `#lloWsPort` `#lloReportUrl` `#lloWebuiUrl` `#lloWebuiToken` `#lloCopyTokenBtn` `#lloSaveBtn` `#lloPickExeBtn`。
- [ ] SnowLuma：`#snwExePath` `#snwBrowseExe` `#snwDetectExe` `#snwApiUrl` `#snwWsUrl` `#snwWsPort` `#snwReportUrl` `#snwWebuiUrl` `#snwWebuiUser` `#snwWebuiToken` `#snwCopyTokenBtn` `#snwSaveBtn` `#snwPickExeBtn` `#snwOpenWebuiBtn`。
- [ ] 自动检测：`#protoAutoCfg` `#protoAutoStatus` `#protoAutoToggle` `#protoAutoList` `#protoAutoApply` `#protoAutoClose`；`renderProtoLogs()` 每 **1200 ms** 轮询 `POST /protocol/logs {limit:200}` 写入 `#protoLog` 并 `scrollTop = scrollHeight`。
- [ ] 控制按钮：`#protoStartBtn`（`POST /protocol/start`）、`#protoStopBtn`（`/protocol/stop`）、`#protoAutoBtn`、`#protoQqPathBtn`（`/qq-path`）、`#protoPathMeta`。
- [ ] 4 个外链按钮（`#linkNapcat` `#linkLlonebot` `#linkSnowluma` `#linkSnowlumaWeb`）用内联 `onclick="protoOpen('<url>')"`（L3446–L3449）—— 重做时这 4 处内联属性必须保留（或同步改 `protoOpen` 的调用方式）。
- [ ] 文件浏览器弹窗由 `openProtoFileBrowser()`（L3632 一带）动态建到 `document.body`，内部 id：`#pfbCancel` `#pfbList` `#pfbPath` `#pfbSelect` `#pfbUp`；行是 `.pfb-row[data-dir]` / `.pfb-row[data-file]`，靠 `dataset.dir` / `dataset.file` 判断目录还是文件。
- [ ] `classifyPickedPath()` / `applyProtoPick()` / `pickFileFor()` / `runDetect()` 是 `mountProtocol` 内层函数，按 `nc*/llo*/snw*` 前缀归类。
- [ ] 页面切走/切回时 `window._monitorInterval` 与协议日志定时器都会 `clearInterval`（L807:5612 附近）—— 重做容器时若改坏 id，定时器会泄漏。
- [ ] `.style.display` 在 `mountProtocol` 内有 3 处读写（L3479:101 / 129 / 287）用于显示隐藏驱动配置块。

---

## 2.2 imagegen

**用途**：ComfyUI 生图控制：状态卡片、模型/主机/端口/超时/checkpoint、采样参数（尺寸/步数/CFG/种子/风格/NSFW/工作流/nodeId）、测试连接、保存配置、开始生图、结果区。

### 渲染函数与真实起止行号

| 项 | 值 |
|---|---|
| 挂载函数 | `mountImagegen(root)`  |
| 函数起止 | **`L839:1461` – `L839:13439`** |
| 函数体字符数 | **11,978** |
| 渲染写法 | `template` |
| 渲染区起止 | `L839:1509` – `L839:7592` |
| 渲染区字符数 | **6,084** |
| 直接在函数内定义的内层函数 | —（无） |

### 该页模板里的 id 清单（按出现顺序，26 个）

| # | id | 定义位置 | JS 是否引用 |
|---|---|---|---|
| 1 | `comfyStatus` | `L839:1649` | — 仅 CSS/静态 |
| 2 | `comfyState` | `L839:1776` | — 仅 CSS/静态 |
| 3 | `comfyHost` | `L839:1917` | — 仅 CSS/静态 |
| 4 | `comfyPort` | `L839:2063` | — 仅 CSS/静态 |
| 5 | `imgModel` | `L839:2415` | ✅ 有 |
| 6 | `imgLlmUrl` | `L839:2656` | ✅ 有 |
| 7 | `imgHost` | `L839:3103` | — 仅 CSS/静态 |
| 8 | `imgPort` | `L839:3293` | — 仅 CSS/静态 |
| 9 | `imgTimeout` | `L839:3495` | — 仅 CSS/静态 |
| 10 | `imgCkpt` | `L839:3772` | — 仅 CSS/静态 |
| 11 | `imgTestBtn` | `L839:3949` | ✅ 有 |
| 12 | `imgPrompt` | `L839:4389` | — 仅 CSS/静态 |
| 13 | `imgNeg` | `L839:4592` | — 仅 CSS/静态 |
| 14 | `imgWidth` | `L839:5021` | — 仅 CSS/静态 |
| 15 | `imgHeight` | `L839:5221` | — 仅 CSS/静态 |
| 16 | `imgSteps` | `L839:5487` | — 仅 CSS/静态 |
| 17 | `imgCfg` | `L839:5686` | — 仅 CSS/静态 |
| 18 | `imgSeed` | `L839:5883` | — 仅 CSS/静态 |
| 19 | `imgStyle` | `L839:6145` | — 仅 CSS/静态 |
| 20 | `imgNsfw` | `L839:6396` | — 仅 CSS/静态 |
| 21 | `imgWorkflow` | `L839:6711` | ✅ 有 |
| 22 | `imgBrowseBtn` | `L839:6889` | ✅ 有 |
| 23 | `imgNodeId` | `L839:7113` | — 仅 CSS/静态 |
| 24 | `imgSaveCfg` | `L839:7276` | ✅ 有 |
| 25 | `imgGenBtn` | `L839:7347` | ✅ 有 |
| 26 | `comfyResult` | `L839:7524` | ✅ 有 |

### 该页 JS 实际引用的 id（6 个，按字符范围归属本函数）

`comfyResult` `imgBrowseBtn` `imgGenBtn` `imgSaveCfg` `imgTestBtn` `imgWorkflow` 

### 该页依赖的 data-* 属性

模板内出现：无

JS 读取：无

### 该页模板内联 onclick / onerror

—（本页模板无内联 `on*=` 属性，全部走 `addEventListener` / `.on*=` / 事件委托）

### 该页结构统计

| button | input | select | textarea | checkbox | .toggle | iframe | table |
|---|---|---|---|---|---|---|---|
| 4 | 13 | 1 | 2 | 0 | 1 | 0 | 0 |

### 该页 addEventListener 事件分布

`click`×5

### 该页 JS 选择器字符串（class / #id / [data-*]）

—（本函数体内无 querySelector/closest/qsa 调用）

### 交互清单（迁移后验收清单）

> 逐条对照：每一项都要在重做后的页面上点一遍 / 填一遍，行为与旧版一致才算通过。

- [ ] 状态区：`#comfyStatus` 网格 + `#comfyState`（「未检测」）+ `#comfyHost` + `#comfyPort`。
- [ ] 模型与连接：`#imgModel`、`#imgLlmUrl`、`#imgHost`、`#imgPort`、`#imgTimeout`。
- [ ] 工作流：`#imgCkpt`、`#imgWorkflow`、`#imgBrowseBtn`（浏览工作流文件）、`#imgNodeId`。
- [ ] 参数：`#imgPrompt`、`#imgNeg`、`#imgWidth`、`#imgHeight`、`#imgSteps`、`#imgCfg`、`#imgSeed`、`#imgStyle`、`#imgNsfw`、`.toggle`。
- [ ] `#imgTestBtn` → `POST /imagegen/test`；`#imgSaveCfg` → `POST /imagegen/workflow`；`#imgGenBtn` → `POST /imagegen/generate`。
- [ ] `#comfyResult` 结果容器，生成后由 JS 写入。
- [ ] `qsa('#imgNsfw')`（L839:13349）按 id 当选择器用 —— 重做若把 `#imgNsfw` 换成 class 或改名，这行直接失效。
- [ ] `.toggle` 元素有全局点击处理（L829:750 `qsa('.toggle').forEach(...)` 切 `.on`），但**带 `dataset.plugin` 的 toggle 会被跳过**（插件卡片自己处理）。
- [ ] `mountImagegen` 内 `.onchange` 赋值 1 处（L839:10286），3 处 `e.target` 委托（L839:10325 / 10343 / 10479）—— 重做时表单元素的 `name`/`id` 变化会让委托取不到值。
- [ ] 生成按钮的文案有 1200 ms 的 `setTimeout` 复原（L839:11686）。

---

## 2.3 behavior

**用途**：群聊行为与触发词数值配置：12 个数值输入 + 保存；并含触发词组（**见 §4.1，触发器控件在 DOM 中不存在**）。

### 渲染函数与真实起止行号

| 项 | 值 |
|---|---|
| 挂载函数 | `mountBehavior(root)`  |
| 函数起止 | **`L2673:553` – `L2761:1`** |
| 函数体字符数 | **6,708** |
| 渲染写法 | `concat` |
| 渲染区起止 | `L2674:20` – `L2703:10` |
| 渲染区字符数 | **2,618** |
| 直接在函数内定义的内层函数 | —（无） |

### 该页模板里的 id 清单（按出现顺序，13 个）

| # | id | 定义位置 | JS 是否引用 |
|---|---|---|---|
| 1 | `bGroupSessionMaxReplies` | `L2678:110` | ✅ 有 |
| 2 | `bGroupIdleExitMinSilence` | `L2679:111` | ✅ 有 |
| 3 | `bGroupLastExitCooldown` | `L2682:108` | ✅ 有 |
| 4 | `bGroupIdleExitProbability` | `L2683:112` | ✅ 有 |
| 5 | `bPokeTimeout` | `L2686:109` | ✅ 有 |
| 6 | `bGroupMsgBufferWindow` | `L2687:111` | ✅ 有 |
| 7 | `bGroupRecentMsgsWindow` | `L2690:109` | ✅ 有 |
| 8 | `bGroupReplyMaxLength` | `L2691:107` | ✅ 有 |
| 9 | `bGroupSessionIdleTimeout` | `L2694:110` | ✅ 有 |
| 10 | `bPrivateReplyMaxLength` | `L2695:107` | ✅ 有 |
| 11 | `bAiPrivateMaxTokens` | `L2698:115` | ✅ 有 |
| 12 | `bPrivateReplyInterval` | `L2699:108` | ✅ 有 |
| 13 | `bSave` | `L2701:90` | ✅ 有 |

### 该页 JS 实际引用的 id（18 个，按字符范围归属本函数）

`bAiPrivateMaxTokens` `bGroupIdleExitMinSilence` `bGroupIdleExitProbability` `bGroupLastExitCooldown` `bGroupMsgBufferWindow` `bGroupRecentMsgsWindow` `bGroupReplyMaxLength` `bGroupSessionIdleTimeout` `bGroupSessionMaxReplies` `bPokeTimeout` `bPrivateReplyInterval` `bPrivateReplyMaxLength` `bSave` `bTriggerBotNick` `bTriggerQuote` `bTriggerSave` `bTriggerTextAt` `bTriggerWords` 

### 该页依赖的 data-* 属性

模板内出现：无

JS 读取：无

### 该页模板内联 onclick / onerror

—（本页模板无内联 `on*=` 属性，全部走 `addEventListener` / `.on*=` / 事件委托）

### 该页结构统计

| button | input | select | textarea | checkbox | .toggle | iframe | table |
|---|---|---|---|---|---|---|---|
| 1 | 12 | 0 | 0 | 0 | 0 | 0 | 0 |

### 该页 addEventListener 事件分布

`click`×2

### 该页 JS 选择器字符串（class / #id / [data-*]）

—（本函数体内无 querySelector/closest/qsa 调用）

### 交互清单（迁移后验收清单）

> 逐条对照：每一项都要在重做后的页面上点一遍 / 填一遍，行为与旧版一致才算通过。

- [ ] 12 个数值输入：`#bGroupSessionMaxReplies`、`#bGroupIdleExitMinSilence`、`#bGroupLastExitCooldown`、`#bGroupIdleExitProbability`、`#bPokeTimeout`、`#bGroupMsgBufferWindow`、`#bGroupRecentMsgsWindow`、`#bGroupReplyMaxLength`、`#bGroupSessionIdleTimeout`、`#bPrivateReplyMaxLength`、`#bAiPrivateMaxTokens`、`#bPrivateReplyInterval`。
- [ ] `#bSave` 按钮 → `POST /config` 提交上述数值（`document.getElementById(...).value` 逐个读，见 L2747–L2755 区段）。
- [ ] **触发词组（`#bTriggerWords` / `#bTriggerSave` / `#bTriggerTextAt` / `#bTriggerQuote` / `#bTriggerBotNick`）在 DOM 中完全不存在**：`set('bTriggerWords', ...)`（L2721）、`toggle('bTriggerTextAt', ...)`（L2724）、`toggle('bTriggerQuote', ...)`（L2725）以及保存时的 `document.getElementById('bTriggerWords').value`（L2749）、`.checked`（L2753–L2755）都指向不存在的元素。保存按钮（L2747）有 `if (bt)` 守卫，但 L2749–L2755 是**无守卫的直接取值**，一旦点保存就会抛 `TypeError` —— 见 §4.1。
- [ ] 重做时必须**先决定**：是照抄这个行为（保存报错），还是补上这 5 个控件。这属于「功能是否 100% 不变」的判定点，需要产品确认，不要自行修。
- [ ] `mountBehavior` 起止 `L2673:553 – L2761:1`，紧跟在 `mountCommands`（`L2673:1 – L2673:552`）之后同行的位置 —— 同一行两个函数，按行正则必串。

---

## 2.4 memes

**用途**：表情包管理：搜索、目录列表、标签云、情绪分类、分组状态、视图切换（网格/瀑布流）、导入（文件/目录/URL）、网格渲染、灯箱预览、删除/改标签、拖拽上传。

### 渲染函数与真实起止行号

| 项 | 值 |
|---|---|
| 挂载函数 | `mountMemes(root)`  |
| 函数起止 | **`L3082:1` – `L3343:1`** |
| 函数体字符数 | **15,035** |
| 渲染写法 | `concat` |
| 渲染区起止 | `L3083:20` – `L3113:10` |
| 渲染区字符数 | **1,727** |
| 直接在函数内定义的内层函数 | `refreshMemes()`、`_render()`、`_loadSidebar()`、`deleteMeme()`、`editTags()`、`bindGridActions()`、`openLightbox()`、`uploadFiles()` |

### 该页模板里的 id 清单（按出现顺序，13 个）

| # | id | 定义位置 | JS 是否引用 |
|---|---|---|---|
| 1 | `memeSearch` | `L3086:31` | ✅ 有 |
| 2 | `memeDirList` | `L3089:13` | ✅ 有 |
| 3 | `memeTagCloud` | `L3091:13` | ✅ 有 |
| 4 | `memeEmotions` | `L3093:13` | ✅ 有 |
| 5 | `memeGroupStatus` | `L3095:13` | ✅ 有 |
| 6 | `memeViewGrid` | `L3100:32` | ✅ 有 |
| 7 | `memeViewMasonry` | `L3101:32` | ✅ 有 |
| 8 | `memeImportFile` | `L3104:77` | ✅ 有 |
| 9 | `memeImportDir` | `L3105:32` | ✅ 有 |
| 10 | `memeImportUrl` | `L3106:32` | ✅ 有 |
| 11 | `memeCount` | `L3107:38` | ✅ 有 |
| 12 | `memeFilterStatus` | `L3110:13` | ✅ 有 |
| 13 | `memeGrid` | `L3111:13` | ✅ 有 |

### 该页 JS 实际引用的 id（13 个，按字符范围归属本函数）

`emptyImportBtn` `memeDirList` `memeEmotions` `memeFilterStatus` `memeGrid` `memeGroupStatus` `memeImportDir` `memeImportFile` `memeImportUrl` `memeSearch` `memeTagCloud` `memeViewGrid` `memeViewMasonry` 

### 该页依赖的 data-* 属性

模板内出现：无

JS 读取：`data-dir`、`data-emotion`、`data-lb-del`、`data-lb-tag`、`data-meme-del`、`data-meme-tag`、`data-memeDel`、`data-memeTag`、`data-name`、`data-tag`

### 该页模板内联 onclick / onerror

—（本页模板无内联 `on*=` 属性，全部走 `addEventListener` / `.on*=` / 事件委托）

### 该页结构统计

| button | input | select | textarea | checkbox | .toggle | iframe | table |
|---|---|---|---|---|---|---|---|
| 4 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |

### 该页 addEventListener 事件分布

`click`×14、`input`×1、`change`×1、`dragover`×1、`drop`×1、`keydown`×1

### 该页 JS 选择器字符串（class / #id / [data-*]）

`.emotion-tab` `.lightbox` `.lightbox-backdrop` `.meme-card` `.tag-pill` `[data-lb-close]` `[data-lb-del]` `[data-lb-tag]` `[data-meme-del]` `[data-meme-tag]` `button` `img` 

### 交互清单（迁移后验收清单）

> 逐条对照：每一项都要在重做后的页面上点一遍 / 填一遍，行为与旧版一致才算通过。

- [ ] 搜索：`#memeSearch`（`input` 事件，1 处 `addEventListener('input')`）。
- [ ] 侧栏：`#memeDirList`（目录）、`#memeTagCloud`（标签云，`.tag-pill`）、`#memeEmotions`（情绪分类，`.emotion-tab`，6 处选择器引用）、`#memeGroupStatus`。
- [ ] 视图切换：`#memeViewGrid`（网格）/ `#memeViewMasonry`（瀑布流）。
- [ ] 导入：`#memeImportFile`（文件）、`#memeImportDir`（目录）、`#memeImportUrl`（URL）、`#emptyImportBtn`（空态导入按钮）。
- [ ] 统计与筛选：`#memeCount`、`#memeFilterStatus`。
- [ ] 网格：`#memeGrid`；卡片 `.meme-card`，带 `[data-meme-del]` / `[data-meme-tag]`（对应 `dataset.memeDel` / `dataset.memeTag`）。
- [ ] 灯箱 `openLightbox()`：overlay 内 `.lightbox` + `.lightbox-backdrop`，控件 `[data-lb-close]` / `[data-lb-del]` / `[data-lb-tag]`；`closest('[data-lb-close]')`、`querySelector('.lightbox-backdrop')` 都在 L3284。
- [ ] 拖拽上传：`dragover` + `drop` 各 1 处（L3303 / L3306 / L3309 区段读 `e.target`）。
- [ ] 键盘：1 处 `keydown`（灯箱 Esc 关闭）。
- [ ] `bindGridActions()` 用 `e.target` 委托（L3271:13）处理卡片上的删除/改标签按钮 —— 重做卡片结构必须保留 `data-meme-del` / `data-meme-tag` 与 `button` / `img` 的层级（`closest('button')`、`querySelector('img')`）。
- [ ] `_render()` 空态用 `<div class="empty" style="grid-column:1/-1">` 且内嵌 emoji `📦`（L3148）—— `grid-column:1/-1` 依赖父容器是 CSS Grid。
- [ ] 接口：`GET /memes`、`GET /memes/tags`、`POST /memes/upload`、`POST /memes/delete`。

---

## 2.5 monitor

**用途**：事件通知 + 资源监测：通知开关（总开关/加好友/入群）、管理员 QQ 列表、保存/测试通知按钮；6 张资源卡片（CPU/MEM/BOT/API/MSG/MEM2）与趋势图容器。

### 渲染函数与真实起止行号

| 项 | 值 |
|---|---|
| 挂载函数 | `mountMonitor(root)`  |
| 函数起止 | **`L835:1504` – `L835:3237`** |
| 函数体字符数 | **1,733** |
| 渲染写法 | `template` |
| 渲染区起止 | `L835:1551` – `L835:3196` |
| 渲染区字符数 | **1,646** |
| 直接在函数内定义的内层函数 | —（无） |

### 该页模板里的 id 清单（按出现顺序，8 个）

| # | id | 定义位置 | JS 是否引用 |
|---|---|---|---|
| 1 | `sNotifyEnabled` | `L835:2002` | — 仅 CSS/静态 |
| 2 | `sNotifyFriendAdd` | `L835:2217` | — 仅 CSS/静态 |
| 3 | `sNotifyGroupInc` | `L835:2430` | — 仅 CSS/静态 |
| 4 | `sNotifyAdmins` | `L835:2672` | — 仅 CSS/静态 |
| 5 | `notifySaveBtn` | `L835:2818` | ✅ 有 |
| 6 | `notifyTestBtn` | `L835:2880` | ✅ 有 |
| 7 | `monitorCards` | `L835:2965` | ✅ 有 |
| 8 | `chartTrend` | `L835:3153` | — 仅 CSS/静态 |

### 该页 JS 实际引用的 id（0 个，按字符范围归属本函数）

—（本函数体内无直接 `$(...)` / `getElementById(...)` 调用；页面数据由全局 load* 函数按 id 写入，见下方 callout）

### 该页依赖的 data-* 属性

模板内出现：无

JS 读取：无

### 该页模板内联 onclick / onerror

—（本页模板无内联 `on*=` 属性，全部走 `addEventListener` / `.on*=` / 事件委托）

### 该页结构统计

| button | input | select | textarea | checkbox | .toggle | iframe | table |
|---|---|---|---|---|---|---|---|
| 2 | 3 | 0 | 1 | 3 | 3 | 0 | 0 |

### 该页 addEventListener 事件分布

—（本函数体内无 addEventListener）

### 该页 JS 选择器字符串（class / #id / [data-*]）

—（本函数体内无 querySelector/closest/qsa 调用）

### 交互清单（迁移后验收清单）

> 逐条对照：每一项都要在重做后的页面上点一遍 / 填一遍，行为与旧版一致才算通过。

- [ ] `#sNotifyEnabled`（总开关）、`#sNotifyFriendAdd`（加好友通知）、`#sNotifyGroupInc`（入群通知）三个 checkbox，`bindNotifyActions()`（L837）用 `el.checked` 读。
- [ ] `#sNotifyAdmins` textarea：管理员 QQ，按 `split('\n')` 每行一个，`trim` + 过滤空行后提交为 `notify_admin.admin_qqs`。
- [ ] `#notifySaveBtn` 点击 → `POST /config` 提交 `notify_admin{enabled,friend_add,group_increase,admin_qqs}` → `showToast` 成功/失败。
- [ ] `#notifyTestBtn` 点击 → `fetch('http://127.0.0.1:3005/api/admin/notify_test')`（**注意这是绝对地址，绕过 `/api` 前缀**），期间按钮 `disabled=true`，`finally` 恢复。
- [ ] `#monitorCards` 内的 `.card` 结构：`loadMonitor()`（L807）在容器**为空**时才用 `innerHTML` 注入 6 张卡（`data-m="CPU|MEM|BOT|API|MSG|MEM2"`，每张含 `.card-label` 与 `.card-value`）。
- [ ] 资源数据每 3000 ms 轮询 `GET /status`，由 `updateCards(data)` 按 `data-m` 映射写入 `.card-value`。
- [ ] `#chartTrend` 是一个 `<canvas>`，位于 `.chart-box`（高 320px）。**全文件没有任何 Chart.js 加载，`loadCharts()` 也从未被调用**（见 §4.2），因此该 canvas 恒为空 —— 重做时不要「顺手修好」，也不要以它为准。
- [ ] 通知设置由 `loadNotifySettings()`（L835）拉 `GET /config` 回填。

---

## 2.6 dashboard

**用途**：仪表盘：Bot 状态卡、协议状态卡、日志条数、实时日志流（带选中/复制），3 个内联 `onclick="router(...)"` 跳转按钮。

### 渲染函数与真实起止行号

| 项 | 值 |
|---|---|
| 挂载函数 | `mountDashboard(root)`  |
| 函数起止 | **`L829:7360` – `L830:147`** |
| 函数体字符数 | **1,785** |
| 渲染写法 | `template` |
| 渲染区起止 | `L829:7411` – `L829:8958` |
| 渲染区字符数 | **1,548** |
| 直接在函数内定义的内层函数 | —（无） |

### 该页模板里的 id 清单（按出现顺序，8 个）

| # | id | 定义位置 | JS 是否引用 |
|---|---|---|---|
| 1 | `dashBotStatus` | `L829:7493` | — 仅 CSS/静态 |
| 2 | `dashBotState` | `L829:7706` | ✅ 有 |
| 3 | `dashBotInfo` | `L829:7758` | ✅ 有 |
| 4 | `dashProtoStatus` | `L829:7825` | — 仅 CSS/静态 |
| 5 | `dashProtoState` | `L829:8060` | ✅ 有 |
| 6 | `dashProtoInfo` | `L829:8114` | ✅ 有 |
| 7 | `dashLogCount` | `L829:8393` | — 仅 CSS/静态 |
| 8 | `dashLog` | `L829:8728` | ✅ 有 |

### 该页 JS 实际引用的 id（0 个，按字符范围归属本函数）

—（本函数体内无直接 `$(...)` / `getElementById(...)` 调用；页面数据由全局 load* 函数按 id 写入，见下方 callout）

### 该页依赖的 data-* 属性

模板内出现：无

JS 读取：无

### 该页模板内联 onclick / onerror

| 事件 | 位置 | 代码 | 调用的全局函数（**红线函数名**）|
|---|---|---|---|
| `onclick` | `L829:7535` | `router(` | `router()` |
| `onclick` | `L829:7869` | `router(` | `router()` |
| `onclick` | `L829:8208` | `router(` | `router()` |

> **红线**：这些函数名被写死在 HTML 属性里，必须在 `window` 全局作用域可见。本页用到：`router()`。

### 该页结构统计

| button | input | select | textarea | checkbox | .toggle | iframe | table |
|---|---|---|---|---|---|---|---|
| 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### 该页 addEventListener 事件分布

—（本函数体内无 addEventListener）

### 该页 JS 选择器字符串（class / #id / [data-*]）

—（本函数体内无 querySelector/closest/qsa 调用）

### 交互清单（迁移后验收清单）

> 逐条对照：每一项都要在重做后的页面上点一遍 / 填一遍，行为与旧版一致才算通过。

- [ ] `#dashBotStatus` / `#dashBotState` / `#dashBotInfo` 三处 Bot 状态文本由 `loadDashStatus()`（L790）写入，重做后必须仍能被写入（元素存在且能接受 textContent）。
- [ ] `#dashProtoStatus` / `#dashProtoState` / `#dashProtoInfo` 三处协议状态文本同上。
- [ ] `#dashLogCount` 日志条数由 `updateDashboard(data)`（L807）写 `map` 写入；注意 `updateDashboard` 是按 `qsa('#dashCards .card')` + `card.dataset.id` 找值的 —— **`#dashCards` 在 DOM 中不存在**（见 §4.1），所以这条实际走的是 `dashboard` 模板里的 id 直写分支。
- [ ] `#dashLog` 实时日志流：每 3 秒 `loadDashLog()` 全量重建 200 条 `.log-row`（`maxSize` 500），必须保留滚动容器语义。
- [ ] 日志行点击可选中（切 `.selected` 与 `.log-check.on`），选中状态存 `window.__dashLogCheckedById[_key]`；`_key` 来自 `data-dash-key`。
- [ ] 向上滚动（离底 > `scrollThreshold`）会置 `window.__dashLogUserScrolledUp = true` 暂停自动滚底；滚回底部恢复。
- [ ] `injectDashCopyButton()` 会**动态往 `.panel-title` 里插入** `#dashCopyBtn`（L807），要求日志面板的标题元素带 `.panel-title` 类，且标题内需能放 `.settings-actions` 容器 —— 重做面板标题结构会直接导致「复制选中」按钮不出现。
- [ ] `#dashCopyBtn` 点击 → `getSelectedDashLogText()` 取选中的 `.log-row.selected` / `.log.selected` 文本 → 写剪贴板 → `showToast`。
- [ ] 3 个内联 `onclick="router('...')"` 跳转按钮（L829:7535 / L829:7869 / L829:8208），必须能跳到对应页面。
- [ ] 新日志行会做打字机逐字显示（`setInterval(..., 18)`，L798），并带 `.log-row-new` 类。

---

## 2.7 tokens

**用途**：Token 消耗统计：今日/本月/累计/估算费用 4 张卡、近 14 天柱状图、最近记录表格。

### 渲染函数与真实起止行号

| 项 | 值 |
|---|---|
| 挂载函数 | `mountTokens(root)` （`async`） |
| 函数起止 | **`L2763:1` – `L2812:1`** |
| 函数体字符数 | **3,779** |
| 渲染写法 | `concat` |
| 渲染区起止 | `L2764:20` – `L2777:10` |
| 渲染区字符数 | **1,251** |
| 直接在函数内定义的内层函数 | `fmt()`、`load()` |

### 该页模板里的 id 清单（按出现顺序，2 个）

| # | id | 定义位置 | JS 是否引用 |
|---|---|---|---|
| 1 | `tkBar` | `L2772:41` | ✅ 有 |
| 2 | `tkRecent` | `L2775:38` | ✅ 有 |

### 该页 JS 实际引用的 id（2 个，按字符范围归属本函数）

`tkBar` `tkRecent` 

### 该页依赖的 data-* 属性

模板内出现：无

JS 读取：无

### 该页模板内联 onclick / onerror

—（本页模板无内联 `on*=` 属性，全部走 `addEventListener` / `.on*=` / 事件委托）

### 该页结构统计

| button | input | select | textarea | checkbox | .toggle | iframe | table |
|---|---|---|---|---|---|---|---|
| 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### 该页 addEventListener 事件分布

—（本函数体内无 addEventListener）

### 该页 JS 选择器字符串（class / #id / [data-*]）

`#tkBar` `#tkRecent` `.grid-4 .panel .meta` 

### 交互清单（迁移后验收清单）

> 逐条对照：每一项都要在重做后的页面上点一遍 / 填一遍，行为与旧版一致才算通过。

- [ ] 4 张统计卡（今日 / 本月 / 累计 / 估算费用）：**没有 id，靠 `root.querySelectorAll('.grid-4 .panel .meta')[0..3]` 按顺序取**（L2784–L2787）—— 见 §4.5，这是最脆的结构依赖。
- [ ] `#tkBar` 近 14 天柱状图容器：`load()` 里 `bar.innerHTML = ''` 后用 `document.createElement('div')` 逐条追加，柱高用**内联 `style.height` + `style.cssText` 渐变背景**写死（L2794–L2799），`title` 提示为「日期 / 数量」。
- [ ] `#tkRecent` 最近记录：拼 `<table>` 字符串写入（L2803–L2808），列为 时间 / 场景 / tokens / 缓存命中 / 费用(元)。
- [ ] 数据源 `GET /token-stats`；`s.d7` / `s.m30` / `s.total` / `s.days[]` / `j.recent[]`。
- [ ] 金额硬编码 `total*0.5/1e6`（L2787）与 `_t*0.5/1e6`（L2806）。
- [ ] `mountTokens` 是 `async function`，且用**单引号字符串串联**渲染（`root.innerHTML = '...' + '...'`），模板内引号都是 `\'` 转义 —— 用正则扫 `id="..."` 会漏掉本页全部 2 个 id。

---

## 2.8 bot

**用途**：Bot 控制：状态/运行时长/PID 三张卡、启动/停止/选择 QQ 路径按钮、路径说明、运行日志。

### 渲染函数与真实起止行号

| 项 | 值 |
|---|---|
| 挂载函数 | `mountBot(root)`  |
| 函数起止 | **`L3350:1` – `L3350:2555`** |
| 函数体字符数 | **2,554** |
| 渲染写法 | `template` |
| 渲染区起止 | `L3350:44` – `L3350:1191` |
| 渲染区字符数 | **1,148** |
| 直接在函数内定义的内层函数 | —（无） |

### 该页模板里的 id 清单（按出现顺序，9 个）

| # | id | 定义位置 | JS 是否引用 |
|---|---|---|---|
| 1 | `botCards` | `L3350:178` | — 仅 CSS/静态 |
| 2 | `botStatusValue` | `L3350:312` | — 仅 CSS/静态 |
| 3 | `botUptimeValue` | `L3350:476` | — 仅 CSS/静态 |
| 4 | `botPidValue` | `L3350:636` | — 仅 CSS/静态 |
| 5 | `botStartBtn` | `L3350:773` | ✅ 有 |
| 6 | `botStopBtn` | `L3350:833` | ✅ 有 |
| 7 | `botQqPathBtn` | `L3350:898` | ✅ 有 |
| 8 | `botPathMeta` | `L3350:960` | ✅ 有 |
| 9 | `botLog` | `L3350:1014` | — 仅 CSS/静态 |

### 该页 JS 实际引用的 id（4 个，按字符范围归属本函数）

`botPathMeta` `botQqPathBtn` `botStartBtn` `botStopBtn` 

### 该页依赖的 data-* 属性

模板内出现：`data-bot`

JS 读取：`data-bot`

### 该页模板内联 onclick / onerror

—（本页模板无内联 `on*=` 属性，全部走 `addEventListener` / `.on*=` / 事件委托）

### 该页结构统计

| button | input | select | textarea | checkbox | .toggle | iframe | table |
|---|---|---|---|---|---|---|---|
| 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### 该页 addEventListener 事件分布

`click`×3

### 该页 JS 选择器字符串（class / #id / [data-*]）

—（本函数体内无 querySelector/closest/qsa 调用）

### 交互清单（迁移后验收清单）

> 逐条对照：每一项都要在重做后的页面上点一遍 / 填一遍，行为与旧版一致才算通过。

- [ ] `#botCards` 网格，内含 3 张卡：`data-bot="status|uptime|pid"`（`dataset.bot`）。
- [ ] `#botStatusValue` / `#botUptimeValue` / `#botPidValue` 三个值，由 `loadBotPage()`（L807）写 `GET /bot/status` 结果。
- [ ] `#botStartBtn` → `POST /bot/start`；`#botStopBtn` → `POST /bot/stop`；两者成功后都再调 `loadBotPage()`。
- [ ] `#botQqPathBtn` 选择 QQ 路径（`POST /qq-path`），`#botPathMeta` 显示「QQ: …」。
- [ ] `#botLog` 运行日志区，`loadBotPage()` 内定时刷新。
- [ ] 注意：`data-bot` 卡片的 `.card-value` 是 id 定位的（`#botStatusValue` 等），不是 `dataset` 映射 —— 与 monitor 页的 `.card-value` 映射写法不同，别混。

---

## 2.9 groupmanage

**用途**：联系人管理：刷新/自动监测开关/状态；群聊管理页签与好友管理页签；群列表、成员列表、好友列表；成员操作（设卡/管理员/禁言/解禁/踢人/备注）与卡片编辑。

### 渲染函数与真实起止行号

| 项 | 值 |
|---|---|
| 挂载函数 | `mountGroupManager(root)`  |
| 函数起止 | **`L2813:1` – `L3081:1`** |
| 函数体字符数 | **17,022** |
| 渲染写法 | `concat` |
| 渲染区起止 | `L2815:5` – `L2826:81` |
| 渲染区字符数 | **946** |
| 直接在函数内定义的内层函数 | `status()`、`roleBadge()`、`gmApi()`、`loadGroups()`、`bindCardActions()`、`openMembers()`、`close()`、`render()`、`doAction()`、`loadMembers()`、`loadFriends()`、`switchTab()` |

### 该页模板里的 id 清单（按出现顺序，10 个）

| # | id | 定义位置 | JS 是否引用 |
|---|---|---|---|
| 1 | `gmRefresh` | `L2816:36` | ✅ 有 |
| 2 | `gmAuto` | `L2817:48` | ✅ 有 |
| 3 | `gmStatus` | `L2818:27` | ✅ 有 |
| 4 | `gmTabGroup` | `L2821:34` | ✅ 有 |
| 5 | `gmTabFriend` | `L2822:34` | ✅ 有 |
| 6 | `gmTip` | `L2824:11` | ✅ 有 |
| 7 | `gmGroupTab` | `L2825:11` | ✅ 有 |
| 8 | `gmGroups` | `L2825:32` | ✅ 有 |
| 9 | `gmFriendTab` | `L2826:11` | ✅ 有 |
| 10 | `gmFriends` | `L2826:54` | ✅ 有 |

### 该页 JS 实际引用的 id（14 个，按字符范围归属本函数）

`gmAuto` `gmClose` `gmFriendTab` `gmFriends` `gmGroupTab` `gmGroups` `gmMemberList` `gmRefresh` `gmRefreshMembers` `gmSearch` `gmStatus` `gmTabFriend` `gmTabGroup` `gmTip` 

### 该页依赖的 data-* 属性

模板内出现：无

JS 读取：`data-act`、`data-gid`、`data-role`、`data-uid`

### 该页模板内联 onclick / onerror

—（本页模板无内联 `on*=` 属性，全部走 `addEventListener` / `.on*=` / 事件委托）

### 该页结构统计

| button | input | select | textarea | checkbox | .toggle | iframe | table |
|---|---|---|---|---|---|---|---|
| 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### 该页 addEventListener 事件分布

`click`×1、`input`×1

### 该页 JS 选择器字符串（class / #id / [data-*]）

`#gmClose` `#gmMemberList` `#gmRefreshMembers` `#gmSearch` `.card` `[data-act="cardinput"]` `[data-act="cardsave"]` `[data-act="reply"]` `[data-role="adminset"]` `[data-role="ban"]` `[data-role="bandur"]` `[data-role="cardin"]` `[data-role="kick"]` `[data-role="remarkin"]` `[data-role="setcard"]` `[data-role="unban"]` `[data-uid]` `button[data-act]` `button[data-role="remarkbtn"]` `input[data-act="reply"]` 

### 交互清单（迁移后验收清单）

> 逐条对照：每一项都要在重做后的页面上点一遍 / 填一遍，行为与旧版一致才算通过。

- [ ] 顶栏：`#gmRefresh`（刷新）、`#gmAuto`（自动监测：开/关）、`#gmStatus`（状态文本）、`#gmTip`（提示）。
- [ ] 页签：`#gmTabGroup`（群聊管理）/ `#gmTabFriend`（好友管理），切换由 `switchTab()` 读写 `.style.display`（L3057 / L3058）控制 `#gmGroupTab` / `#gmFriendTab` 显隐 —— **靠内联 display 判当前页签**（L3066:47 也读 display）。
- [ ] 列表容器：`#gmGroups`（群）、`#gmFriends`（好友）。
- [ ] 成员弹窗（动态建）：`#gmClose` `#gmMemberList` `#gmRefreshMembers` `#gmSearch`；`openMembers()`（L2916 一带）创建 overlay 到 `document.body`。
- [ ] 群卡片：`qsa('.card')` + `button[data-act]`；`dataset.act` 有 `cardinput` / `cardsave` / `reply`；有 `[data-act="reply"]` 输入框且用 `.onchange`。
- [ ] 成员操作全部走 `data-role` 属性选择器：`setcard` / `cardin` / `adminset` / `ban` / `bandur` / `unban` / `kick` / `remarkbtn`（按钮）+ `remarkin`（输入框）；每个操作都用 `closest('[data-uid]')` 上溯拿 `data-uid`。
- [ ] `dataset.uid` 是全项目引用最多的 dataset 之一（7 次），`data-uid` 在 JS 选择器里出现 5 次 —— 这是本页最强红线。
- [ ] 自动监测：`#gmAuto` 点击后 `setInterval(loadGroups, 15000)` 存入 `window.__gmTimer`；切换页签/离开时 `clearInterval(window.__gmTimer)`（L2827 / L3072 / L3073）。**注意 `mountGroupManager` 里有两处注册**（L3072:68 与 L3080:22），见 §4.3。
- [ ] `loadGroups()` 走 `gmApi()` → `http://127.0.0.1:3005` 的 `/api/groups`；`loadFriends()` 类似。这两个是**绝对地址**，与其它页的 `/api` 相对前缀不同。
- [ ] 群成员头像用 `https://q1.qlogo.cn/g?b=qq&nk=<qq>`（L2946），`<img onerror="this.style.opacity=.2">`（L2946:123）与好友列表同理（L3017:123）—— 这两处内联 `onerror` 也是红线。
- [ ] 删除/操作后会有 100 ms / 600 ms 的 `setTimeout` 动画复原（L3881 / L3898 一带）。

---

## 2.10 chatlogs

**用途**：日志控制：搜索框、级别下拉、清空、自动滚动开关、复制、统计、日志流（`.log` 行 + `.log-check` 多选）。

### 渲染函数与真实起止行号

| 项 | 值 |
|---|---|
| 挂载函数 | `mountChatlogs(root)`  |
| 函数起止 | **`L837:1425` – `L837:3826`** |
| 函数体字符数 | **2,401** |
| 渲染写法 | `template` |
| 渲染区起止 | `L837:1473` – `L837:2293` |
| 渲染区字符数 | **821** |
| 直接在函数内定义的内层函数 | —（无） |

### 该页模板里的 id 清单（按出现顺序，7 个）

| # | id | 定义位置 | JS 是否引用 |
|---|---|---|---|
| 1 | `logSearch` | `L837:1526` | — 仅 CSS/静态 |
| 2 | `logLevel` | `L837:1615` | — 仅 CSS/静态 |
| 3 | `logClear` | `L837:1808` | ✅ 有 |
| 4 | `logAuto` | `L837:1879` | ✅ 有 |
| 5 | `logCopy` | `L837:1933` | ✅ 有 |
| 6 | `logStats` | `L837:2029` | — 仅 CSS/静态 |
| 7 | `logStream` | `L837:2198` | ✅ 有 |

### 该页 JS 实际引用的 id（4 个，按字符范围归属本函数）

`logAuto` `logClear` `logCopy` `logStream` 

### 该页依赖的 data-* 属性

模板内出现：无

JS 读取：无

### 该页模板内联 onclick / onerror

—（本页模板无内联 `on*=` 属性，全部走 `addEventListener` / `.on*=` / 事件委托）

### 该页结构统计

| button | input | select | textarea | checkbox | .toggle | iframe | table |
|---|---|---|---|---|---|---|---|
| 3 | 1 | 1 | 0 | 0 | 0 | 0 | 0 |

### 该页 addEventListener 事件分布

`click`×3

### 该页 JS 选择器字符串（class / #id / [data-*]）

—（本函数体内无 querySelector/closest/qsa 调用）

### 交互清单（迁移后验收清单）

> 逐条对照：每一项都要在重做后的页面上点一遍 / 填一遍，行为与旧版一致才算通过。

- [ ] `#logSearch` 输入框：过滤日志文本（`__logApplyFilter`）。
- [ ] `#logLevel` 下拉：ALL / INFO / WARN / ERROR / DEBUG 级别过滤。
- [ ] `#logClear` 点击 → 清空（调 `/protocol/logs/clear`）。
- [ ] `#logAuto` 按钮文字「自动滚动：开/关」，切换 `__logAuto` 变量。
- [ ] `#logCopy` 复制选中日志（`injectChatlogCopyButton()` 也会动态插 `#chatlogCopyBtn`，L837）。
- [ ] `#logStats` 统计文本。
- [ ] `#logStream` 日志流容器；每 1000 ms `setInterval` 检查 `$('logStream')` 存在则 `loadLogs()`（L837:2422，`__chatlogAutoTimer`）。
- [ ] `router()` 离开 chatlogs 时会 `clearInterval(__chatlogAutoTimer)`（L3804:118）—— 重做时若把页面容器换成非 `$` 可取的结构，定时器不会停。
- [ ] 日志行结构：`.log` 行内含 `.log-time` / `.log-type` / `.log-check`，`data-key` 属性用于选中键；点击行或勾选 `.log-check` 都切换选中（`closest`/`e.target` 委托）。
- [ ] `loadLogs()` 拉 `POST /protocol/logs {limit:500}`，与已有日志按 `time|direction|text` 去重合并，最多留 500 条。

---

## 2.11 settings

**用途**：配置中心：搜索、保存、重载、测试四个按钮 + `#settingsNav` 导航 + `#settingsPanels` 面板容器（各子面板由 render*Panel 动态追加）。

### 渲染函数与真实起止行号

| 项 | 值 |
|---|---|
| 挂载函数 | `mountSettings(root)`  |
| 函数起止 | **`L841:271` – `L901:1`** |
| 函数体字符数 | **2,574** |
| 渲染写法 | `template` |
| 渲染区起止 | `L842:20` – `L858:3` |
| 渲染区字符数 | **712** |
| 直接在函数内定义的内层函数 | —（无） |

### 该页模板里的 id 清单（按出现顺序，6 个）

| # | id | 定义位置 | JS 是否引用 |
|---|---|---|---|
| 1 | `sSearch` | `L847:28` | ✅ 有 |
| 2 | `sSave` | `L849:37` | ✅ 有 |
| 3 | `sReload` | `L850:35` | ✅ 有 |
| 4 | `sTest` | `L851:35` | ✅ 有 |
| 5 | `settingsNav` | `L855:33` | ✅ 有 |
| 6 | `settingsPanels` | `L856:36` | ✅ 有 |

### 该页 JS 实际引用的 id（2 个，按字符范围归属本函数）

`settingsNav` `settingsPanels` 

### 该页依赖的 data-* 属性

模板内出现：无

JS 读取：`data-group`、`data-panel`

### 该页模板内联 onclick / onerror

—（本页模板无内联 `on*=` 属性，全部走 `addEventListener` / `.on*=` / 事件委托）

### 该页结构统计

| button | input | select | textarea | checkbox | .toggle | iframe | table |
|---|---|---|---|---|---|---|---|
| 3 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |

### 该页 addEventListener 事件分布

`click`×1

### 该页 JS 选择器字符串（class / #id / [data-*]）

`#settingsNav` `#settingsPanels` `.settings-nav-item` `[data-panel="` 

### 交互清单（迁移后验收清单）

> 逐条对照：每一项都要在重做后的页面上点一遍 / 填一遍，行为与旧版一致才算通过。

- [ ] 顶栏：`#sSearch`（搜索配置项）、`#sSave`（保存）、`#sReload`（重载）、`#sTest`（测试）。
- [ ] `#settingsNav` 左侧导航，`#settingsPanels` 右侧面板容器。
- [ ] 导航项 `.settings-nav-item` 点击切面板，靠 `[data-panel="<key>"]` 定位；`router`/`initSettingsSearch` 也用 `qsa('.settings-nav-item')`。
- [ ] 面板由 `renderXxxPanel(root)` 系列函数**追加**（有的用 `root.innerHTML +=`），因此 `#settingsPanels` 必须是可追加的普通容器。
- [ ] 配置脏检查：`window.__settingsSavedSnapshot = JSON.stringify(collectSettings())`，由 `markSettingsDirty()` 比对；保存后 1200 ms `setTimeout`（L1703）。
- [ ] `initSettingsSearch()`（L917 一带）大量读写 `.style.display` 做过滤显示/隐藏（L917 / L924 / L925 / L931 / L932）—— **靠内联 display 值判状态**，重做若改用 class 控制显隐会破坏搜索过滤。
- [ ] `bindSettingsEvents()`（L2167–L2247）有 8 处 `.style.display` 读写，同理。
- [ ] `mountEditableList(root, items, onChange)`（**L1407–L1530**）是共享组件，渲染 `.editable-list-row` / `.editable-list-check`（内含 `input[type=checkbox][data-idx]`）/ `.editable-list-text` / `.editable-list-del[data-idx]`；`render()` 里还会往 `root.parentNode.querySelector('.field-hint')` 写「当前共 N 句」。
- [ ] 四个可编辑列表实例：`#recallPreList`、`#recallPostList`、`#recallPrivateList`、`#exitIdleList`，各自的预览按钮（`#recallPrePreview` 等）用 `querySelectorAll('#xxxList .editable-list-text')` 取**文本**（不是 input.value）做预览 —— 所以 `.editable-list-text` 这个 class 名和「文本节点在 span 里」的结构是硬红线。
- [ ] `mountEditableList` 的删除按钮用动画：先加 `.removing`，300 ms `setTimeout` 后真正 splice + 重渲染（L3866 一带为同款交互）。
- [ ] 三个弹窗由全局函数生成到 `document.body`：`showConfirm(msg, cb)`（`#ccCancel`/`#ccConfirm`）、`showPrompt(...)`（`#spInput`/`#spCancel`/`#spConfirm`）、`openPreviewModal(...)`（`#pmClose`）、`openProviderModal(...)`、`openSetupWizard(...)`（`#wzProvider`/`#wzUrl`/`#wzModel`/`#wzMsg`/`#wzKey`/`#wzFetch`/`#wzCancel`/`#wzSave`）。
- [ ] Provider 卡片容器 id 是 `providerCards`，由 `renderProviderPanel()` 里 `bar.id = 'providerCards'` **动态赋值**（L940），不是 HTML 里写死的 —— 见 §4.4。
- [ ] 自定义下拉组件 `.egg-select-wrap` / `.egg-select-trigger` / `.egg-option`，靠 `.is-selected` / `.is-hover` 类做状态；`closest('.egg-select-wrap')`（L4271 / L4344）是它的委托入口；还会包装原生 `select`（L4341 `querySelectorAll('select')`）。

---

## 2.12 console

**用途**：底层控制：指令输出区 + 指令输入框 + 执行按钮（回车提交）。

### 渲染函数与真实起止行号

| 项 | 值 |
|---|---|
| 挂载函数 | `mountConsole(root)`  |
| 函数起止 | **`L839:1` – `L839:1460`** |
| 函数体字符数 | **1,459** |
| 渲染写法 | `template` |
| 渲染区起止 | `L839:48` – `L839:725` |
| 渲染区字符数 | **678** |
| 直接在函数内定义的内层函数 | —（无） |

### 该页模板里的 id 清单（按出现顺序，3 个）

| # | id | 定义位置 | JS 是否引用 |
|---|---|---|---|
| 1 | `consoleOutput` | `L839:263` | ✅ 有 |
| 2 | `consoleInput` | `L839:573` | ✅ 有 |
| 3 | `consoleSend` | `L839:673` | ✅ 有 |

### 该页 JS 实际引用的 id（3 个，按字符范围归属本函数）

`consoleInput` `consoleOutput` `consoleSend` 

### 该页依赖的 data-* 属性

模板内出现：无

JS 读取：无

### 该页模板内联 onclick / onerror

—（本页模板无内联 `on*=` 属性，全部走 `addEventListener` / `.on*=` / 事件委托）

### 该页结构统计

| button | input | select | textarea | checkbox | .toggle | iframe | table |
|---|---|---|---|---|---|---|---|
| 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |

### 该页 addEventListener 事件分布

`click`×1、`keydown`×1

### 该页 JS 选择器字符串（class / #id / [data-*]）

—（本函数体内无 querySelector/closest/qsa 调用）

### 交互清单（迁移后验收清单）

> 逐条对照：每一项都要在重做后的页面上点一遍 / 填一遍，行为与旧版一致才算通过。

- [ ] `#consoleInput` 输入指令，回车（`keydown` + `e.key === 'Enter'`）或点 `#consoleSend` 都会发送。
- [ ] 发送后 `#consoleOutput` 追加 `<div class="log in"><span class="log-time">IN </span>…</div>`。
- [ ] `POST /command {command}` 返回后追加 `<div class="log out">…OUT…</div>`，并把 `out.scrollTop = out.scrollHeight`。
- [ ] 发送后清空 `#consoleInput.value`。
- [ ] 输入内容经 `escapeHtml()` 转义后才拼进 innerHTML —— 重做时**必须保留 `escapeHtml`**。
- [ ] > 注意：`commands` 页（远程控制）与此页**功能几乎相同但是不同页面、不同 id**（`cmdInput`/`cmdSend`/`cmdOutput` vs `consoleInput`/`consoleSend`/`consoleOutput`）。两页共用一个 `e.target` 委托白名单（L592 的 `closest('#protoLog, #cmdOutput, #consoleOutput, .modal-overlay, #oboOverlay')`）。

---

## 2.13 memory

**用途**：记忆：卡片区、记忆列表、QQ 搜索框与搜索按钮、QQ 列表、记忆详情（含编码/解码按钮 —— **见 §4.1**）。

### 渲染函数与真实起止行号

| 项 | 值 |
|---|---|
| 挂载函数 | `mountMemory(root)`  |
| 函数起止 | **`L839:13440` – `L839:14510`** |
| 函数体字符数 | **1,070** |
| 渲染写法 | `template` |
| 渲染区起止 | `L839:13486` – `L839:14105` |
| 渲染区字符数 | **620** |
| 直接在函数内定义的内层函数 | —（无） |

### 该页模板里的 id 清单（按出现顺序，6 个）

| # | id | 定义位置 | JS 是否引用 |
|---|---|---|---|
| 1 | `memoryCards` | `L839:13516` | ✅ 有 |
| 2 | `memoryList` | `L839:13626` | ✅ 有 |
| 3 | `memoryQqSearch` | `L839:13842` | ✅ 有 |
| 4 | `memoryQqSearchBtn` | `L839:13940` | ✅ 有 |
| 5 | `memoryQqList` | `L839:13997` | ✅ 有 |
| 6 | `memoryDetail` | `L839:14032` | ✅ 有 |

### 该页 JS 实际引用的 id（2 个，按字符范围归属本函数）

`memoryQqSearch` `memoryQqSearchBtn` 

### 该页依赖的 data-* 属性

模板内出现：无

JS 读取：无

### 该页模板内联 onclick / onerror

—（本页模板无内联 `on*=` 属性，全部走 `addEventListener` / `.on*=` / 事件委托）

### 该页结构统计

| button | input | select | textarea | checkbox | .toggle | iframe | table |
|---|---|---|---|---|---|---|---|
| 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |

### 该页 addEventListener 事件分布

`click`×1、`keydown`×1

### 该页 JS 选择器字符串（class / #id / [data-*]）

—（本函数体内无 querySelector/closest/qsa 调用）

### 交互清单（迁移后验收清单）

> 逐条对照：每一项都要在重做后的页面上点一遍 / 填一遍，行为与旧版一致才算通过。

- [ ] `#memoryCards` 卡片区、`#memoryList` 记忆列表、`#memoryDetail` 详情容器、`#memoryDetailContent` 详情内容。
- [ ] `#memoryQqSearch` 输入框 + `#memoryQqSearchBtn` 按钮：点按钮或回车（`keydown`）触发搜索。
- [ ] `#memoryQqList` QQ 列表。
- [ ] `loadMemory()` / `openMemoryDetail()`（L807）按 `[data-qq]`（`qsa('[data-qq]')`，L807:13673）与 `dataset.qq` 取 QQ 号。
- [ ] `openMemoryDetail()` 用量 60 ms 的 `setTimeout(revealNext, 60)` 做逐字揭示动画；`.mem-stream` / `.mem-enc` / `.decoded` 三个 class 是这条链的钩子（`closest('.mem-enc')`、`classList.contains('mem-enc')`）。
- [ ] **`#memoryDecodeAll` / `#memoryEncodeAll` 被 `openMemoryDetail()` 引用但 DOM 中不存在**（见 §4.1）—— 重做时不要以为「补上这两个按钮」就能修好，要先确认原设计意图。
- [ ] 注意 `mountMemory` 起止是 `L839:13440 – L839:14510`，与 `mountImagegen`（`L839:1461 – L839:13439`）**在同一行紧邻**，按行正则必然串页。

---

## 2.14 commands

**用途**：远程控制：指令输入框 + 发送按钮 + 指令输出区。

### 渲染函数与真实起止行号

| 项 | 值 |
|---|---|
| 挂载函数 | `mountCommands(root)`  |
| 函数起止 | **`L2673:1` – `L2673:552`** |
| 函数体字符数 | **551** |
| 渲染写法 | `template` |
| 渲染区起止 | `L2673:49` – `L2673:533` |
| 渲染区字符数 | **485** |
| 直接在函数内定义的内层函数 | —（无） |

### 该页模板里的 id 清单（按出现顺序，3 个）

| # | id | 定义位置 | JS 是否引用 |
|---|---|---|---|
| 1 | `cmdInput` | `L2673:221` | ✅ 有 |
| 2 | `cmdSend` | `L2673:323` | ✅ 有 |
| 3 | `cmdOutput` | `L2673:370` | ✅ 有 |

### 该页 JS 实际引用的 id（0 个，按字符范围归属本函数）

—（本函数体内无直接 `$(...)` / `getElementById(...)` 调用；页面数据由全局 load* 函数按 id 写入，见下方 callout）

### 该页依赖的 data-* 属性

模板内出现：无

JS 读取：无

### 该页模板内联 onclick / onerror

—（本页模板无内联 `on*=` 属性，全部走 `addEventListener` / `.on*=` / 事件委托）

### 该页结构统计

| button | input | select | textarea | checkbox | .toggle | iframe | table |
|---|---|---|---|---|---|---|---|
| 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |

### 该页 addEventListener 事件分布

—（本函数体内无 addEventListener）

### 该页 JS 选择器字符串（class / #id / [data-*]）

—（本函数体内无 querySelector/closest/qsa 调用）

### 交互清单（迁移后验收清单）

> 逐条对照：每一项都要在重做后的页面上点一遍 / 填一遍，行为与旧版一致才算通过。

- [ ] `#cmdInput` 输入指令（placeholder「输入指令... /help」），`#cmdSend` 发送。
- [ ] `#cmdOutput` 输出区，由 `loadCommands()`（L807）写入，初始为 `<div class="log sys">输入 /help 查看可用指令</div>`。
- [ ] 本页大量逻辑落在全局 `loadCommands()` 里 —— 重做只改模板时，`#cmdInput` / `#cmdSend` / `#cmdOutput` 三个 id 一个都不能动。
- [ ] `#cmdOutput` 出现在全局委托白名单里（L592 的 `closest('#protoLog, #cmdOutput, #consoleOutput, .modal-overlay, #oboOverlay')`）—— 改 id 会连带影响全局拖拽/点击抑制逻辑。
- [ ] `mountCommands` 起止 `L2673:1 – L2673:552`；**同一行紧接 `mountBehavior`**（`L2673:553 – L2761:1`）。

---

## 2.15 plugins

**用途**：插件管理：标签栏、插件计数、刷新按钮、插件网格（卡片由 `loadPlugins()` 注入，含启用开关与「打开 UI」）。

### 渲染函数与真实起止行号

| 项 | 值 |
|---|---|
| 挂载函数 | `mountPlugins(root)`  |
| 函数起止 | **`L3694:1` – `L3694:362`** |
| 函数体字符数 | **361** |
| 渲染写法 | `template` |
| 渲染区起止 | `L3694:48` – `L3694:345` |
| 渲染区字符数 | **298** |
| 直接在函数内定义的内层函数 | —（无） |

### 该页模板里的 id 清单（按出现顺序，4 个）

| # | id | 定义位置 | JS 是否引用 |
|---|---|---|---|
| 1 | `pluginTagBar` | `L3694:118` | ✅ 有 |
| 2 | `pluginsCount` | `L3694:215` | ✅ 有 |
| 3 | `pluginsRefresh` | `L3694:260` | ✅ 有 |
| 4 | `pluginsGrid` | `L3694:322` | ✅ 有 |

### 该页 JS 实际引用的 id（0 个，按字符范围归属本函数）

—（本函数体内无直接 `$(...)` / `getElementById(...)` 调用；页面数据由全局 load* 函数按 id 写入，见下方 callout）

### 该页依赖的 data-* 属性

模板内出现：无

JS 读取：无

### 该页模板内联 onclick / onerror

—（本页模板无内联 `on*=` 属性，全部走 `addEventListener` / `.on*=` / 事件委托）

### 该页结构统计

| button | input | select | textarea | checkbox | .toggle | iframe | table |
|---|---|---|---|---|---|---|---|
| 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### 该页 addEventListener 事件分布

—（本函数体内无 addEventListener）

### 该页 JS 选择器字符串（class / #id / [data-*]）

—（本函数体内无 querySelector/closest/qsa 调用）

### 交互清单（迁移后验收清单）

> 逐条对照：每一项都要在重做后的页面上点一遍 / 填一遍，行为与旧版一致才算通过。

- [ ] `#pluginTagBar` 标签栏：`qsa('#pluginTagBar .tag-chip')`（L3719:7）绑定标签，点击设置 `window.__pluginActiveTag` 并重载。
- [ ] `#pluginsCount` 插件计数文本。
- [ ] `#pluginsRefresh` 刷新按钮 → `loadPlugins()`。
- [ ] `#pluginsGrid` 网格容器，`loadPlugins()`（**定义在 L3695，不是 L807 那个**）注入插件卡片。
- [ ] 插件卡片：`[data-plugin-toggle]` 启用开关（`dataset.pluginToggle`）、`[data-plugin-open]` 打开 UI（`dataset.pluginOpen`）、`[data-plugin-open-btn]`。
- [ ] 打开 UI 时先写 `window._lastPluginPage = match.dataset.page`，再由 `router('plugin_<name>')` 用 `#pluginFrame` iframe 加载 `/page?plugin_id=<name>`。
- [ ] `iframe#pluginFrame` 在**两处**被渲染：`mountPluginPage(root, page)`（L839:14822）与 `router()` 的 plugin_ 分支（L3804）—— 两处都写 `id="pluginFrame"`。
- [ ] **`pluginsGrid` / `pluginsRefresh` 各被定义 2 次**（L839 的 `mountPluginPage` 里与 L3694 的 `mountPlugins` 里）—— 见 §4.4。
- [ ] 插件页通过 `postMessage` 回传：`window.addEventListener('message')`（L3804）监听 `ego-back` 或 `eggbloom:navigate && page==='plugins'` 时回到 plugins 页。

---

# 第 3 部分：外部依赖与全局状态

## 3.1 外部资源

全文件**只有 3 个外部网络请求标签**，全部在 `<head>`，全部在 **L1**：

| # | 类型 | 位置 | 内容 | 备注 |
|---|---|---|---|---|
| 1 | `<link rel="preconnect">` | **L1:338** | `https://fonts.googleapis.com` | DNS/TLS 预连接 |
| 2 | `<link rel="preconnect" crossorigin>` | **L1:397** | `https://fonts.gstatic.com` | 同上 |
| 3 | `<link rel="stylesheet">` | **L1:465** | `https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Outfit:wght@300;400;500;600;700;800;900&display=swap` | **两个字体族** |
| — | `<link rel="icon">` | **L1:168** | `data:image/svg+xml,...⚡...` | 内联 SVG favicon，无网络请求 |

**字体是硬依赖**：`--font-d: Outfit, 'Noto Sans SC', 'PingFang SC', system-ui, sans-serif` 与
`--font-m: JetBrains Mono, Consolas, monospace`（定义在 `<style>` 的 `:root`，L1）。
JS 生成的 HTML 里有 5 处内联样式直接写 `var(--font-m)`（见 §3.4），
所以 `--font-m` 变量名本身也是红线。

### ⚠️ 关于 `chart.min.js`

**任务描述里提到的 `chart.min.js` 在本文件中不存在。** 全文件没有任何 `<script src=...>` 标签。
但代码里确实有 Chart.js 的调用与 `typeof Chart === 'undefined'` 守卫 —— 详见 §4.2。

### 其余绝对地址（不是「资源」，是 API 端点，但同样是硬编码红线）

| 位置 | 地址 | 用途 |
|---|---|---|
| L837:1109 | `http://127.0.0.1:3005/api/admin/notify_test` | monitor 页「测试通知」（**裸 fetch，不走 `API()`**）|
| L839:2684 | `http://127.0.0.1:8081/v1/chat/completions` | imagegen 本地 LLM 探测 |
| L1005:54 | `https://api.deepseek.com/chat/completions` | 默认远端 API 地址 |
| L2238:30 | `http://127.0.0.1:` | 远端模型探测前缀 |
| L2836:19 | `http://127.0.0.1:3005` | groupmanage 的 `gmApi()` 基址 |
| L2946:24 | `https://q1.qlogo.cn/g?b=qq&nk=` | 群成员头像（QQ 头像 CDN）|
| L3363:906 | `http://127.0.0.1:3100` | 协议端探测 |
| L3379:69 | `http://127.0.0.1:3000/onebot/v11` | OneBot v11 默认 API |
| L3405:69 | `http://127.0.0.1:3000` | 协议端默认 API |
| L3519 / L3526 / L3527 | `:5099` / `:6099` / `:7000` | 各驱动 WebUI 默认端口 |
| L4001:106 | `https://api.deepseek.com` | 引导向导默认地址 |

外链按钮（docs 链接，内联 `onclick="protoOpen('...')"`）：`https://napneko.github.io/`（L3446）、
`https://llonebot.github.io/zh-CN/`（L3447）、`https://github.com/SnowLuma/SnowLuma`（L3448）、
`https://snowluma.github.io`（L3449）。

### 接口前缀

`const API = (p, opt = {}) => fetch('/api' + p, opt)...`（**L662:1**）。
所有 `API('/x')` 实际请求 `/api/x`。全文件共 **51 个不同端点**，调用最多的：
`/config`×12、`/protocol/config`×7、`/protocol/logs`×4、`/protocol/check`×3、`/protocol/status`×3、`/qq-path`×3。

## 3.2 全局轮询 / 定时器（迁移时一个都不能丢）

### A. 长周期业务轮询（**关键，必须保留**）

| 周期 | 刷新内容 | 注册位置 | 所在函数 | 清理 |
|---|---|---|---|---|
| **5000 ms** | `poll()` → `loadDashStatus()` + `loadDashLog()`（仪表盘状态与日志）| `L829:6345` | `boot()` | 无（`pollTimer` 变量持有，从未 clear）|
| **3000 ms** | `loadDashLog()` 仪表盘日志 | `L830:116` | `mountDashboard()` | `clearInterval(window._dashLogInterval)` @ `L830:49` |
| **3000 ms** | `updateCards()` → `GET /status`，写 `#monitorCards .card-value` | `L807:5682` | `loadMonitor()` | `clearInterval(window._monitorInterval)` @ `L807:5612` |
| **1200 ms** | `renderProtoLogs()` → `POST /protocol/logs {limit:200}` → `#protoLog` | `L829:711` | `loadProtocolPage()` | 无显式 clear（依赖元素存在性判断）|
| **15000 ms** | `loadGroups()` 群列表自动刷新 | `L3072:68` | `mountGroupManager()`（`#gmAuto` 点击分支）| `clearInterval(window.__gmTimer)` @ `L2827:26` / `L3072:16` / `L3073:12` |
| **15000 ms** | `loadGroups()` 群列表自动刷新（**第二处注册**）| `L3080:22` | `mountGroupManager()` 末尾 | 同上 |
| **1000 ms** | `if ($('logStream')) loadLogs()` 日志流刷新 | `L837:2422` | `mountChatlogs()` | `clearInterval(__chatlogAutoTimer)` @ `L837:2336`；`router()` 离开时清 @ `L3804:118` |

### B. 短周期动画定时器（视觉，可随重做调整，但会改变观感）

| 周期 | 内容 | 位置 |
|---|---|---|
| 80 ms | `updateBackground()` + `updateCode()` 启动页字符雨 | `L829:4403`（`clearInterval(bgInterval)` @ `L829:4593`）|
| 70 ms × 28 步 | `boot()` 标题 `EggBloom` 逐字解码 | `L829:5682`（`clearInterval(interval)` @ `L829:6178`）|
| 60 ms | `revealNext` 记忆详情逐字揭示 | `L807:16332` |
| 400 ms | `revealNext` 启动页揭示节拍 | `L829:4352` |
| 18 ms | 仪表盘新日志行打字机 | `L798:1407`（`clearInterval(typeInterval)` @ `L798:1513`）|
| 2600 ms（一次性）| `showToast` 自动移除 | `L788:285` |
| 300 ms | `removeListItem` 删除动画后 splice | `L3866:3` |
| 600 ms | 按钮涟漪移除 | `L3898:5` |
| 100 ms | 按钮缩放复原 | `L3881:5` |
| 30 ms | 面板滚动 `scrollIntoView` | `L4149:19` |
| 900 ms（一次性）| 首次进入自动弹引导 | `L4080:23` |
| 1200 ms（一次性）| 配置保存快照 / 生图按钮文案复原 | `L1703:5` / `L839:11686` |

> **风险**：这些定时器都以「元素是否存在」为守卫（`if (!el) return;`）。
> 重做时只要 id 名字或层级变了，定时器仍会跑但静默失效 —— **不会有报错，只会数据不更新**。
> 建议迁移后用 §2 的交互清单逐页确认「数值真的在动」。

## 3.3 页面切换机制（路由）

### 路由入口有 3 个

| # | 位置 | 触发 |
|---|---|---|
| 1 | **L3805**（脚本顶层，解析时立即执行）| `router(location.hash.replace('#','') || 'dashboard')` —— **hash 路由**，URL 形如 `#protocol` |
| 2 | **L3806** | `qsa('#sideRail .side-rail-item').forEach(n => n.addEventListener('click', () => router(n.dataset.page)))` —— 左侧导航点击，**靠 `dataset.page`** |
| 3 | `boot()` 内 **L829:6330** 附近 | `router('dashboard')` —— 启动时强制回仪表盘 |

额外入口：`window.addEventListener('message')`（**L3805**）监听 iframe 回传，
收到 `{type:'ego-back'}` 或 `{type:'eggbloom:navigate', page:'plugins'}` 时 `router('plugins')`。

### `router(page)` 的完整行为（定义在 **L3804**）

1. `if (__chatlogAutoTimer && page !== 'chatlogs') clearInterval(__chatlogAutoTimer)` —— 离开日志页停轮询。
2. `currentPage = page`。
3. 从 `pageTitles[page]` 取 `[title, sub]`；写入 **`#pageTitle`.textContent** 与 **`#pageSub`.textContent**。
4. `qsa('#sideRail .side-rail-item')` 逐个 `classList.toggle('ac', n.dataset.page === page)` —— **高亮类名是 `.ac` 不是 `.active`**（`.active` 另有用途）。
5. `container = $('page-content')`；`container.innerHTML = ''`；`container.dataset.page = page`。
6. 若 `page.startsWith('plugin_')`：从 `window._lastPluginPage` 或 `document.querySelectorAll('[data-plugin-open]')` 里找 `dataset.pluginOpen === name` 的元素，取其 `dataset.page` 作为 iframe 地址。
7. `container.classList.remove('page-enter'); void container.offsetWidth; container.classList.add('page-enter')` —— 强制重排以重放进入动画（**`page-enter` 类必须有动画定义**）。
8. `try { mount(container, pluginPageUrl || page) } catch(e){ console.error('Mount error:', e) }` —— **挂载异常被吞掉**，只进控制台。

> **挂载机制红线**：`pageMounts[page]` 拿到的函数会被调用为 `mount(container, page)`，
> 即**每个 `mountXxx(root)` 的 `root` 就是 `#page-content` 本身**，页面内容直接覆写它的 `innerHTML`。
> 因此：① `#page-content` 这个 id 不能改名；② `mountXxx` 必须接受 `(root)` 参数；
> ③ 页面内不要依赖 `#page-content` 之外的祖先结构（除了 `#pageTitle` / `#pageSub` / `#sideRail`）。

> **注意**：`const pageCache = {}`（L3804 前）**定义了但从未使用** —— 没有页面缓存，每次切页都重新挂载并重跑 `load*()`。

### 导航壳（shell HTML，静态写在 L506–L574）

| id / 选择器 | 作用 | 迁移要求 |
|---|---|---|
| `#sideRail` | 左侧导航 `<nav>` | **改名会断路由**；CSS 里有 7 条 `#sideRail` 规则 |
| `#sideRail .side-rail-item`（15 个）| 导航项，各带 `data-page="<page key>"` | `data-page` 值与 `pageMounts` 的 key 必须一致 |
| `.side-rail-item .ac` | 当前页高亮类（JS 切换）| 必须保留 `.ac` 规则 |
| `.shuffle-text`（带 `data-shuffle`）| 导航文字动效 | 纯视觉 |
| `#content` | 内容区 `<main>` | CSS 有 `.content` 规则 |
| `#pageTitle` / `#pageSub` | 页标题 / 副标题 | **`router()` 直接写 textContent，改名即抛错** |
| `#page-content` | 页面挂载容器 | **核心红线** |
| `#statusDot` / `#statusText` / `#uptimePill` / `#clock` | 顶栏状态胶囊 | 全局状态更新目标 |
| `#modelQuickSelect` / `#modelQuickPill` | 顶栏快速切模型 | `initModelQuickSelect()`（L1932）读取并填充 |
| `#oboPreviewBtn` | 重新查看新手引导 | `oboBind()` 绑定点击 |
| `#appTitlebar` / `#tbMin` / `#tbMax` / `#tbClose` | 自定义标题栏窗口按钮 | 桌面壳交互 |
| `#decodeSplash` / `#decodeText` / `#splashSub` / `#splashBarFill` | 启动解码动画 | `boot()` 读写；**`#decodeGrid` 不存在**（§4.1）|
| `#oboOverlay` / `#oboKicker` / `#oboTitle` / `#oboSub` / `#oboProg` / `#oboBody` / `#oboPrev` / `#oboNext` / `#oboSkiplnk` | 新手引导遮罩 | `oboRender()` 动态写 body |
| `#bloomCanvas` | **CSS 里有规则（L1）但 HTML 里不存在该元素** | 死 CSS，可删 |

## 3.4 CSS 变量契约

`:root` 定义的变量被 **JS 拼进内联样式**（JS 侧出现 1 处 `var(--x)`），因此**变量名也是红线**：

| 变量 | JS 内联样式里出现次数 |
|---|---|
| `var(--ink3)` | 37 |
| `var(--gold)` | 16 |
| `var(--ink2)` | 11 |
| `var(--ink)` | 9 |
| `var(--font-m)` | 5 |
| `var(--ink5)` | 5 |
| `var(--ink4)` | 4 |
| `var(--crimson)` | 2 |
| `var(--bg)` | 2 |

> 重做 CSS 时若把 `--ink3` 改名成 `--text-muted` 之类，**JS 生成的 HTML 会静默变成无颜色**（`var()` 未定义不报错）。
> 全部 14 个 `:root` 变量（L1）：`--bg --surface --paper --elevated --input --ink --ink2 --ink3 --ink4 --ink5 --gold --gold-dim --slate --crimson --warm --font-d --font-m --base-mono --shadow-sm --shadow-md --shadow-lg --shadow-gold --ease --ease-spring`。

## 3.5 全局状态变量（跨页面共享，重做时不要「顺手清理」）

| 变量 | 位置 | 作用 |
|---|---|---|
| `window.__dashLogAll` | L792 / L796 | 仪表盘全量日志缓存 |
| `window.__dashLogCheckedById` | L796 | 仪表盘日志选中态（按 `_key`）|
| `window.__dashLogUserScrolledUp` | L796 / L798 | 用户是否上滚（暂停自动滚底）|
| `window.__dashLogHealthOk` | L798 | 日志健康标记 |
| `window.__dashLogScrollBinded` | L796 / L798 | 滚动监听只绑一次 |
| `window.__logAll` / `__logFiltered` / `__logAuto` / `__logSelectedKeys` | L835 / L837 | 日志页状态 |
| `window._monitorInterval` | L807 | monitor 轮询句柄 |
| `window._dashLogInterval` | L830 | dashboard 轮询句柄 |
| `window.__gmTimer` | L2827 / L3072 / L3080 | 群管理轮询句柄 |
| `window.__logSelectedKeys` | L835 | 日志页多选 |
| `window.__settingsSavedSnapshot` | L1703 / L2084 / L1928 | 配置脏检查快照 |
| `window.__pluginActiveTag` | L3719 区段 | 插件页当前标签 |
| `window._lastPluginPage` | L807 / L3804 | 插件 iframe 地址 |
| `window._protoCurDriver` | L707 区段 | 当前协议驱动 |
| `window._refreshProviderCards` | L974 | provider 卡片重绘钩子 |
| `window.__restartDecodeSplash` | L829 | 启动动画重放（被赋为空函数）|
| `obo` / `OBO_KEY` / `OBO_PAGES` / `OBO_STEPS` | L3900+ | 新手引导状态 |

---

# 第 4 部分：迁移风险点（每条都带行号）

## 4.1 【最高危】被 JS 引用但在 DOM 中完全不存在 —— 9 个 id

这 9 个 id 只有引用、**全文件找不到任何 `id="..."` 定义**（含模板拼接、`createElement` + `el.id=`）。
它们是**已存在的功能缺口**。重做时若「顺手补上」，功能就变了；若照抄，则要保持同样的降级行为。
**这是判定「功能 100% 不变」时最需要产品确认的清单。**

| id | 引用位置 | 引用函数 | 有无空值守卫 | 后果 |
|---|---|---|---|---|
| `bTriggerWords` | L2721（`set('bTriggerWords', ...)`）、L2749（`.value`）| `mountBehavior` | L2721 有；**L2749 无守卫** | 点「保存」时若走到 L2749 → `TypeError: Cannot read properties of null` |
| `bTriggerSave` | L2747 | `mountBehavior` | **有**（`if (bt)`）| 该按钮永不出现，无报错 |
| `bTriggerTextAt` | L2724、L2754（`.checked`）| `mountBehavior` | L2724 有；**L2754 无守卫** | 同上，保存抛错 |
| `bTriggerQuote` | L2725、L2755（`.checked`）| `mountBehavior` | L2725 有；**L2755 无守卫** | 同上，保存抛错 |
| `bTriggerBotNick` | L2753（`.checked`）| `mountBehavior` | **无** | 同上，保存抛错 |
| `decodeGrid` | L829:1133 | 启动页 IIFE | **有**（`if (!el \|\| !grid) return;`）| 整个字符雨背景动画直接 return，**从不执行** |
| `galleryGrid` | L807:10739、L807:10918 | `loadGallery()` | 未确认 | 画廊功能依赖的容器缺失，需运行期确认 |
| `memoryDecodeAll` | L807:17453 | `openMemoryDetail()` | 未确认 | 记忆详情「全部解码」按钮缺失 |
| `memoryEncodeAll` | L807:17552 | `openMemoryDetail()` | 未确认 | 记忆详情「全部编码」按钮缺失 |

> 另外两个「引用但不存在」的**选择器**（容器级）：
> - `qsa('#dashCards .card')`（**L807:1936**）与 `#dashCards`：dashboard 模板里没有 `#dashCards`，
>   所以 `updateDashboard(data)` 的映射分支永远容器为空、什么也不写。这是**静默失效**（无报错、无效果）。
> - `qsa('#monitorCards .card')` 的 `#monitorCards` 存在，但 monitor 模板里那张卡容器是空 `<div id="monitorCards">`，
>   卡片由 `loadMonitor()` 在**容器为空**时才注入 —— 重做时若预先把卡片写进 HTML，`loadMonitor()` 会因 `.card` 已存在而跳过注入，
>   此时**必须**保留 `data-m="CPU|MEM|BOT|API|MSG|MEM2"` 属性，否则 `updateCards()` 的 `qsa('#monitorCards .card')` 映射取不到 `dataset.m`。

## 4.2 【最高危】Chart.js 被引用但从未加载 —— 图表功能实际是死的

| 事实 | 位置 |
|---|---|
| `makeChart(id, type, labels, datasets)` 第一行守卫：`if (!ctx \|\| typeof Chart === 'undefined') return;` | **L807:2149** |
| `charts[id] = new Chart(ctx, {...})` | **L807:2310** |
| `loadCharts()` 定义，调用 `makeChart('msg'…)` / `makeChart('ai'…)` / `makeChart('img'…)` | **L807:2991–L807:3540** |
| `loadCharts()` 的**调用点：0 个**（全文件搜索无第二处） | — |
| `#chart-msg` / `#chart-ai` / `#chart-img` 三个 canvas 的存在性 | **全文件 0 处** |
| `loadCharts()` 用到的 `pad()` 辅助函数 | 未确认 |
| monitor 页的 `#chartTrend` canvas（**L835:3200 附近**，位于 `.chart-box` 内）| 存在，但**没有任何 JS 引用它**（`charts.trend` 只在 `updateMonitor` 里被读，而 `charts` 从未被赋 `trend` 键）|

**结论**：本 SPA 的图表能力**完全未生效** —— 库没加载、`loadCharts()` 没被调用、canvas 不存在。
`updateMonitor()`（L807:7024 与 L807:5840 附近有**两个同名定义**，后者覆盖前者）里的 `charts.trend` 分支恒为 false。

> 迁移决策：如果目标是「功能 100% 不变」，**就不要引入 Chart.js**，保持 canvas 空着；
> 如果目标是「顺便修好图表」，那是**功能变更**，必须单独立项并补齐 `<script src="chart.min.js">`、三个 canvas 与 `loadCharts()` 调用。
> 报告此点是为了避免迁移方误以为「图表本来就该有」而擅自加载库。

## 4.3 【高危】同一 `id` 被多个页面 / 多处共用

### A. 同一 id 在多个页面的模板里被定义（跨页冲突）

- **`chatlogCopyBtn`** — `L837:4649`(js-template)· `L837:4648`(js-assigned)
- **`dashCopyBtn`** — `L806:294`(js-template)· `L807:552`(js-template)· `L806:293`(js-assigned)· `L807:551`(js-assigned)
- **`pluginFrame`** — `L841:92`(js-template)· `L3804:1325`(js-template)
- **`pluginsGrid`** — `L839:14778`(js-template)· `L3694:322`(js-template)
- **`pluginsRefresh`** — `L839:14708`(js-template)· `L3694:260`(js-template)
- **`providerCards`** — `L940:7`(js-template)· `L940:6`(js-assigned)

逐个说明：

- **`dashCopyBtn`**（L806:294 / L807:552 静态模板；L806:293 / L807:551 `el.id=` 赋值）：
  `loadDashLog()` 与 `injectDashCopyButton()` **两个函数各自都会创建这个按钮**，靠 `if (document.getElementById('dashCopyBtn')) return;` 去重。
  重做时若把日志面板标题结构改掉，两个创建点都可能落空或重复插入。
- **`chatlogCopyBtn`**（L837:4649 模板 + L837:4648 赋值）：同上，`injectChatlogCopyButton()` 内部。
- **`pluginsRefresh` / `pluginsGrid`**：**在 L839（`mountPluginPage`）与 L3694（`mountPlugins`）各定义一次**。
  这是「插件详情页」与「插件列表页」两套模板复用了同一批 id —— 因为同一时刻只渲染一个，运行时不会冲突，
  但**重做时若把两个页面放进同一个容器（或做页面缓存）就会撞 id**。另外 `pageCache` 变量虽定义但未使用，暂时安全。

### B. 同一函数名被定义两次（后者覆盖前者）

| 函数 | 定义 1 | 定义 2 | 生效的 | 风险 |
|---|---|---|---|---|
| `loadPlugins` | **L807:18693** | **L3695** | L3695（后者覆盖）| 两个实现逻辑不同。改 L807 那个**完全无效**；必须改 L3695 那个 |
| `updateMonitor` | **L807:5840** | **L807:7024** | L807:7024 | 同上，前一个死代码 |
| `mountPlugins` 的渲染与 `mountPluginPage` | L3694 / L839 | — | — | 两处都写 `#pluginsGrid` |
| `getSelectedDashLogText` | **L806** | **L807** | L807 | 前一个死代码 |

> **迁移必做**：用 `function <name>` 全文件查重，确认你改的那个是生效版本。

### C. 被多个页面的 JS 共同引用的 id（改一处影响多页）

| id | 引用它的页面/函数 | 位置 |
|---|---|---|
| `#page-content` | `router()`（所有页面）| L3804 |
| `#pageTitle` / `#pageSub` | `router()`（所有页面）| L3804 |
| `#sideRail` | `router()` 高亮 + 导航点击绑定 | L3804 / L3806 |
| `#cmdOutput` / `#consoleOutput` / `#protoLog` | 全局点击抑制白名单 `closest('#protoLog, #cmdOutput, #consoleOutput, .modal-overlay, #oboOverlay')` | **L592:14** |

> **L592** 这一条特别隐蔽：它是一个全局 `document` 级监听里的白名单，
> 只要 `commands` / `console` / `protocol` 三页中任意一个的输出区 id 改名，
> 该区域内的点击行为就会改变（原来是「不处理」，改后会被全局逻辑接管）。

## 4.4 【高危】靠「兄弟节点 / 父节点 / 顺序」定位，而不是靠 id

共 **16 处**结构型定位（`.parentElement` 7、`.parentNode` 4、`.firstChild` 3、`.lastChild` 2）。
**重做容器层级时这些会静默失效。**

| 类型 | 位置 | 代码意图 |
|---|---|---|
| `.parentElement` | **L806:566** | `el.parentElement.querySelector('.panel-title')` 找面板标题插复制按钮 |
| `.parentElement` | **L807:1138** | 同上（`injectDashCopyButton` 内）|
| `.parentElement` | **L1437 区段** / L1464 | 弹窗/文件选择器插入点 |
| `.parentElement` | **L1411 区段**（`mountEditableList` 内）| `root.parentNode.querySelector('.field-hint')` 写「当前共 N 句」 |
| `.firstChild` | **L806:645 / L807:1218** | `actions.insertBefore(copyBtn, actions.firstChild)` |
| `.lastChild` | **L798 区段** | 仪表盘打字机：`var textNode = row.lastChild;` —— **假设日志文本是行的最后一个子节点** |
| `.lastChild` | **L807 区段** | `getSelectedDashLogText()`: `var text = row.lastChild` |
| `.parentNode` | **L807:17712 附近** | 记忆详情 `.mem-enc` 的上溯 |

> **最脆的一处是 `.lastChild`**：`loadDashLog()` 里 `row.innerHTML = check + '<span class="log-type">' + '<span class="log-time">' + text`，
> 然后 `var textNode = row.lastChild; textNode.textContent = '';` 做逐字打字。
> **只要在日志文本后面再加任何元素（哪怕一个空 span），打字机就会作用到错的节点上。**
> 位置：L798 区段（`loadDashLog` 函数体内）。

## 4.5 【高危】完全靠「子元素顺序」取值（最脆的结构依赖）

`tokens` 页的 4 张统计卡**没有任何 id**，靠**位置下标**取值：

| 位置 | 代码 |
|---|---|
| **L2784** | `root.querySelectorAll('.grid-4 .panel .meta')[0].textContent = fmt(t7)` |
| **L2785** | `root.querySelectorAll('.grid-4 .panel .meta')[1].textContent = fmt(t30)` |
| **L2786** | `root.querySelectorAll('.grid-4 .panel .meta')[2].textContent = fmt(total)` |
| **L2787** | `root.querySelectorAll('.grid-4 .panel .meta')[3].textContent = (total*0.5/1e6).toFixed(4)+' 元'` |

这条选择器 `.grid-4 .panel .meta` 的**三个 class 一个都不能改，且 4 张卡内 `.meta` 的顺序不能变**。
如果重做时给这些值加上 id（看似更现代），**必须同时改 JS**，否则 `[0]`–`[3]` 会取到错值或 `undefined` 抛错。
**建议：本页重做时只改视觉，不动 `.grid-4` / `.panel` / `.meta` 三层结构和顺序。**

同款风险：`#dashCards .card`（L807:1936）也是靠 `card.dataset.id` + `.card-value` 子元素定位，
且 `#dashCards` 本身不存在（§4.1）。

## 4.6 【中危】靠内联 `style` 的值判状态（不是读 DOM 属性）

全文件有 **165 处 `.style.X`** 读写，其中 **50 处读 `.style.display`** 用作状态判断。
**重做时若改用 class 控制显隐（这是最自然的现代做法），这些判断全会失效。**

`groupmanage` 的页签是最典型的例子：

| 位置 | 代码 |
|---|---|
| **L3057 / L3058** | `switchTab()` 里 `g.style.display = ...` / `f.style.display = ...` 切换群/好友页签 |
| **L3066:47** | 读 `document.getElementById('gmFriendTab').style.display !== 'none'` 判断当前是哪个页签 |

其余 48 处 `.style.display` 读取分布：

| 函数 | 次数 | 位置 |
|---|---|---|
| `bindSettingsEvents` | 10 | `L2167:50`· `L2169:24`· `L2181:28`· `L2182:15`· `L2210:18`· `L2215:20`· `L2220:18`· `L2227:23`· `L2230:20`· `L2247:18` |
| `oboRender` | 7 | `L3982:39`· `L3987:40`· `L4004:40`· `L4014:40`· `L4025:40`· `L4035:40`· `L4045:40` |
| `initSettingsSearch` | 5 | `L917:12`· `L924:36`· `L925:11`· `L931:62`· `L932:62` |
| `fetchRemoteModels` | 4 | `L2646:14`· `L2649:14`· `L2651:14`· `L2669:12` |
| `loadSettingsData` | 3 | `L1622:63`· `L1633:38`· `L1641:48` |
| `mountProtocol` | 3 | `L3479:101`· `L3479:129`· `L3479:287` |
| `updateLlmModeUI` | 2 | `L1854:26`· `L1855:24` |
| `applyModelOptions` | 2 | `L2565:24`· `L2566:20` |
| `updateApiKeyStatus` | 2 | `L2579:22`· `L2591:13` |
| `switchTab` | 2 | `L3057:7`· `L3058:7` |
| `None` | 1 | `L627:47` |
| `openMemoryDetail` | 1 | `L807:14025` |
| `showBox` | 1 | `L811:180` |
| `hideSplash` | 1 | `L829:3582` |
| `mountEditableList` | 1 | `L1417:12` |
| `openAddModal` | 1 | `L1463:13` |
| `collectSettings` | 1 | `L1771:102` |
| `buildLlmTestPayload` | 1 | `L1863:37` |
| `validateLLMForm` | 1 | `L1905:45` |
| `mountGroupManager` | 1 | `L3066:47` |

> **`settings` 页最密集**：`initSettingsSearch`(L917–L932)、`bindSettingsEvents`(L2167–L2247)、
> `loadSettingsData`(L1622–L1641)、`collectSettings`(L1771)、`updateLlmModeUI`(L1854–L1863)、
> `validateLLMForm`(L1905)、`applyModelOptions`(L2565)、`updateApiKeyStatus`(L2579)、`fetchRemoteModels`(L2646–L2669)。
> 这些函数用 `display:none` 表示「本配置项不适用」，用 display 值判断分支 —— 改成 class 后逻辑会走错分支，
> **可能提交错误的配置到后端**（比视觉问题严重得多）。

## 4.7 【中危】`innerHTML` 字符串拼接且含用户/后端数据（XSS 风险）

全文件有 **427 处**内联 `style=` 在 `<script>` 内，说明大量 HTML 是字符串拼出来的。
下面这些拼接点**未使用 `escapeHtml()`**，其中若干直接插入后端返回的文本：

| 位置 | 函数 | 风险内容 |
|---|---|---|
| **L615:37** | — | `'识别失败：' + ((r && r.error) \|\| '未知错误')` —— **后端 error 字段直接进 innerHTML** |
| **L894:8** | `mountSettings` | `'<div class="settings-panel-title">'+g.title+'</div>'` |
| **L1724:7** | `renderGroupToolList` | `tools.map(...)` 拼工具名 |
| **L2781:74** | `mountTokens` 的 `load` | `'<div class="meta">'+j.error+'</div>'` —— **后端 error 直接进 innerHTML** |
| **L3148:11** | `mountMemes` 的 `_render` | 空态标题拼接 |
| **L3810:8** | `showConfirm` | `'<div class="modal-title">'+msg+'</div>'` —— `msg` 来自调用方，`protoRiskMsg()` 会拼入驱动名 |
| **L3828:10** | `showPrompt` | `'<div class="modal-title">'+msg+'</div>'` |
| **L3847:6** | `addEmptyState` | `icon` / `text` / `hint` 三处拼接 |
| **L1439 / L1538 / L1979** | `openAddModal` / `openProviderModal` / `openSetupWizard` | 弹窗表单拼接（含 provider 名等后端字段）|

对照：转录安全的地方用了 `escapeHtml()`（例如 **L835 区段** `__logRender` 里 `escapeHtml(l.time)`、
`mountConsole` 里 `escapeHtml(v)`、`renderProtoLogs` 里 `escapeHtml(l.text)`）。

> **迁移要求**：重做只应改结构，**不要「顺手」把这些拼接改成模板字符串而不加转义**，
> 也不要把本来安全的 `escapeHtml()` 调用删掉。
> 上述未转义点是**既有问题**；如要修复，属于独立的安全变更，需单独验证，不要混在视觉重做里。

`escapeHtml` 的定义位置：未在本次分析中单独定位（其调用点遍布全文），**未能确认**其具体行号。

## 4.8 【中危】内联 `onclick` 依赖全局函数名

内联属性里的函数必须在 `window` 作用域可见。若重做时把脚本包进 IIFE 或改成 ES module，
这 11 处内联属性会全部抛 `ReferenceError`，**且只在用户点击时才暴露**。

| 内联调用的函数 | 出现次数 | 位置 |
|---|---|---|
| `protoOpen()` | 4 | `L3446:82`· `L3447:91`· `L3448:94`· `L3449:87` |
| `router()` | 3 | `L829:7535`· `L829:7869`· `L829:8208` |
| `protoApplySwitch()` | 1 | `L707:37` |
| `testPluginApi()` | 1 | `L3751:102` |

同理 `pageMounts` 里的 15 个 `mountXxx` 也必须是全局函数（`router()` 通过对象字面量引用，
若 `pageMounts` 与函数定义不在同一作用域也会断）。

## 4.9 【低危但易漏】JS 选择器里出现但 CSS 里没有定义的 class

这些 class 只被 JS 用作选择器/状态标记，`<style>` 里没有任何规则：

| class | JS 引用次数 | 说明 |
|---|---|---|
| `.mem-stream` | 1 | 纯 JS 钩子，**无视觉**；改名会断 JS，但改动不影响外观 |
| `.pfb-row` | 3 | 纯 JS 钩子，**无视觉**；改名会断 JS，但改动不影响外观 |

## 4.10 【低危】CSS 死代码与不存在的元素

- `#bloomCanvas`：CSS 有规则（**L1**）但 HTML 无此元素 —— 死 CSS。
- `#decodeGrid`：JS 引用但无元素（§4.1）—— 启动页字符雨背景**从未运行**。
- `loadCharts()` / `makeChart()` / `#chart-msg|ai|img`：全部死代码（§4.2）。
- `loadPlugins`(L807) / `updateMonitor`(L807:5840) / `getSelectedDashLogText`(L806)：被同名后定义覆盖的死代码（§4.3）。
- `const pageCache = {}`（L3804 前）：定义但从未使用。
- 403 个有定义的 id 中有 **159 个** JS 从不引用（其中多数仅由 CSS 或纯静态使用）。

> 重做时可以清理这些死代码，但**务必先确认它们真的没有被 `window` 动态引用或字符串拼接引用**，
> 例如 `providerCards` 是通过 `bar.id = 'providerCards'` 赋值的（L940），静态搜索 `id="providerCards"` 找不到。

---

# 附录 A：分析方法与可复核性

## A.1 分析方式

所有结论由**只读**脚本对源码做静态分析得出，**未修改任何被分析的文件**（包括该 HTML）。脚本位于
`D:\git\_eggbloom-backup-20261003\`（`analyze_webui.py`、`extract2.py`、`extract3.py`、`perpage.py`、
`callgraph.py`、`risks.py`、`cssinv.py`、`allsel.py`、`shell.py`、`dataattr.py`、`gen1.py`、`gen2.py`、
`gen3.py`、`accept.py`、`verify.py`、`verify2.py`），中间结果在 `facts*.json`、`*.txt`。

## A.2 精确定位方法（为什么不用简单正则）

1. 读入全文，建立 `offset → (line, col)` 索引。
2. 用 `function <name>(<params>) {` 找到函数名与其 `{` 的偏移。
3. 对该 `{` 做**花括号配对**求匹配 `}`。配对器是一个状态机，会正确跳过：
   - 单/双引号字符串（含 `\` 转义）
   - 模板字符串 `` ` ``（含 `\` 转义）
   - 模板插值 `${ ... }`（递归配对，插值内可再嵌套字符串/模板）
   - `//` 行注释与 `/* */` 块注释
4. 每个函数得到精确的 `[start, end]` 偏移区间。
5. 任何属性/选择器/事件的命中位置，都用「**包含该偏移的最内层函数**」归属到具体函数，
   **不按行归属**。这解决了「L839 一行含 `mountConsole` + `mountImagegen` + `mountMemory` + `mountPluginPage` 四个函数」
   以及「L2673 一行含 `mountCommands` + `mountBehavior` 两个函数」的串页问题。

## A.3 三处已知的提取难点（已处理）

| 难点 | 表现 | 处理 |
|---|---|---|
| 半压缩 | 单行 5 万字符 | 全程按偏移量而非行号 |
| JS 字符串里的转义引号 | `root.innerHTML = \'<div id=\'tkBar\'>\'` | 正则与配对器都接受 `\"` / `\'` 形式，否则会漏掉 `tokens` 页全部 2 个 id 与 `behavior` 页全部 13 个 id |
| 渲染写法不统一 | 11 个页面用模板字符串；`behavior`/`memes`/`tokens`/`groupmanage` 用 `\'...\' + \'...\'` 串联 | 渲染区提取器同时支持两种；模板 id 计数基于「去掉 JS 字符串引号后的 HTML 文本」 |

## A.4 与任务描述不符的三处（已实测确认）

| 项 | 任务描述 | 实测 | 处理 |
|---|---|---|---|
| 总行数 | 4339 | `split("\n")` = **4371**；`Get-Content` = 4370 | 文中一律用 4371；**4339 未能确认** |
| 页面数 | 14（列出 dashboard/monitor/chatlogs/console/imagegen/memory/tokens/plugins/settings/bot/protocol/behavior/groupmanage/memes）| `pageMounts` = **15**，多出 `commands` | 第 2 部分覆盖全部 15 个，并单列 `commands` |
| `panel-title` | 24 处 | **41 处** | 按 41 记录 |
| `chart.min.js` | 提到依赖 | **文件中不存在任何 `<script src>`** | §4.2 单列说明 |

## A.5 明确标注「未能确认」的项

以下项本次分析未能给出确定结论，**未做估算**，使用时请自行运行期验证：

| 项 | 状态 |
|---|---|
| 文件总行数 4339 | **未能确认**（实测 4371 / 4370） |
| `escapeHtml()` 的定义行号 | **未能确认**（调用点遍布全文，定义位置未单独定位） |
| `galleryGrid` 的空值守卫写法 | **未能确认**（`loadGallery()` 内未逐句核对） |
| `memoryDecodeAll` / `memoryEncodeAll` 的空值守卫写法 | **未能确认**（`openMemoryDetail()` 内未逐句核对） |
| `loadCharts()` 用到的 `pad()` 是否定义 | **未能确认** |
| `window` 上的动态引用（如字符串拼接 `window['load'+x]`） | **未做穷举**；§4.10 的死代码清理前需人工确认 |
| 运行期实际 DOM（本次为纯静态分析） | **未做**：建议迁移后用浏览器逐个执行 §2 的验收清单 |

## A.6 迁移审计建议

1. **先固化基线**：在改任何东西之前，对当前版本做一次「交互清单全过」的录屏或截图存档（15 页 × §2 清单）。
2. **diff 校验脚本**：对改后的文件重跑本套脚本，比对以下不变量——
   `pageMounts` 的 15 个 key、每个 `mountXxx` 的 id 集合、`SEL['cls']` / `SEL['ids']` / `data-*` 集合、
   `F5['defrefs']` 的 id 集合。**任何缺失项都是功能回归**。
3. **改一页验一页**：一次只动一个页面，跑该页 §2 清单 + 该页定时器（等一个周期确认数值在动）。
4. **不要混入变更**：§4.1 的功能缺口、§4.2 的 Chart.js、§4.7 的未转义拼接都是**既有问题**，
   修它们属于功能/安全变更，必须与视觉重做分开提交、分开验证。
