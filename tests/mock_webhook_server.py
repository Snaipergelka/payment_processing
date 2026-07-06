import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = raw.decode(errors="replace")
        print(f"\n=== Webhook received on {self.path} ===")
        print(json.dumps(body, indent=2, ensure_ascii=False) if isinstance(body, dict) else body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"received": true}')

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - silence default access log
        pass


if __name__ == "__main__":
    port = 9000
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Mock webhook server listening on http://0.0.0.0:{port}")
    server.serve_forever()
