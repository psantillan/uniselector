"""A slow HTTP JSON service for the API task: GET /reports/quarterly answers after a fixed delay.

  python -m uniselector_tasks.slow_api --port 8471 --seconds 120
"""
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPORT = {"quarter": "2026-Q3", "currency": "USD", "regions": [
    {"name": "north", "revenue": 48210, "orders": 311}, {"name": "west", "revenue": 91377, "orders": 640},
    {"name": "south", "revenue": 27654, "orders": 198}, {"name": "east", "revenue": 60102, "orders": 402}]}


def main() -> None:
    p = argparse.ArgumentParser(description="Serve one slow JSON report.")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--seconds", type=float, required=True, help="the delay of /reports/quarterly")
    a = p.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            found = self.path.split("?")[0] == "/reports/quarterly"
            if found:
                time.sleep(a.seconds)
            body = json.dumps(REPORT if found else {"error": "not found", "paths": ["/reports/quarterly"]}).encode()
            self.send_response(200 if found else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
