#!/usr/bin/env powershell
param(
    [string]$Token = ""
)

$ErrorActionPreference = "Continue"
$WarningPreference = "SilentlyContinue"

Write-Output "Iniciando push a GitHub..."
Set-Location "F:\PRESENTABLE"

# Forzar HTTPS y evitar prompts
git config --global credential.helper store 2>&1 | Out-Null
git config --global user.email "cesar@example.com" 2>&1
git config --global user.name "Cesar Pantoja" 2>&1

# Limpiar .git anterior si existe
if (Test-Path ".\.git") {
    Remove-Item ".\.git" -Recurse -Force -ErrorAction SilentlyContinue
    Write-Output "Repositorio anterior limpiado"
}

# Inicializar repositorio
git init -b main
git add -A
git commit -m "Initial snapshot: BESSER + modeling-agent + setup scripts"

# Configurar remoto
git remote add origin "https://github.com/CesarPantoja1/CC_BESSER.git"

# Hacer push
Write-Output "Ejecutando push..."
git push -u origin main --force --no-verify

Write-Output "Completado"
