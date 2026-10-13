# Meet Recorder

Bot para entrar a una reunión de Google Meet en días y horarios definidos, grabar pantalla y audio en MP4, y salir cuando termina la reunión, queda solo o llega la hora límite. Funciona en **Windows** (Docker Desktop) y **Linux** (Docker Engine + Compose); también puede ejecutarse en una Raspberry Pi de 64 bits con Docker.

El perfil de Google, las grabaciones y los registros se guardan en volúmenes locales de Docker. No se incluyen cuentas, enlaces de reuniones ni documentos personales en este repositorio.

## Requisitos

- Docker Desktop en Windows (con WSL 2), o Docker Engine y el plugin Compose en Linux.
- Una cuenta de Google con acceso al Meet y espacio suficiente para las grabaciones.
- Un navegador para completar el inicio de sesión una sola vez.

## Instalación rápida

1. Cloná este repositorio y entrá en la carpeta:

   ```sh
   git clone https://github.com/genti91/meet-recorder.git
   cd meet-recorder
   ```

2. Creá el archivo privado de configuración:

   **Windows, PowerShell:**

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

   **Linux:**

   ```sh
   sh setup.sh
   ${EDITOR:-nano} .env
   ```

   Cambiá por lo menos `MEET_URL`, `TZ`, `SCHEDULE_DAYS` y `SCHEDULE_TIME`. El ejemplo usa lunes y miércoles a las 06:34 en horario de Buenos Aires. `ATTENDANCE_DNI` es opcional y queda vacío por defecto. Las horas se expresan en formato de 24 horas.

3. Iniciá sesión en Google. Cerrá primero el grabador si ya está funcionando:

   ```sh
   docker compose stop recorder
   docker compose run --rm --service-ports login
   ```

   Esperá a que aparezca el mensaje de inicio de sesión y abrí [http://localhost:6080/vnc.html](http://localhost:6080/vnc.html). En ese escritorio remoto, iniciá sesión con la cuenta que va a entrar al Meet. Cuando termines, cerrá Chromium en el escritorio remoto y esperá a que termine el comando. El puerto de este acceso sólo escucha en tu equipo y se cierra al terminar.

4. Activá el grabador y el reproductor:

   ```sh
   docker compose up -d --build recorder player
   docker compose logs -f recorder
   ```

   El servicio queda esperando el próximo día y horario programados. Para probar una reunión inmediatamente, usá `docker compose run --rm --no-deps recorder python3 /app/auto_meet.py` (entrará al Meet configurado y comenzará a grabar). Detenelo con `Ctrl+C` cuando quieras. No lo pruebes a la vez que el servicio programado esté grabando.

5. Reproducí o descargá las grabaciones en [http://localhost:8765](http://localhost:8765). El reproductor sólo escucha en tu equipo. Los archivos persisten en el volumen Docker `recordings` incluso al recrear los contenedores.

## Configuración

Todas las opciones están en [`.env.example`](.env.example). Editá `.env` y aplicá los cambios con `docker compose up -d --force-recreate recorder player`.

| Variable | Uso |
| --- | --- |
| `MEET_URL` | Enlace completo a un Meet, por ejemplo `https://meet.google.com/abc-defg-hij`. |
| `TZ` | Zona horaria IANA, por ejemplo `America/Argentina/Buenos_Aires`. |
| `SCHEDULE_DAYS` | Días separados por comas: `mon,tue,wed,thu,fri,sat,sun`. |
| `SCHEDULE_TIME` | Hora de entrada, `HH:MM` en la zona `TZ`. |
| `STOP_TIME` | Hora local a la que termina como último recurso; vacío para desactivarla. |
| `MAX_MINUTES` | Límite absoluto desde la entrada. |
| `NO_START_MINUTES` | Espera máxima si nadie más entra. |
| `ATTENDANCE_DNI` | Opcional. Envía **sólo los dígitos** una vez cuando detecta dos DNI distintos en el chat. Deja el chat abierto y lo comprueba cada segundo hasta enviarlo. |
| `SCREEN`, `FPS`, `VIDEO_ENCODER` | Calidad de vídeo. `libx264` es la opción general; `h264_v4l2m2m` se usa sólo en hardware compatible configurado aparte. |
| `NTFY_TOPIC`, `NTFY_SERVER` | Avisos opcionales por ntfy. Usá un tema secreto difícil de adivinar. |
| `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` | Avisos opcionales por Telegram. |

El bot graba desde que intenta entrar. Al terminar, convierte el archivo temporal `.ts` a `.mp4` sin recodificar. Si se corta la energía, intenta recuperar ese archivo temporal al iniciar la siguiente ejecución. Si nadie más entró, descarta la grabación de la sala vacía.

## Comandos útiles

```sh
docker compose ps                    # Estado
docker compose logs -f recorder      # Registro en vivo
docker compose restart recorder      # Reiniciar tras cambiar el entorno externo
docker compose stop recorder         # Pausar próximas grabaciones
docker compose up -d recorder        # Reanudar
docker compose down                  # Parar contenedores, conservar archivos
```

**No uses `docker compose down -v` si querés conservar perfil y grabaciones:** `-v` borra los volúmenes de datos. Para exportar archivos, descargalos desde el reproductor. En Windows, configurá Docker Desktop para que se inicie con el sistema; en Linux, asegurate de que Docker arranque automáticamente.

## Privacidad y límites

`.env` contiene el enlace del Meet y posiblemente identificadores o tokens: está ignorado por Git. El perfil de Chromium contiene la sesión de Google y también debe mantenerse privado. Los logs pueden contener información de la reunión; se guardan en un volumen local, no en Git. El reproductor y el escritorio de inicio de sesión se publican únicamente en `127.0.0.1` del equipo anfitrión. Compartir el reproductor con otros dispositivos requiere configurar una red privada o un proxy con autenticación por separado.

La interfaz de Meet cambia con el tiempo. Revisá una primera grabación y el comportamiento del chat antes de confiar en una sesión importante. Grabá reuniones sólo con la autorización que corresponda.

## Licencia

[MIT](LICENSE).
