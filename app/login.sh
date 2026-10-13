#!/bin/sh
set -eu
mkdir -p /data/profile "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp -ac &
export DISPLAY=:99
sleep 2
x11vnc -display :99 -forever -shared -nopw -listen 127.0.0.1 -rfbport 5900 -bg >/dev/null 2>&1
websockify --web=/usr/share/novnc 0.0.0.0:6080 127.0.0.1:5900 &
echo 'Abrí http://localhost:6080/vnc.html para iniciar sesión en Google.'
exec chromium --no-sandbox --disable-dev-shm-usage --disable-gpu \
  --allow-browser-signin=false \
  --user-data-dir=/data/profile --password-store=basic \
  --no-first-run --no-default-browser-check \
  --window-size=1280,720 https://accounts.google.com/
