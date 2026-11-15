webui/fonts/ —— 本地像素字体包（随应用分发，不联网）

Press Start 2P  —— 显示体（壳体页码、标题数字）
Silkscreen      —— 等宽/标注体
Fusion Pixel 12px SC —— 中文正文/标签（fusion-pixel-12px-sc.ttf / .woff2）

授权：均为 SIL Open Font License 1.1（OFL-1.1）—— 明确允许自由使用、修改、
      **再分发与打包**（Bundling），因此可以放进应用目录离线使用。
      再分发时须保留本文件的版权与许可说明。

来源：
  · Press Start 2P —— @fontsource/press-start-2p（Google Fonts 收录，作者 CodeMan38）
  · Silkscreen     —— @fontsource/silkscreen（Google Fonts 收录，作者 Jason Kottke）
  · Fusion Pixel Font（缝合像素字体）—— TakWolf/fusion-pixel-font
    https://github.com/TakWolf/fusion-pixel-font
    本项目使用 12px 简体中文（SC）版本，OFL-1.1。

说明：刻意**不用** Google Fonts CDN —— 它在大陆被墙，且桌面端应用
      不应该为了字体去请求第三方。

若要更新：重新下载对应字体文件覆盖同名文件即可，无需改 HTML。
