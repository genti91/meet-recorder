#!/usr/bin/env python3
"""
auto_meet.py — Se une a una reunión de Google Meet con la cuenta ya logueada en el
perfil persistente de Chromium, graba pantalla + audio con ffmpeg y sale sola
cuando la reunión termina.

Uso:
    MEET_URL="https://meet.google.com/abc-defg-hij" python3 auto_meet.py
    python3 auto_meet.py https://meet.google.com/abc-defg-hij

Variables de entorno opcionales (con su valor por defecto):
    PROFILE_DIR=/data/profile                 Perfil de Chromium (sesión de Google)
    OUT_DIR=/data/recordings                  Carpeta de salida de los MP4
    LOG_DIR=/data/logs                        Carpeta de logs
    DISPLAY_NUM=:99                           Display de Xvfb
    SCREEN=1280x720                           Resolución de la pantalla virtual (1920x1080 satura la Pi 4)
    FPS=10                                    Fotogramas por segundo de la grabación
    VIDEO_ENCODER=libx264                     libx264 (CPU); h264_v4l2m2m para Pi con /dev/video11
    CHECK_INTERVAL=10                         Segundos entre comprobaciones de asistentes
    END_CONFIRMATIONS=3                       Lecturas seguidas con solo el bot para salir
    MAX_MINUTES=180                           Duración máxima absoluta de la grabación
    STOP_TIME=09:30                           Hora local límite (opcional, HH:MM)
    ATTENDANCE_DNI=12345678                   DNI propio para enviar por chat al tomar lista (opcional)
    NO_START_MINUTES=30                       Salir si en este tiempo la reunión no ha empezado (solo el bot)
    JOIN_TIMEOUT=600                          Segundos máx. esperando ser admitido en la sala
"""
import datetime as dt
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys

# --------------------------------------------------------------------------- config
HOME = Path.home()
MEET_URL = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MEET_URL", "")).strip()
PROFILE_DIR = Path(os.environ.get("PROFILE_DIR", "/data/profile"))
OUT_DIR = Path(os.environ.get("OUT_DIR", "/data/recordings"))
LOG_DIR = Path(os.environ.get("LOG_DIR", "/data/logs"))
DISPLAY_NUM = os.environ.get("DISPLAY_NUM", ":99")
SCREEN = os.environ.get("SCREEN", "1280x720")
FPS = int(os.environ.get("FPS", "10"))
VIDEO_ENCODER = os.environ.get("VIDEO_ENCODER", "libx264")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "10"))
END_CONFIRMATIONS = int(os.environ.get("END_CONFIRMATIONS", "3"))
MAX_MINUTES = int(os.environ.get("MAX_MINUTES", "180"))
STOP_TIME = os.environ.get("STOP_TIME", "").strip()
ATTENDANCE_DNI = os.environ.get("ATTENDANCE_DNI", "").strip()
NO_START_MINUTES = int(os.environ.get("NO_START_MINUTES", "30"))
JOIN_TIMEOUT = int(os.environ.get("JOIN_TIMEOUT", "600"))

CHROMIUM_BIN = shutil.which("chromium") or shutil.which("chromium-browser") or "/usr/bin/chromium"
CHROMEDRIVER_BIN = shutil.which("chromedriver") or "/usr/bin/chromedriver"

SINK_NAME = "meet_sink"      # aquí suena Chromium; ffmpeg graba meet_sink.monitor
MIC_SINK_NAME = "mic_sink"   # micrófono "silencioso" para que Meet tenga un dispositivo
VIRT_MIC_NAME = "virtmic"    # fuente no-monitor (Chromium oculta los monitores) que ve Meet como micro

# --------------------------------------------------------------------------- logging
STAMP = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / f"auto_meet_{STAMP}.log"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("auto_meet")

STOP_REQUESTED = False


def _on_signal(signum, _frame):
    global STOP_REQUESTED
    log.warning("Señal %s recibida: finalizando de forma ordenada", signal.Signals(signum).name)
    STOP_REQUESTED = True


signal.signal(signal.SIGINT, _on_signal)
signal.signal(signal.SIGTERM, _on_signal)


# --------------------------------------------------------------------------- helpers
def sh(cmd, check=False, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, check=check, **kw)


def wait_for_display(display, timeout=15):
    sock = Path(f"/tmp/.X11-unix/X{display.lstrip(':')}")
    t0 = time.time()
    while time.time() - t0 < timeout:
        if sock.exists() and sh(["xdpyinfo", "-display", display]).returncode == 0:
            return True
        time.sleep(0.2)
    return False


# --------------------------------------------------------------------------- Xvfb
def start_xvfb():
    if wait_for_display(DISPLAY_NUM, timeout=1):
        log.info("Xvfb %s ya estaba en marcha, lo reutilizo", DISPLAY_NUM)
        return None
    log.info("Iniciando Xvfb %s (%sx24)", DISPLAY_NUM, SCREEN)
    proc = subprocess.Popen(
        ["Xvfb", DISPLAY_NUM, "-screen", "0", f"{SCREEN}x24", "-nolisten", "tcp", "-ac"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    if not wait_for_display(DISPLAY_NUM):
        proc.kill()
        raise RuntimeError("Xvfb no arrancó")
    return proc


# --------------------------------------------------------------------------- PulseAudio
def setup_pulseaudio():
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    if sh(["pulseaudio", "--check"]).returncode != 0:
        log.info("Iniciando PulseAudio")
        r = sh(["pulseaudio", "--start", "--exit-idle-time=-1"])
        if r.returncode != 0:
            raise RuntimeError(f"PulseAudio no arrancó: {r.stderr.strip()}")
        time.sleep(1)
    # Limpia restos de ejecuciones anteriores para no acumular sinks (meet_sink.2, ...)
    for line in sh(["pactl", "list", "short", "modules"]).stdout.splitlines():
        if any(k in line for k in (f"sink_name={SINK_NAME}", f"sink_name={MIC_SINK_NAME}", f"source_name={VIRT_MIC_NAME}")):
            sh(["pactl", "unload-module", line.split("	")[0]])
    modules = []
    for name in (SINK_NAME, MIC_SINK_NAME):
        r = sh(["pactl", "load-module", "module-null-sink", f"sink_name={name}",
                f"sink_properties=device.description={name}"], check=True)
        modules.append(r.stdout.strip())
    # Chromium ignora las fuentes "monitor", así que creamos un micrófono virtual real
    # (silencioso) a partir del monitor de mic_sink con module-remap-source.
    r = sh(["pactl", "load-module", "module-remap-source", f"source_name={VIRT_MIC_NAME}",
            f"master={MIC_SINK_NAME}.monitor", "source_properties=device.description=VirtualMic"], check=True)
    modules.append(r.stdout.strip())
    sh(["pactl", "set-default-sink", SINK_NAME])
    sh(["pactl", "set-default-source", VIRT_MIC_NAME])
    # Chromium hereda estas variables: su audio va a meet_sink y su micro es virtmic
    os.environ["PULSE_SINK"] = SINK_NAME
    os.environ["PULSE_SOURCE"] = VIRT_MIC_NAME
    log.info("PulseAudio listo: sink=%s (grabación desde %s.monitor)", SINK_NAME, SINK_NAME)
    return modules


def teardown_pulseaudio(modules):
    for m in modules:
        sh(["pactl", "unload-module", m])


# --------------------------------------------------------------------------- ffmpeg
def start_ffmpeg(tmpfile):
    """Graba en MPEG-TS (robusto ante cortes de luz y compatible con el encoder HW); al terminar se remuxea a MP4."""
    if VIDEO_ENCODER == "h264_v4l2m2m" and os.path.exists("/dev/video11"):
        venc = ["-c:v", "h264_v4l2m2m", "-b:v", "3M", "-pix_fmt", "yuv420p"]
    else:
        venc = ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-crf", "28", "-pix_fmt", "yuv420p", "-threads", "3"]
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y",
        "-thread_queue_size", "1024",
        "-f", "x11grab", "-video_size", SCREEN, "-framerate", str(FPS), "-draw_mouse", "0",
        "-i", f"{DISPLAY_NUM}.0",
        "-thread_queue_size", "1024",
        "-f", "pulse", "-i", f"{SINK_NAME}.monitor",
        *venc, "-g", str(FPS * 2),
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        # MKV con volcado inmediato y clústeres cortos: ante un corte de luz se pierde ~1 s, no el vídeo
        "-f", "mpegts", "-flush_packets", "1",
        str(tmpfile),
    ]
    log.info("Iniciando ffmpeg (%s) -> %s", venc[1], tmpfile)
    errlog = open(LOG_DIR / f"ffmpeg_{STAMP}.log", "w")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=errlog,
                            env={**os.environ, "DISPLAY": DISPLAY_NUM}, start_new_session=True)
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError(f"ffmpeg terminó inmediatamente (código {proc.returncode}); mira {errlog.name}")
    return proc


def finish_recording(tmpfile, outfile):
    """Convierte el TS/MKV en MP4 (sin recodificar). Si falla, se conserva el original."""
    if not tmpfile.exists():
        return None
    r = sh(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(tmpfile),
            "-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", str(outfile)])
    if r.returncode == 0 and outfile.exists() and outfile.stat().st_size > 0:
        tmpfile.unlink()
        return outfile
    log.warning("No pude convertir a MP4 (%s); conservo %s", r.stderr.strip()[:200], tmpfile.name)
    return tmpfile


def stop_ffmpeg(proc):
    if proc is None or proc.poll() is not None:
        return
    log.info("Enviando SIGINT a ffmpeg para cerrar la grabación correctamente")
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        log.warning("ffmpeg no terminó en 60 s, enviando SIGTERM")
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


# --------------------------------------------------------------------------- Chromium / Selenium
def clean_stale_profile_locks():
    if sh(["pgrep", "-f", f"user-data-dir={PROFILE_DIR}"]).returncode == 0:
        raise RuntimeError(f"Ya hay un Chromium usando el perfil {PROFILE_DIR}")
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        p = PROFILE_DIR / name
        if p.is_symlink() or p.exists():
            p.unlink(missing_ok=True)


def start_browser():
    clean_stale_profile_locks()
    w, h = SCREEN.split("x")
    opts = Options()
    opts.binary_location = CHROMIUM_BIN
    for flag in (
        f"--user-data-dir={PROFILE_DIR}",
        "--use-fake-ui-for-media-stream",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        # OJO: no usar SwiftShader (--use-angle=swiftshader) en armhf: el gpu-process muere por "alignment exception"
        "--disable-gpu",
        # Sin "inicio de sesión en el navegador" (DICE): si falla, Chromium cierra la sesión web al arrancar
        "--allow-browser-signin=false",
        "--password-store=basic",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-position=0,0",
        f"--window-size={w},{h}",
        "--kiosk",
        "--autoplay-policy=no-user-gesture-required",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=Translate,MediaRouter,OptimizationHints",
        "--disable-infobars",
        "--lang=es",
    ):
        opts.add_argument(flag)
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL", "performance": "ALL"})
    opts.add_experimental_option("prefs", {
        "profile.default_content_setting_values.media_stream_mic": 1,
        "profile.default_content_setting_values.media_stream_camera": 1,
        "profile.default_content_setting_values.notifications": 2,
    })
    os.environ["DISPLAY"] = DISPLAY_NUM
    service = Service(CHROMEDRIVER_BIN, log_path=str(LOG_DIR / f"chromedriver_{STAMP}.log"))
    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(90)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": JS_ON_NEW_DOCUMENT})
    return driver


JS_ON_NEW_DOCUMENT = r"""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
// Guarda todas las RTCPeerConnection que cree Meet para poder diagnosticarlas
(() => {
  const Orig = window.RTCPeerConnection;
  if (!Orig) return;
  window.__pcs = [];
  const Wrapped = function (...args) { const pc = new Orig(...args); window.__pcs.push(pc); return pc; };
  Wrapped.prototype = Orig.prototype;
  Object.setPrototypeOf(Wrapped, Orig);
  window.RTCPeerConnection = Wrapped;
})();
"""

JS_RTC_STATS = r"""
const done = arguments[0];
(async () => {
  const out = [];
  for (const pc of (window.__pcs || [])) {
    const info = {sig: pc.signalingState, ice: pc.iceConnectionState, gath: pc.iceGatheringState,
                  conn: pc.connectionState, hasLocal: !!pc.localDescription, hasRemote: !!pc.remoteDescription,
                  pairs: [], local: {}, remote: {}, transport: []};
    try {
      const st = await pc.getStats();
      st.forEach(r => {
        if (r.type === 'candidate-pair') info.pairs.push([r.state, r.nominated ? 'nom' : '', r.localCandidateId, r.remoteCandidateId, r.requestsSent|0, r.responsesReceived|0]);
        if (r.type === 'local-candidate') info.local[r.id] = r.candidateType + '/' + r.protocol + '/' + (r.address || r.ip || '?') + ':' + r.port;
        if (r.type === 'remote-candidate') info.remote[r.id] = r.candidateType + '/' + r.protocol + '/' + (r.address || r.ip || '?') + ':' + r.port;
        if (r.type === 'transport') info.transport.push([r.dtlsState, r.iceState, r.selectedCandidatePairId]);
      });
    } catch (e) { info.statsErr = String(e); }
    out.push(info);
  }
  done(out);
})();
"""

JS_CLICK_BUTTON = r"""
const re = new RegExp(arguments[0], 'i');
for (const b of document.querySelectorAll('button, [role=button]')) {
  const txt = (b.innerText || '') + ' ' + (b.getAttribute('aria-label') || '');
  if (re.test(txt) && b.offsetParent !== null) { b.click(); return txt.trim(); }
}
return null;
"""

JS_PARTICIPANTS = r"""
// 1) Insignia numérica junto al botón de "Personas"
const badge = document.querySelector('div.uGOf1d');
if (badge && /^\d+$/.test(badge.textContent.trim())) return parseInt(badge.textContent.trim());
// 2) Cualquier aria-label del tipo "12 participantes" / "12 people"
for (const el of document.querySelectorAll('[aria-label]')) {
  const m = el.getAttribute('aria-label').match(/(\d+)\s*(participant|persona|people|asistente|contact)/i);
  if (m) return parseInt(m[1]);
}
// 3) Tiles de vídeo visibles
const tiles = document.querySelectorAll('[data-participant-id]');
if (tiles.length) return tiles.length;
return -1;
"""

JS_INBOUND_VIDEO = r"""
// Estadísticas del vídeo entrante (suma de todos los streams de vídeo recibidos)
const done = arguments[0];
(async () => {
  const out = {frames: 0, lost: 0, recv: 0, freezes: 0, jitter: 0, streams: 0, w: 0, h: 0, ice: ''};
  for (const pc of (window.__pcs || [])) {
    if (pc.connectionState === 'closed') continue;
    out.ice += pc.iceConnectionState + ' ';
    try {
      const st = await pc.getStats();
      st.forEach(r => {
        if (r.type === 'inbound-rtp' && r.kind === 'video') {
          out.streams++; out.frames += r.framesDecoded || 0; out.lost += r.packetsLost || 0;
          out.recv += r.packetsReceived || 0; out.freezes += r.freezeCount || 0;
          out.jitter = Math.max(out.jitter, r.jitter || 0);
          if ((r.frameWidth || 0) > out.w) { out.w = r.frameWidth; out.h = r.frameHeight; }
        }
      });
    } catch (e) {}
  }
  done(out);
})();
"""

JS_IN_CALL = r"""
const re = /leave call|salir de la llamada|abandonar la llamada|end call/i;
for (const b of document.querySelectorAll('button')) {
  if (re.test(b.getAttribute('aria-label') || '')) return true;
}
return document.querySelectorAll('[data-participant-id]').length > 0;
"""

JS_MEETING_OVER = r"""
const t = document.body.innerText.replace(/\n/g, ' ');
return /you'?ve been removed|te han quitado|has salido de la llamada|you left the meeting|the meeting has ended|la reuni[oó]n ha terminado|return to home screen|volver a la pantalla de inicio|the call ended|la llamada ha finalizado/i.test(t);
"""

JS_OPEN_CHAT = r"""
const visible = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
for (const b of document.querySelectorAll('button[aria-label], [role="button"][aria-label]')) {
  const label = b.getAttribute('aria-label') || '';
  if (visible(b) && /(chat|mensaje|message)/i.test(label) &&
      !/(cerrar|close|ocultar|hide)/i.test(label)) {
    b.click();
    return true;
  }
}
return false;
"""

JS_CHAT_PANEL = r"""
const visible = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
const inputs = [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')];
for (const input of inputs) {
  if (!visible(input)) continue;
  const hint = [input.getAttribute('aria-label'), input.getAttribute('placeholder'),
                input.getAttribute('data-placeholder')].filter(Boolean).join(' ');
  if (!/(chat|mensaje|message)/i.test(hint)) continue;
  let panel = null;
  for (let p = input.parentElement; p && p !== document.body; p = p.parentElement) {
    const box = p.getBoundingClientRect();
    if (box.width >= 200 && box.width < innerWidth * 0.65 && box.height >= 180)
      panel = p;
  }
  if (panel) return {text: panel.innerText, input};
}
return null;
"""

def dump_diagnostics(driver, tag):
    """Vuelca consola del navegador, texto visible y captura para depurar la UI de Meet."""
    try:
        for entry in driver.get_log("browser")[-60:]:
            if entry.get("level") in ("SEVERE", "WARNING"):
                log.info("BROWSER[%s] %s", entry.get("level"), str(entry.get("message"))[-700:])
    except Exception as e:  # noqa: BLE001
        log.debug("sin logs de navegador: %s", e)
    try:
        import json as _json
        reqs, bad = {}, []
        for entry in driver.get_log("performance"):
            msg = _json.loads(entry["message"])["message"]
            m, p = msg.get("method"), msg.get("params", {})
            if m == "Network.requestWillBeSent":
                reqs[p["requestId"]] = p["request"]["url"]
            elif m == "Network.responseReceived" and p["response"]["status"] >= 400:
                bad.append((p["response"]["status"], p["response"]["url"][:160]))
            elif m == "Network.loadingFailed":
                bad.append((p.get("errorText"), p.get("blockedReason"), reqs.get(p["requestId"], "?")[:160]))
            elif m == "Network.webSocketFrameError":
                bad.append(("WS-ERR", p.get("errorMessage")))
        log.info("RED: %d peticiones, fallidas/erróneas: %s", len(reqs), bad[-25:])
    except Exception as e:  # noqa: BLE001
        log.info("sin log de red: %s", e)
    text = js(driver, "return document.body.innerText") or ""
    log.info("TEXTO VISIBLE: %r", " | ".join(t for t in text.splitlines() if t.strip())[:800])
    labels = js(driver, r"return [...document.querySelectorAll('[aria-label]')].map(e=>e.getAttribute('aria-label')).filter(l=>/\d/.test(l))") or []
    log.info("ARIA-LABELS con números: %s", labels[:40])
    try:
        driver.set_script_timeout(20)
        for i, pc in enumerate(driver.execute_async_script(JS_RTC_STATS) or []):
            log.info("RTC[%d] sig=%s ice=%s gath=%s conn=%s local=%s remote=%s transport=%s",
                     i, pc["sig"], pc["ice"], pc["gath"], pc["conn"], pc["hasLocal"], pc["hasRemote"], pc["transport"])
            for pair in pc["pairs"][:12]:
                log.info("RTC[%d]   pair %s %s  L=%s  R=%s  req=%s resp=%s", i, pair[0], pair[1],
                         pc["local"].get(pair[2], pair[2]), pc["remote"].get(pair[3], pair[3]), pair[4], pair[5])
    except Exception as e:  # noqa: BLE001
        log.info("RTC stats no disponibles: %s", e)
    try:
        driver.save_screenshot(str(LOG_DIR / f"diag_{tag}_{STAMP}.png"))
    except Exception:  # noqa: BLE001
        pass


JS_CLICK_TOOLBAR_MORE = r"""
// Botón "Más opciones" de la barra inferior (el más bajo en pantalla; los mosaicos tienen otros iguales)
const re = /^(más opciones|more options)$/i;
let best = null;
for (const b of document.querySelectorAll('button[aria-label]')) {
  if (re.test(b.getAttribute('aria-label').trim()) && b.offsetParent !== null) {
    const y = b.getBoundingClientRect().top;
    if (!best || y > best.y) best = {b, y};
  }
}
if (best) { best.b.click(); return true; }
return false;
"""

JS_CLICK_MENU_ITEM = r"""
const re = new RegExp(arguments[0], 'i');
for (const el of document.querySelectorAll('[role=menuitem], [role=menuitemradio], [role=radio], label, span')) {
  if (re.test((el.innerText || '').trim()) && el.offsetParent !== null) { el.click(); return el.innerText.trim(); }
}
return null;
"""


def set_layout_spotlight(driver):
    """Layout 'Destacado' (solo el que habla/presenta): Chromium decodifica 1 vídeo en vez de N mosaicos."""
    try:
        if not js(driver, JS_CLICK_TOOLBAR_MORE):
            log.warning("Layout: no encontré 'Más opciones'")
            return
        time.sleep(1.5)
        if not js(driver, JS_CLICK_MENU_ITEM, r"^(ajustar vista|cambiar diseño|change layout|adjust view)"):
            log.warning("Layout: no encontré 'Cambiar diseño'")
            driver.find_element("tag name", "body").send_keys("\ue00c")  # Escape
            return
        time.sleep(1.5)
        chosen = js(driver, JS_CLICK_MENU_ITEM, r"^(foco|destacado|spotlight)$")
        time.sleep(1)
        driver.find_element("tag name", "body").send_keys("\ue00c")  # cerrar el diálogo
        log.info("Layout: %s", chosen or "no encontré 'Destacado'")
    except Exception as e:  # noqa: BLE001
        log.warning("Layout: %s", e)


def js(driver, script, *args):
    try:
        return driver.execute_script(script, *args)
    except WebDriverException as e:
        log.debug("JS error: %s", e)
        return None


def click_button(driver, pattern):
    return js(driver, JS_CLICK_BUTTON, pattern)


def dismiss_popups(driver):
    for pat in (r"^(entendido|got it|ok|aceptar|cerrar|dismiss|no, gracias|no thanks)$",
                r"continuar sin (micr[oó]fono|c[aá]mara)|continue without (microphone|camera)",
                r"usar el micr[oó]fono|use microphone|permitir|allow"):
        if click_button(driver, pat):
            time.sleep(1)


def mute_devices(driver):
    for pat in (r"turn off microphone|desactivar (el )?micr[oó]fono",
                r"turn off camera|desactivar (la )?c[aá]mara"):
        if click_button(driver, pat):
            time.sleep(0.5)


def join_meeting(driver):
    log.info("Abriendo %s", MEET_URL)
    driver.get(MEET_URL)
    time.sleep(8)
    if "accounts.google.com" in driver.current_url:
        raise RuntimeError("Google pide iniciar sesión: ejecuta docker compose run --rm --service-ports login")
    dismiss_popups(driver)
    mute_devices(driver)
    dismiss_popups(driver)

    join_pat = r"unir(me|se|te) (ahora|igualmente)|join now|(pedir|solicitar) unir(me|se|te)|ask to join|participar ahora|join anyway"
    clicked = None
    for _ in range(12):  # hasta ~60 s para que aparezca el botón
        if js(driver, JS_IN_CALL):
            clicked = "(ya dentro, sin sala de espera)"
            break
        clicked = click_button(driver, join_pat)
        if clicked:
            break
        dismiss_popups(driver)
        time.sleep(5)
    if not clicked:
        driver.save_screenshot(str(LOG_DIR / f"no_join_button_{STAMP}.png"))
        raise RuntimeError("No encontré el botón de unirse (captura en logs/)")
    log.info("Pulsado botón: %r", clicked)

    t0 = time.time()
    while time.time() - t0 < JOIN_TIMEOUT and not STOP_REQUESTED:
        if js(driver, JS_IN_CALL):
            log.info("Dentro de la reunión")
            time.sleep(3)
            dismiss_popups(driver)
            dump_diagnostics(driver, "joined")
            return
        if js(driver, JS_MEETING_OVER):
            raise RuntimeError("La reunión terminó/denegaron el acceso antes de entrar")
        time.sleep(5)
    if STOP_REQUESTED:
        raise RuntimeError("Parada solicitada durante la espera de admisión")
    driver.save_screenshot(str(LOG_DIR / f"join_timeout_{STAMP}.png"))
    raise RuntimeError(f"No fui admitido en {JOIN_TIMEOUT} s")


def keep_chat_open(driver):
    """Abre el chat una vez y lo deja visible durante la grabación."""
    if js(driver, JS_CHAT_PANEL):
        return True
    if js(driver, JS_OPEN_CHAT):
        time.sleep(0.3)
        if js(driver, JS_CHAT_PANEL):
            log.info("Chat abierto para vigilar la toma de lista")
            return True
    log.warning("No pude abrir el chat; reintentaré")
    return False


def check_attendance_chat(driver, panel=None):
    """Envía el DNI una sola vez cuando el chat contiene dos DNIs distintos."""
    if not ATTENDANCE_DNI:
        return False
    try:
        panel = panel or js(driver, JS_CHAT_PANEL)
        if not panel:
            log.debug("No pude leer el panel de chat")
            return False
        numbers = {line.strip() for line in panel["text"].splitlines()
                   if re.fullmatch(r"\d{7,9}", line.strip())}
        if ATTENDANCE_DNI in numbers:
            log.info("El DNI propio ya figura en el chat; no lo repito")
            return True
        if len(numbers) < 2:
            return False
        editor = panel["input"]
        editor.click()
        editor.send_keys(ATTENDANCE_DNI, Keys.ENTER)
        log.info("DNI enviado al chat para la toma de lista")
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("No pude revisar o enviar el DNI en el chat: %s", exc)
        return False


def monitor_meeting(driver):
    peak = 0
    started = False
    end_hits = 0
    not_in_call = 0
    unknown = 0
    last_frames = 0
    attendance_sent = False
    last_chat_check = 0
    last_chat_open_attempt = 0
    joined_at = time.time()
    deadline = joined_at + MAX_MINUTES * 60
    if STOP_TIME:
        try:
            stop_hour, stop_minute = map(int, STOP_TIME.split(":"))
            if not (0 <= stop_hour <= 23 and 0 <= stop_minute <= 59):
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"STOP_TIME inválido: {STOP_TIME!r}; usar HH:MM") from exc
        local_now = dt.datetime.now().astimezone()
        stop_at = local_now.replace(hour=stop_hour, minute=stop_minute, second=0, microsecond=0)
        deadline = min(deadline, stop_at.timestamp())

    if ATTENDANCE_DNI:
        keep_chat_open(driver)
        last_chat_open_attempt = time.time()

    while not STOP_REQUESTED:
        now = time.time()
        if now >= deadline:
            log.warning("Alcanzado el límite de grabación (%s); finalizando",
                        STOP_TIME or f"{MAX_MINUTES} min")
            break
        if not started and now > joined_at + NO_START_MINUTES * 60:
            log.warning("La reunión no ha empezado en %d min; me retiro", NO_START_MINUTES)
            break
        if js(driver, JS_MEETING_OVER) or not js(driver, JS_IN_CALL):
            not_in_call += 1
            if not_in_call >= 3:
                log.info("Ya no estoy en la llamada (expulsado / reunión finalizada)")
                break
        else:
            not_in_call = 0

        count = js(driver, JS_PARTICIPANTS)
        count = -1 if count is None else int(count)
        if count > 0:
            peak = max(peak, count)
            if not started and count >= 2:
                started = True
                log.info("Reunión iniciada (%d asistentes)", count)
        elapsed = int(now - joined_at)
        log.info("asistentes=%s pico=%d iniciada=%s t=%ds", count if count > 0 else "?", peak, started, elapsed)
        # Vídeo entrante: si los fotogramas dejan de avanzar con la conexión viva, el emisor/red se ha parado
        try:
            driver.set_script_timeout(10)
            v = driver.execute_async_script(JS_INBOUND_VIDEO) or {}
            delta = v.get("frames", 0) - last_frames
            log.info("video-in: streams=%d %dx%d frames=+%d perdidos=%d/%d jitter=%.0fms freezes=%d ice=%s",
                     v.get("streams", 0), v.get("w", 0), v.get("h", 0), delta, v.get("lost", 0), v.get("recv", 0),
                     v.get("jitter", 0) * 1000, v.get("freezes", 0), v.get("ice", "").strip())
            if started and v.get("streams", 0) and delta == 0 and last_frames > 0:
                log.warning("video-in: sin fotogramas nuevos en este intervalo (emisor parado o pérdida de red)")
            last_frames = v.get("frames", 0)
        except Exception as e:  # noqa: BLE001
            log.debug("video-in stats: %s", e)
        if count <= 0:
            unknown += 1
            if unknown in (3, 30):  # diagnóstico a los ~30 s y ~5 min sin lectura
                dump_diagnostics(driver, f"unknown{unknown}")
        else:
            unknown = 0

        if started and count == 1:
            end_hits += 1
            log.info("Solo queda el bot (%d/%d)", end_hits, END_CONFIRMATIONS)
            if end_hits >= END_CONFIRMATIONS:
                log.info("Solo queda el bot; finalizando la grabación")
                break
        else:
            end_hits = 0

        dismiss_popups(driver)
        for _ in range(CHECK_INTERVAL * 2):
            if STOP_REQUESTED:
                break
            if ATTENDANCE_DNI:
                chat_now = time.time()
                interval = 1 if not attendance_sent else 10
                if chat_now - last_chat_check >= interval:
                    last_chat_check = chat_now
                    panel = js(driver, JS_CHAT_PANEL)
                    if panel:
                        if not attendance_sent:
                            attendance_sent = check_attendance_chat(driver, panel)
                    elif chat_now - last_chat_open_attempt >= 5:
                        last_chat_open_attempt = chat_now
                        keep_chat_open(driver)
            time.sleep(0.5)
    return peak, started


def leave_meeting(driver):
    try:
        if click_button(driver, r"leave call|salir de la llamada|abandonar la llamada|end call"):
            time.sleep(2)
            click_button(driver, r"^(just leave|salir|solo salir|leave)$")
            time.sleep(1)
            log.info("Llamada cerrada")
    except Exception as e:  # noqa: BLE001
        log.warning("No pude pulsar 'salir': %s", e)


# --------------------------------------------------------------------------- main
def main():
    if not MEET_URL:
        print(__doc__)
        sys.exit(2)
    if "meet.google.com" not in MEET_URL:
        log.warning("La URL no parece de Google Meet: %s", MEET_URL)

    outfile = OUT_DIR / f"meet_{STAMP}.mp4"
    tmpfile = OUT_DIR / f"meet_{STAMP}.ts"
    # Grabaciones interrumpidas (corte de luz/reinicio) de ejecuciones anteriores: convertir lo que se salvó
    for orphan in sorted(list(OUT_DIR.glob("meet_*.ts")) + list(OUT_DIR.glob("meet_*.mkv"))):
        log.warning("Recuperando grabación interrumpida: %s", orphan.name)
        finish_recording(orphan, orphan.with_suffix(".mp4"))
    xvfb = ffmpeg = driver = None
    pulse_modules = []
    exit_code = 0
    peak = 0
    started = False
    error = None
    try:
        xvfb = start_xvfb()
        pulse_modules = setup_pulseaudio()
        driver = start_browser()
        ffmpeg = start_ffmpeg(tmpfile)
        join_meeting(driver)
        set_layout_spotlight(driver)
        peak, started = monitor_meeting(driver)
        log.info("Pico de asistentes: %d", peak)
        leave_meeting(driver)
    except Exception as e:  # noqa: BLE001
        exit_code = 1
        error = str(e)
        log.error("ERROR: %s", e, exc_info=True)
        if driver is not None:
            try:
                driver.save_screenshot(str(LOG_DIR / f"error_{STAMP}.png"))
            except Exception:  # noqa: BLE001
                pass
    finally:
        stop_ffmpeg(ffmpeg)
        if not error and not started and tmpfile.exists():
            log.warning("La reunión nunca empezó: descarto el vídeo de la sala vacía (%s)", tmpfile.name)
            tmpfile.unlink()
        final = finish_recording(tmpfile, outfile)
        if driver is not None:
            try:
                driver.quit()
            except Exception:  # noqa: BLE001
                pass
        sh(["pkill", "-f", f"user-data-dir={PROFILE_DIR}"])
        teardown_pulseaudio(pulse_modules)
        if xvfb is not None:
            xvfb.terminate()
            try:
                xvfb.wait(timeout=10)
            except subprocess.TimeoutExpired:
                xvfb.kill()
        size_mb = final.stat().st_size / 1e6 if final else 0
        if final:
            log.info("Grabación guardada: %s (%.1f MB)", final, size_mb)
        log.info("Fin (código %d)", exit_code)
        if error and "iniciar sesión" in error:
            notify("Meet bot: SESIÓN PERDIDA", f"No pude grabar {MEET_URL}: Google pide login. "
                   "Repite el inicio de sesión con docker compose run --rm --service-ports login.", "high")
        elif error:
            notify("Meet bot: ERROR", f"{MEET_URL}\n{error[:300]}", "high")
        elif not started:
            notify("Meet bot: sin reunión", f"Nadie entró a {MEET_URL} en {NO_START_MINUTES} min; no se grabó nada.")
        else:
            notify("Meet bot: grabación guardada",
                   f"{final.name if final else '(sin archivo)'} ({size_mb:.0f} MB, pico {peak} asistentes)", "low")
    sys.exit(exit_code)


def notify(title, message, priority="default"):
    """Aviso por ntfy/Telegram si las variables correspondientes están configuradas."""
    try:
        import notify as _notify  # noqa: PLC0415
        _notify.send(title, message, priority)
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo enviar el aviso: %s", e)


if __name__ == "__main__":
    main()
