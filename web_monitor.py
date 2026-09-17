#!/usr/bin/env python3
"""Phone web dashboard — replacement for SSD1306 OLED monitor."""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from monitor_data import MonitorData
from music_bridge import music_bridge

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
STATIC_DIR = Path(__file__).resolve().parent / "static"

monitor = MonitorData()

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".json": "application/json; charset=utf-8",
    ".mp4": "video/mp4",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "PhoneMonitor/1.0"

    def log_message(self, fmt, *args):
        return

    def _send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._serve_file(STATIC_DIR / "index.html")
        if path == "/api/status":
            payload = json.dumps(monitor.snapshot(), ensure_ascii=False).encode("utf-8")
            return self._send(200, payload, "application/json; charset=utf-8")
        if path == "/api/scroll/ack":
            monitor.clear_scroll()
            return self._send(200, b'{"ok":true}', "application/json; charset=utf-8")
        if path == "/api/music/events":
            return self._music_sse()
        if path == "/api/music/lyrics":
            qs = parse_qs(urlparse(self.path).query)
            track_id = (qs.get("track_id") or [""])[0]
            payload = json.dumps(
                music_bridge.get_lyrics(track_id),
                ensure_ascii=False,
            ).encode("utf-8")
            return self._send(200, payload, "application/json; charset=utf-8")
        if path.startswith("/api/music/cover/"):
            track_id = path[len("/api/music/cover/") :].strip("/")
            result = music_bridge.get_cover_bytes(track_id)
            if not result:
                return self._send(404, b"Not found", "text/plain; charset=utf-8")
            body, ctype = result
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return

        if path.startswith("/static/"):
            rel = path[len("/static/") :]
        elif path.startswith("/") and (STATIC_DIR / path.lstrip("/")).is_file():
            rel = path.lstrip("/")
        else:
            return self._send(404, b"Not found", "text/plain; charset=utf-8")

        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            return self._send(404, b"Not found", "text/plain; charset=utf-8")
        return self._serve_file(target)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/music":
            return self._send(404, b"Not found", "text/plain; charset=utf-8")
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return self._send(400, b'{"ok":false,"error":"bad json"}', "application/json; charset=utf-8")
        action = body.get("action") if isinstance(body, dict) else None
        value = None
        if isinstance(body, dict):
            if "value" in body:
                value = body.get("value")
            elif "volume" in body:
                value = body.get("volume")
        result = music_bridge.action(str(action or ""), value=value)
        payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
        # accepted commands return 200 even while mpv work continues in queue
        code = 200 if (result.get("ok") or result.get("accepted")) else 400
        return self._send(code, payload, "application/json; charset=utf-8")

    def _music_sse(self):
        import json
        import time

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = music_bridge.subscribe()
        try:
            while True:
                try:
                    payload = q.get(timeout=15.0)
                except Exception:
                    payload = None
                if payload is None:
                    # keepalive comment
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.wfile.write(b"data: " + body + b"\n\n")
                self.wfile.flush()
        except Exception:
            pass
        finally:
            music_bridge.unsubscribe(q)


    def _serve_file(self, path: Path):
        if not path.is_file():
            return self._send(404, b"Not found", "text/plain; charset=utf-8")
        body = path.read_bytes()
        ctype = MIME.get(path.suffix.lower(), "application/octet-stream")
        return self._send(200, body, ctype)


def _warmup():
    try:
        monitor.update_weather_cache()
        monitor.update_jira_cache()
        music_bridge.snapshot(force=True)
    except Exception:
        pass


def main():
    monitor.start()
    httpd = ThreadingHTTPServer((WEB_HOST, WEB_PORT), Handler)
    print(f"Phone monitor http://{WEB_HOST}:{WEB_PORT}/", flush=True)
    threading.Thread(target=_warmup, name="monitor-warmup", daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
