# -*- coding: utf-8 -*-
"""tests/test_autonomy_live.py —— 真下载路径（本机 HTTP，不连外网）。

起一个 127.0.0.1 的 HTTP 服务，支持 Range；用 autonomy.tools._http_get +
acquire.download_resume 走真实 socket / urllib / 断点续传。
"""
import hashlib
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PAYLOAD = (b"EggBloom-autonomy-live-" * 4096)  # ~100KB
SHA = hashlib.sha256(PAYLOAD).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        rng = self.headers.get("Range")
        start = 0
        if rng and rng.startswith("bytes="):
            try:
                start = int(rng.split("=", 1)[1].split("-", 1)[0])
            except Exception:
                start = 0
        body = PAYLOAD[start:]
        self.send_response(206 if start else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if start:
            self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD)-1}/{len(PAYLOAD)}")
        self.end_headers()
        self.wfile.write(body)


PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


def main():
    td = tempfile.mkdtemp(prefix="eb-live-")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_port}/file.bin"
    try:
        from autonomy import acquire
        from autonomy.tools import _http_get

        # 1 全新下载
        dest = os.path.join(td, "full.bin")
        r = acquire.download_resume(base, dest, http_get=_http_get,
                                    total_hint=len(PAYLOAD),
                                    expected_sha256=SHA)
        check("本机 HTTP 下载成功", r.get("ok") is True, r)
        check("内容完整", open(dest, "rb").read() == PAYLOAD)
        check("sha256 对齐", r.get("sha256") == SHA, r.get("sha256"))

        # 2 断点续传：先造 10KB 的 .part
        dest2 = os.path.join(td, "resume.bin")
        with open(dest2 + ".part", "wb") as f:
            f.write(PAYLOAD[:10000])
        r2 = acquire.download_resume(base, dest2, http_get=_http_get,
                                     total_hint=len(PAYLOAD),
                                     expected_sha256=SHA)
        check("Range 续传成功", r2.get("ok") is True and r2.get("resumed") is True, r2)
        check("续传后内容完整", open(dest2, "rb").read() == PAYLOAD)

        # 3 RealBackend 的 model 分支（真实 urllib，不注入）
        from autonomy.tools import RealBackend
        art = {"kind": "model", "name": "live", "target": os.path.join(td, "backend.bin"),
               "url": base, "sha256": SHA, "size_bytes": len(PAYLOAD)}
        r3 = RealBackend().acquire(art, td)
        check("RealBackend 下载", r3.get("ok") is True, r3)
        check("RealBackend 内容完整", open(art["target"], "rb").read() == PAYLOAD)

        # 4 runner 全链路（plan → acquire → verify → wire）
        from autonomy import runner
        code = runner.main(["--need", "live", "--kind", "model", "--url", base,
                            "--sha256", SHA, "--size-bytes", str(len(PAYLOAD)),
                            "--root", td, "--data", td, "--skip-net", "--json"])
        check("runner 成功返回", code == 0, code)
        check("runner 产物落 models",
              os.path.exists(os.path.join(td, "models", "misc", "live")),
              os.listdir(os.path.join(td, "models", "misc")) if os.path.exists(os.path.join(td, "models", "misc")) else None)
    finally:
        srv.shutdown()
        srv.server_close()

    print("=" * 58)
    if FAIL:
        print(f"失败 {FAIL} 条：")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print(f"通过 {PASS} / 失败 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
