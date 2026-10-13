#!/bin/sh
set -eu
mkdir -p /data/profile /data/recordings /data/logs /data/state "$XDG_RUNTIME_DIR"
chown recorder:recorder /data/profile /data/logs /data/state "$XDG_RUNTIME_DIR"
# El reproductor monta las grabaciones en sólo lectura; allí chown no aplica.
chown recorder:recorder /data/recordings 2>/dev/null || true
chmod 700 "$XDG_RUNTIME_DIR"
exec /usr/sbin/gosu recorder "$@"
