import subprocess
import sys
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WEBUI_DIR = os.path.join(HERE, "webui")
SERVER_PY = os.path.join(WEBUI_DIR, "webui_server.py")


def main():
    print("[debug_launch] starting webui server...")
    server_proc = subprocess.Popen(
        [sys.executable, SERVER_PY],
        cwd=HERE,          # 与生产一致：main.js 也是以应用根目录为 cwd 启动 webui_server
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    ready = False
    for _ in range(60):
        time.sleep(1)
        try:
            with urllib.request.urlopen("http://localhost:8080", timeout=1) as r:
                if r.status == 200:
                    ready = True
                    break
        except Exception:
            continue

    if not ready:
        print("[debug_launch] server did not become ready in time")

    print("[debug_launch] opening browser http://localhost:8080")
    try:
        import webbrowser
        webbrowser.open("http://localhost:8080")
    except Exception as e:
        print(f"[debug_launch] browser open failed: {e}")

    print("[debug_launch] press Ctrl+C to stop")
    try:
        server_proc.wait()
    except KeyboardInterrupt:
        print("\n[debug_launch] stopping...")
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
            server_proc.wait()


if __name__ == "__main__":
    main()
