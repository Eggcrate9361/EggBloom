# -*- coding: utf-8 -*-
"""Helper: 无弹窗杀进程（供 protocol_manager.py 引入）"""

import ctypes
import subprocess


def kill_process_no_window(pid: int):
    """使用 Windows API 直接杀进程，不弹 cmd 窗口"""
    try:
        handle = ctypes.windll.kernel32.OpenProcess(
            0x1000 | 0x1, False, pid
        )  # PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            ctypes.windll.kernel32.TerminateProcess(handle, 1)
            ctypes.windll.kernel32.CloseHandle(handle)
            return True, None
        else:
            return False, f"无法打开 PID={pid}"
    except Exception as e:
        return False, str(e)


def kill_by_name_no_window(names: list):
    """按进程名查找并杀掉，不弹窗口"""
    results = []
    for name in names:
        try:
            r = subprocess.run(
                ['wmic', 'process', 'where', f'name="{name}"', 'get', 'ProcessId', '/format:csv'],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            killed = 0
            for line in (r.stdout.decode("utf-8", errors="replace") if isinstance(r.stdout, bytes) else r.stdout).strip().splitlines():
                parts = line.strip().split(',')
                if len(parts) >= 2 and parts[0] == name:
                    try:
                        pid = int(parts[1].strip())
                        ok, err = kill_process_no_window(pid)
                        if ok:
                            killed += 1
                    except (ValueError, TypeError):
                        pass
            results.append((name, killed, None))
        except Exception as e:
            results.append((name, 0, str(e)))
    return results
