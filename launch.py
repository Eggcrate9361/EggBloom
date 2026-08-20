#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EggBloom Desktop 启动脚本
自动定位 Electron 运行时与应用目录，支持开发/打包两种模式。
"""
import os
import sys
import subprocess
import shutil
from pathlib import Path


def find_electron_and_app():
    """定位 Electron 可执行文件与应用目录。

    优先级：
    1. 打包后：同目录 resources/app 结构（electron.exe 在 runtime/ 下）
    2. 开发模式：从 __file__ 向上查找 app-src 目录
    3. 环境变量 ELECTRON_PATH / EGG_BLOOM_APP_DIR
    """
    # 1. 打包后结构：exe 同级有 resources/app，electron 在 runtime/electron.exe
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).parent
        # 查找 runtime/electron.exe
        for runtime_dir in (base / 'runtime', base / 'electron-runtime', base):
            electron = runtime_dir / 'electron.exe'
            if electron.exists():
                app_dir = base / 'resources' / 'app'
                if app_dir.exists():
                    return str(electron), str(app_dir)
        # 兜底：同目录找 electron.exe
        electron = base / 'electron.exe'
        if electron.exists():
            app_dir = base / 'resources' / 'app'
            if app_dir.exists():
                return str(electron), str(app_dir)

    # 2. 开发模式：查找 app-src 目录
    dev_root = Path(__file__).parent
    app_src = dev_root / 'app-src'
    if app_src.exists():
        # 尝试从全局安装的 electron 启动
        electron = shutil.which('electron')
        if electron:
            return electron, str(app_src)

    # 3. 环境变量兜底
    env_electron = os.environ.get('ELECTRON_PATH')
    env_app = os.environ.get('EGG_BLOOM_APP_DIR')
    if env_electron and env_app and Path(env_electron).exists() and Path(env_app).exists():
        return env_electron, env_app

    return None, None


def main():
    import shutil
    electron, app_dir = find_electron_and_app()

    if not electron or not app_dir:
        print("[launch] ERROR: 无法定位 Electron 运行时或应用目录")
        print("  请确保：")
        print("  1. 打包版：electron.exe 位于 runtime/ 目录，resources/app 存在")
        print("  2. 开发版：已安装 electron (npm i -g electron) 或设置 ELECTRON_PATH")
        print("  3. 或设置环境变量 ELECTRON_PATH 和 EGG_BLOOM_APP_DIR")
        return 1

    print(f"[launch] starting electron: {electron}")
    print(f"[launch] cwd: {app_dir}")

    # Use Popen so it doesn't block
    proc = subprocess.Popen([electron, "."], cwd=app_dir)
    print(f"[launch] electron started with PID: {proc.pid}")
    print("[launch] you can close this window")
    return 0


if __name__ == '__main__':
    sys.exit(main())
