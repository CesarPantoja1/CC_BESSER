#!/usr/bin/env powershell
$ErrorActionPreference = "Continue"

Set-Location "F:\PRESENTABLE"

Write-Output "Limpiando repositorios anidados..."
Get-ChildItem -Path "." -Recurse -Directory -Filter ".git" -Force | ForEach-Object {
    Write-Output "Eliminando: $($_.FullName)"
    Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output "Eliminando .git local..."
if (Test-Path ".\.git") {
    Remove-Item ".\.git" -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output "Configurando git..."
git config --global credential.helper store 2>&1 | Out-Null
git config --global user.email "cesar@example.com" 2>&1
git config --global user.name "Cesar Pantoja" 2>&1

Write-Output "Inicializando nuevo repositorio..."
git init -b main
git add -A
git commit -m "Clean snapshot: BESSER + modeling-agent (sin .git anidados)"

Write-Output "Configurando remoto..."
git remote add origin "https://github.com/CesarPantoja1/CC_BESSER.git"

Write-Output "Push a GitHub..."
git push -u origin main --force --no-verify

Write-Output "✓ Push completado exitosamente"
