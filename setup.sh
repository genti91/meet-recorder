#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -e .env ]; then
  cp .env.example .env
  printf '%s\n' 'Creado .env. Editá MEET_URL, horario y zona horaria.'
else
  printf '%s\n' '.env ya existe; no se modificó.'
fi
if ! command -v docker >/dev/null 2>&1; then
  printf '%s\n' 'Instalá e iniciá Docker Engine y el plugin Compose antes de continuar.'
else
  printf '%s\n' 'Después de editar .env: docker compose run --rm --service-ports login'
  printf '%s\n' 'Luego: docker compose up -d --build recorder player'
fi
