"""最小 HTTPS 标识 server：自签证书，返回 '<TAG>@<ip:port>'，记 hits。"""
import http.server
import socketserver
import ssl
import sys

tag = sys.argv[1]
ip = sys.argv[2]
port = int(sys.argv[3])
cert = sys.argv[4]
key = sys.argv[5]
HITS = f"/tmp/hits_{tag}.txt"


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            with open(HITS, "a") as f:
                f.write(".\n")
        except Exception:
            pass
        body = f"{tag}@{ip}:{port}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def log_message(self, *a):
        pass


ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(cert, key)


class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


srv = S((ip, port), H)
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
srv.serve_forever()
