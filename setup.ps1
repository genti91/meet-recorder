$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host 'Creado .env. Abrilo y configurá MEET_URL, horario y zona horaria.'
} else {
    Write-Host '.env ya existe; no se modificó.'
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host 'Instalá e iniciá Docker Desktop antes de continuar.'
} else {
    Write-Host 'Después de editar .env: docker compose run --rm --service-ports login'
    Write-Host 'Luego: docker compose up -d --build recorder player'
}
