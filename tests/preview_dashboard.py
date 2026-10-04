"""Static preview only, not a replacement for the authenticated Jarvis backend.

Run: python tests/preview_dashboard.py --port 8766
"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


class PreviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND), **kwargs)

    def do_GET(self):
        route = self.path.split("?", 1)[0]
        if route.startswith("/api/"):
            self.send_error(503, "Static preview has no backend")
            return
        pages = {"/": "settings.html", "/dashboard": "dashboard.html",
                 "/chat": "chat.html", "/assistant": "chat.html", "/portal": "portal.html",
                 "/settings": "settings.html", "/wissen": "wissen.html",
                 "/userchat": "userchat.html"}
        if route in pages:
            self.path = "/" + pages[route]
        elif route.startswith("/static/"):
            self.path = self.path[len("/static"):]
        super().do_GET()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    print(f"Static preview: http://127.0.0.1:{args.port}/dashboard", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler).serve_forever()
