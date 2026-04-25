#!/usr/bin/env powershell
Set-Location "F:\PRESENTABLE"
git init -b main 2>&1 | Out-Null
git config user.email "cesar@example.com" 2>&1 | Out-Null
git config user.name "Cesar Pantoja" 2>&1 | Out-Null
git add -A 2>&1
git commit -m "Initial snapshot: BESSER + modeling-agent + setup" 2>&1
git remote remove origin 2>&1 | Out-Null
git remote add origin "https://github.com/CesarPantoja1/CC_BESSER.git" 2>&1
git push -u origin main --force 2>&1
Write-Host "✓ Push completado"
