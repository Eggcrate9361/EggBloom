# -*- coding: utf-8 -*-
"""
protocols/llonebot_adapter.py — LLOneBot 协议驱动（OneBot v11）

LLOneBot 是 QQNT 插件型框架（https://github.com/LLOneBot/LLOneBot）：
- 方式 A（插件模式）：把 LLOneBot 装进 QQNT 插件目录，启动 QQ 即自动加载
  → 协议面板「exe_path」填 QQ.exe（或 LLOneBot 启动器），OneBot 网络配置
    在 LLOneBot 面板（或 LLOneBot/config/config.json）完成
- 方式 B（独立启动器）：部分发行版提供独立 exe 直接拉起

动作路径：标准 OneBot v11（/onebot/v11），LLOneBot 4.x 默认 HTTP :3000、
WS Server :3001（正向）。各驱动端口互不冲突，切换驱动时清空旧进程。

事件通道：与 NapCat 一致走方案 B——WS Server → 内置 WS 客户端订阅 →
POST bot_core:3005（复用 OneBotStdAdapter 事件桥 + PING→PONG 保活）。
"""

import json
import os
import logging

from protocols.onebot_std_adapter import OneBotStdAdapter, _read_json
from protocols import registry

logger = logging.getLogger(__name__)

# LLOneBot 官方默认端口（HTTP 动作 / WS Server）
DEFAULT_API_URL = "http://127.0.0.1:3000/onebot/v11"
DEFAULT_WS_PORT = 3001
# LLOneBot WebUI 默认端口（登录/配置引导）
DEFAULT_WEBUI_PORT = 7000


class LLOneBotAdapter(OneBotStdAdapter):
    """LLOneBot 协议驱动：QQNT 插件 + OneBot v11（HTTP + WS Server 事件桥）"""

    name = "llonebot"
    driver_key = "llonebot"
    display_name = "LLOneBot"
    default_api_url = DEFAULT_API_URL
    default_ws_port = DEFAULT_WS_PORT
    default_webui_port = DEFAULT_WEBUI_PORT
    # 停止/切换时连同 QQ 一起清理，避免注入态 QQ 残留与其他驱动冲突
    kill_images = ["LLOneBot.exe", "LLOneBot_WebUI.exe", "QQ.exe", "QQEX.exe"]

    def _build_command(self) -> list:
        """启动命令：直接运行配置的 exe（QQ.exe 或 LLOneBot 启动器）。"""
        return [self.exe_path]

    def _build_env(self) -> dict:
        env = super()._build_env()
        if self.exe_path.lower().endswith("qq.exe"):
            # 插件模式：QQ 启动时自动加载 LLOneBot
            env["LLONEBOT_ENABLED"] = "1"
        return env

    def _find_llonebot_configs(self) -> list:
        """尽力定位 LLOneBot 插件 config/config.json（兼容多种安装形态，找不到返回空列表）。"""
        roots = []
        exe = str(self._cfg.get("exe_path") or "").strip().strip('"')
        if exe:
            roots.append(os.path.dirname(exe))
        if self._driver_dir:
            roots.append(self._driver_dir)
        rels = [
            os.path.join("resources", "app", "plugins", "LLOneBot", "config", "config.json"),
            os.path.join("LiteLoaderQQNT", "plugins", "LLOneBot", "config", "config.json"),
            os.path.join("plugins", "LLOneBot", "config", "config.json"),
            os.path.join("LLOneBot", "config", "config.json"),
            os.path.join("config", "config.json"),
        ]
        found = []
        for root in roots:
            if not root or not os.path.isdir(root):
                continue
            for rel in rels:
                p = os.path.join(root, rel)
                if os.path.exists(p) and os.path.isfile(p):
                    found.append(p)
            # 兼容自定义位置：根下子目录名以 llonebot 开头，内含 config/config.json
            try:
                for sub in os.listdir(root):
                    if not sub.lower().startswith("llonebot"):
                        continue
                    p = os.path.join(root, sub, "config", "config.json")
                    if os.path.exists(p) and os.path.isfile(p):
                        found.append(p)
            except Exception:
                pass
        seen, uniq = set(), []
        for p in found:
            if p not in seen:
                seen.add(p)
                uniq.append(p)
        return uniq

    def _ensure_network_config(self):
        """尽力自动写入 LLOneBot 网络配置（HTTP api_port + WS ws_port，免鉴权）。

        找不到配置文件（LLOneBot 未安装/路径异常）时仅告警不阻塞，
        由用户在 LLOneBot 面板手动配置。
        """
        api_port, ws_port = int(self.api_port), int(self.ws_port)
        cfgs = self._find_llonebot_configs()
        if not cfgs:
            logger.warning(
                "LLOneBot 配置文件未找到，请在 LLOneBot 面板手动配置 OneBot 网络（HTTP %s / WS %s）",
                api_port, ws_port,
            )
            return
        for path in cfgs:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                net = data.get("network")
                if not isinstance(net, dict):
                    logger.warning("LLOneBot 配置缺少 network 字段，跳过自动写入: %s", path)
                    continue
                changed = False
                net["enable"] = True
                for srv in net.get("httpServers") or []:
                    if isinstance(srv, dict):
                        srv["enable"] = True
                        srv["port"] = api_port
                        srv["token"] = ""
                        changed = True
                for srv in net.get("wsServers") or []:
                    if isinstance(srv, dict):
                        srv["enable"] = True
                        srv["port"] = ws_port
                        srv["token"] = ""
                        changed = True
                if changed:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    logger.info("LLOneBot 已自动写入网络配置: %s", path)
                else:
                    logger.info("LLOneBot 配置无需修改: %s", path)
            except Exception as e:
                logger.warning("LLOneBot 自动配置写入失败 %s: %s", path, e)

    def get_webui_info(self) -> dict:
        """读取 LLOneBot WebUI 登录信息（config/webui.json，格式可能与 NapCat 不同，尽力兼容）。"""
        info = {
            "found": False,
            "host": "127.0.0.1",
            "port": self.webui_port,
            "url": f"http://127.0.0.1:{self.webui_port}",
            "token": "",
        }
        try:
            wj = _read_json(os.path.join(self._driver_dir, "config", "webui.json"), {})
            if wj:
                info.update({
                    "found": True,
                    "port": int(wj.get("port") or self.webui_port),
                    "url": f"http://127.0.0.1:{int(wj.get('port') or self.webui_port)}",
                    "token": str(wj.get("token") or ""),
                })
        except Exception:
            pass
        return info


registry.register("llonebot", LLOneBotAdapter)
