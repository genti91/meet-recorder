FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HOME=/home/recorder \
    XDG_RUNTIME_DIR=/tmp/recorder-runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver python3 python3-selenium tzdata procps \
    ffmpeg xvfb x11-utils pulseaudio pulseaudio-utils \
    x11vnc novnc websockify tini gosu passwd ca-certificates fonts-liberation fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/sh recorder \
    && mkdir -p /data/profile /data/recordings /data/logs /data/state "$XDG_RUNTIME_DIR" \
    && mkdir -p /etc/chromium/policies/managed \
    && printf '%s\n' '{"BrowserSignin":0,"SyncDisabled":true,"MetricsReportingEnabled":false}' > /etc/chromium/policies/managed/meet-recorder.json \
    && chown recorder:recorder "$XDG_RUNTIME_DIR" \
    && chmod 700 "$XDG_RUNTIME_DIR"

WORKDIR /app
COPY app/ /app/
ENTRYPOINT ["/usr/bin/tini", "--", "/bin/sh", "/app/entrypoint.sh"]
CMD ["python3", "/app/scheduler.py"]
