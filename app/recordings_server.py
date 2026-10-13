#!/usr/bin/env python3
"""Read-only recording browser, bound to localhost for Tailscale Serve."""

from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
import os
import re


RECORDINGS_DIR = Path(os.environ.get("RECORDINGS_DIR", "/data/recordings")).resolve()
HOST = os.environ.get("RECORDINGS_HOST", "127.0.0.1")
PORT = int(os.environ.get("RECORDINGS_PORT", "8765"))
CHUNK_SIZE = 1024 * 1024


def recording(name):
    if not name or "/" in name or "\\" in name or "\x00" in name or not name.endswith(".mp4"):
        return None
    path = RECORDINGS_DIR / name
    if path.is_symlink() or not path.is_file() or path.resolve().parent != RECORDINGS_DIR:
        return None
    return path


def page(title, content):
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title><style>
*{{box-sizing:border-box}}body{{font:16px system-ui,sans-serif;background:#111827;color:#f9fafb;margin:0}}
main{{max-width:900px;margin:auto;padding:32px 18px}}h1{{font-size:clamp(1.5rem,5vw,2.2rem)}}
a{{color:#93c5fd}}.card{{display:block;background:#1f2937;border:1px solid #374151;border-radius:12px;
padding:18px;margin:12px 0;text-decoration:none;color:inherit}}.card:hover{{border-color:#93c5fd}}
.meta{{color:#9ca3af;font-size:.9rem;margin-top:6px}}video{{width:100%;max-height:75vh;background:black;border-radius:12px}}
.actions{{display:flex;gap:18px;margin:20px 0;flex-wrap:wrap}}.empty{{color:#9ca3af}}
</style></head><body><main>{content}</main></body></html>""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.route(send_body=True)

    def do_HEAD(self):
        self.route(send_body=False)

    def route(self, send_body):
        path = urlsplit(self.path).path
        if path == "/":
            items = []
            for video in RECORDINGS_DIR.glob("*.mp4"):
                if recording(video.name) and video.stat().st_size > 0:
                    items.append(video)
            items.sort(key=lambda video: video.stat().st_mtime, reverse=True)
            cards = []
            for video in items:
                stat = video.stat()
                name = escape(video.name)
                href = "/watch/" + quote(video.name)
                date = datetime.fromtimestamp(stat.st_mtime).strftime("%d/%m/%Y %H:%M")
                cards.append(f'<a class="card" href="{href}"><strong>{name}</strong>'
                             f'<div class="meta">{date} · {stat.st_size / 1024**2:.0f} MB</div></a>')
            body = "<h1>Grabaciones de clases</h1>" + ("".join(cards) if cards else
                   '<p class="empty">Todavía no hay grabaciones terminadas.</p>')
            return self.html(page("Grabaciones de clases", body), send_body)

        if path.startswith("/watch/"):
            video = recording(unquote(path[len("/watch/"):]))
            if not video:
                return self.send_error(404)
            name = escape(video.name)
            source = "/files/" + quote(video.name)
            body = (f'<p><a href="/">← Todas las grabaciones</a></p><h1>{name}</h1>'
                    f'<video controls playsinline preload="metadata" src="{source}"></video>'
                    f'<div class="actions"><a href="{source}" download>Descargar MP4</a></div>')
            return self.html(page(video.name, body), send_body)

        if path.startswith("/files/"):
            video = recording(unquote(path[len("/files/"):]))
            if not video:
                return self.send_error(404)
            return self.video(video, send_body)

        self.send_error(404)

    def html(self, data, send_body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; media-src 'self'; base-uri 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def video(self, path, send_body):
        size = path.stat().st_size
        start, end = 0, size - 1
        requested_range = self.headers.get("Range")
        if requested_range:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested_range.strip())
            if not match or not any(match.groups()):
                return self.range_error(size)
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            else:
                suffix = int(last)
                if suffix == 0:
                    return self.range_error(size)
                start = max(size - suffix, 0)
            if start >= size or end < start:
                return self.range_error(size)

        length = end - start + 1
        self.send_response(206 if requested_range else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        if requested_range:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if send_body:
            with path.open("rb") as file:
                file.seek(start)
                remaining = length
                while remaining:
                    chunk = file.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    def range_error(self, size):
        self.send_response(416)
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
