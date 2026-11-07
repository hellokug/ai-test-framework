"""静态稿预览服务器：Cache-Control: no-store（避免浏览器缓存挡住静态稿迭代）。
用法：python serve.py [端口]（默认 8766；可同时跑多个端口）。

/api/* 反向代理到真实后端（默认 http://127.0.0.1:8300，环境变量 AITE_API 覆盖）：
静态稿的原地对接层（api.js）走同源 /api 路径，后端零改动（无需 CORS）。
后端未启动时代理返回 502，api.js 据此回退假数据并标记 MOCK。
"""
import http.server
import json
import os
import socketserver
import sys
import urllib.error
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
API_BASE = os.environ.get("AITE_API", "http://127.0.0.1:8300")


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    # ---------------------------------------------------------- /api 反向代理
    def _proxy(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(API_BASE + self.path, data=body,
                                     method=self.command)
        for header in ("Content-Type", "Authorization"):
            if self.headers.get(header):
                req.add_header(header, self.headers[header])
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload, status = resp.read(), resp.status
        except urllib.error.HTTPError as exc:
            payload, status = exc.read(), exc.code
        except (urllib.error.URLError, OSError) as exc:
            payload = json.dumps({"detail": f"backend unreachable: {exc}"}).encode()
            status = 502
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._proxy()
        else:
            super().do_GET()

    def do_POST(self):
        self._proxy()

    def do_PUT(self):
        self._proxy()

    def do_PATCH(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()


socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as srv:
    print(f"serving on http://127.0.0.1:{PORT} (no-store, api -> {API_BASE})")
    srv.serve_forever()
