"""最小 HTTP 标识 server：每次 do_GET 先记 hits 文件（落点铁证），再返回 body。

hits 在 wfile.write 之前写入——即使 chrome 经代理中途断开（BrokenPipe），
请求到达已被记录。
"""
import http.server
import socketserver
import sys
import time

tag = sys.argv[1]
ip = sys.argv[2]
port = int(sys.argv[3])
HITS = f"/tmp/hits_{tag}.txt"


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            with open(HITS, "a") as f:
                f.write(f"{time.time():.3f} from={self.client_address[0]}\n")
        except Exception:
            pass
        body = f"{tag}@{ip}:{port}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def log_message(self, *a):
        pass


class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


S((ip, port), H).serve_forever()
