#!/usr/bin/env python3
"""Avisos opcionales por ntfy o Telegram usando variables de entorno.

Usar notify.py "título" "mensaje" o import notify; notify.send(titulo, mensaje).
Si no hay ningún canal configurado, sólo escribe en stdout.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

def _load():
    return {key: os.environ.get(key, "") for key in
            ("NTFY_TOPIC", "NTFY_SERVER", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")}


def send(title, message, priority="default"):
    cfg = _load()
    sent = []
    if cfg.get("NTFY_TOPIC"):
        url = f"{(cfg.get('NTFY_SERVER') or 'https://ntfy.sh').rstrip('/')}/{cfg['NTFY_TOPIC']}"
        req = urllib.request.Request(url, data=message.encode("utf-8"), method="POST",
                                     headers={"Title": title.encode("latin-1", "replace").decode("latin-1"),
                                              "Priority": priority, "Tags": "video_camera"})
        try:
            urllib.request.urlopen(req, timeout=15).read()
            sent.append("ntfy")
        except Exception as e:  # noqa: BLE001
            print(f"notify: fallo ntfy: {e}", file=sys.stderr)
    if cfg.get("TELEGRAM_TOKEN") and cfg.get("TELEGRAM_CHAT_ID"):
        url = f"https://api.telegram.org/bot{cfg['TELEGRAM_TOKEN']}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": cfg["TELEGRAM_CHAT_ID"],
                                       "text": f"{title}\n{message}"}).encode()
        try:
            urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15).read()
            sent.append("telegram")
        except Exception as e:  # noqa: BLE001
            print(f"notify: fallo telegram: {e}", file=sys.stderr)
    print(f"notify [{','.join(sent) or 'sin canal'}] {title}: {message}")
    return sent


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    send(sys.argv[1], " ".join(sys.argv[2:]))
