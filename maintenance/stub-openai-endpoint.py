"""A minimal OpenAI-shaped stub: /v1/models with meta.n_ctx, and /props."""
import http.server
import json
import socketserver


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                                          # noqa: N802
        if self.path.endswith("/models"):
            body = json.dumps({"data": [{"id": "stub", "meta": {"n_ctx": 32768}}]}).encode()
            code = 200
        elif self.path.endswith("/props"):
            body = json.dumps({"default_generation_settings": {"n_ctx": 32768}}).encode()
            code = 200
        else:
            body, code = b"{}", 404
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):                                 # noqa: A003
        pass


socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", 18123), H) as srv:
    print("stub endpoint on 127.0.0.1:18123", flush=True)
    srv.serve_forever()
