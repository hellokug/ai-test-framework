"""静态稿预览服务器：Cache-Control: no-store（避免浏览器缓存挡住静态稿迭代）。
用法：python serve.py [端口]（默认 8766；可同时跑多个端口）。"""
import http.server
import socketserver
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8766


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as srv:
    print(f"serving on http://127.0.0.1:{PORT} (no-store)")
    srv.serve_forever()
