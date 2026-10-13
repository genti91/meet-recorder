#!/usr/bin/env python3
"""Small persistent scheduler for one Meet URL. Timezone comes from TZ in .env."""
import datetime as dt
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
STATE = Path("/data/state/last_run")
child = None
stopping = False


def config():
    url = os.environ.get("MEET_URL", "").strip()
    if not re.fullmatch(r"https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}/?", url):
        raise ValueError("MEET_URL debe ser una URL de Meet con formato https://meet.google.com/abc-defg-hij")
    tokens = [item.strip().lower() for item in os.environ.get("SCHEDULE_DAYS", "").split(",")]
    if not tokens or any(item not in DAYS for item in tokens):
        raise ValueError("SCHEDULE_DAYS debe usar mon,tue,wed,thu,fri,sat,sun")
    clock = os.environ.get("SCHEDULE_TIME", "")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
        raise ValueError("SCHEDULE_TIME debe tener formato HH:MM")
    stop = os.environ.get("STOP_TIME", "")
    if stop and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", stop):
        raise ValueError("STOP_TIME debe tener formato HH:MM")
    dni = os.environ.get("ATTENDANCE_DNI", "")
    if dni and not re.fullmatch(r"\d{7,9}", dni):
        raise ValueError("ATTENDANCE_DNI debe tener entre 7 y 9 dígitos, sin puntos")
    return {DAYS[item] for item in tokens}, clock


def stop_child(_signum, _frame):
    global stopping
    stopping = True
    if child and child.poll() is None:
        child.terminate()


def main():
    global child
    days, clock = config()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, stop_child)
    signal.signal(signal.SIGINT, stop_child)
    print(f"Programado: días {sorted(days)}, {clock}, zona {os.environ.get('TZ', 'sistema')}", flush=True)
    while not stopping:
        now = dt.datetime.now().astimezone()
        today = now.date().isoformat()
        if (now.weekday() in days and now.strftime("%H:%M") == clock
                and (not STATE.exists() or STATE.read_text().strip() != today)):
            STATE.write_text(today + "\n")
            print(f"{now.isoformat()} Iniciando grabación", flush=True)
            child = subprocess.Popen([sys.executable, "/app/auto_meet.py"])
            result = child.wait()
            child = None
            print(f"Grabación terminada: código {result}", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(f"Configuración inválida: {exc}", file=sys.stderr)
        sys.exit(2)
