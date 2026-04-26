# Guía completa de configuración y ejecución de CC-SDD

Este documento resume todo lo necesario para que el aplicativo de CC-SDD funcione en otro equipo (instalación, configuración, permisos y solución de errores).

## 1) Objetivo del stack

Al ejecutar el proyecto se levantan 4 servicios:

1. **Backend BESSER (FastAPI)** en `http://localhost:9000/besser_api/docs`
2. **Frontend Editor** en `http://localhost:8080`
3. **Modeling Agent (WebSocket)** en `ws://localhost:8765`
4. **Gemini Service (WebSocket)** en `ws://localhost:9001`

El servicio de Gemini trabaja sobre la carpeta `sdd-workspace`, donde crea `.kiro/specs/*` y otros archivos de especificación.

---

## 2) Requisitos previos

En Windows (PowerShell):

- Python 3.11+ (ideal 3.12)
- Node.js LTS + npm
- Gemini CLI instalado y disponible en `PATH` (`gemini --help`)
- Dependencias Python del repo instaladas en los `venv` correspondientes

Validación rápida:

```powershell
py --version
node --version
npm --version
gemini --help
```

---

## 3) Rutas críticas

La raíz del repo se asume como:

`F:\PRESENTABLE`

Rutas usadas por el arranque:

- `F:\PRESENTABLE\BESSER`
- `F:\PRESENTABLE\modeling-agent`
- `F:\PRESENTABLE\sdd-workspace`
- `F:\PRESENTABLE\gemini_service`

Si tu compañero tiene otra ruta (por ejemplo `C:\Users\User\Desktop\BesserCC2\CC_BESSER`), debe ser consistente y existir `sdd-workspace` dentro del proyecto.

---

## 4) Arranque recomendado

Desde la raíz del repo:

```powershell
cd F:\PRESENTABLE
py .\start.py
```

Esto abre terminales para backend, frontend, modeling-agent y gemini-service.

Luego abrir:

- `http://localhost:8080`

Y usar el flujo CC-SDD desde el botón correspondiente del editor.

---

## 5) Problema reportado: `Unknown arguments: skip-trust, skipTrust`

### Causa

El bridge estaba invocando Gemini con un flag no soportado por la versión actual del CLI:

- `--skip-trust` (inválido en versiones recientes)

### Solución aplicada

Se actualizó `gemini_service/bridge.py` para ejecutar:

- `gemini --yolo -p "..."`

y confiar en:

- `GEMINI_CLI_TRUST_WORKSPACE=true`

para la confianza del workspace.

Con esto desaparece el error de argumentos desconocidos.

---

## 6) Si no permite crear archivos (caso del compañero)

Normalmente no es por permisos de Windows del sistema, sino por uno de estos puntos:

1. **`WORK_DIR` incorrecto**: Gemini está apuntando a una carpeta distinta.
2. **Carpeta no escribible**: ACL de Windows restringida.
3. **CLI en modo restringido/política**: no puede ejecutar herramientas de escritura.
4. **Proceso en otro usuario**: el servicio corre con un usuario distinto al dueño de la carpeta.

### Checklist de verificación

#### A) Confirmar carpeta de trabajo real

En la salida del servicio debe verse algo como:

`Work dir: ...\sdd-workspace`

Esa carpeta debe existir y ser la que esperan.

#### B) Probar escritura manual en `sdd-workspace`

```powershell
cd F:\PRESENTABLE\sdd-workspace
"ok" | Out-File .\write_test.txt -Encoding utf8
Test-Path .\write_test.txt
Remove-Item .\write_test.txt
```

Si esto falla, el problema sí es de permisos del sistema/ACL.

#### C) Verificar permisos ACL (Windows)

```powershell
icacls F:\PRESENTABLE\sdd-workspace
```

Si hace falta, dar permisos de modificación al usuario actual:

```powershell
icacls F:\PRESENTABLE\sdd-workspace /grant "$env:USERNAME:(OI)(CI)M" /T
```

#### D) Revisar que Gemini CLI funcione en la misma terminal

```powershell
cd F:\PRESENTABLE\sdd-workspace
gemini --help
```

---

## 7) Verificación funcional mínima del flujo

1. Ejecutar `py .\start.py`
2. Abrir `http://localhost:8080`
3. Lanzar Discovery con una idea corta
4. Confirmar que no aparece `Unknown arguments: skip-trust`
5. Confirmar que se crean/actualizan archivos bajo:
	- `sdd-workspace\.kiro\specs\...`

---

## 8) Recomendaciones para evitar incidencias entre equipos

- Usar misma versión principal de Gemini CLI en todo el equipo.
- Evitar rutas con permisos corporativos restringidos (OneDrive con políticas, carpetas protegidas).
- Ejecutar todo con el mismo usuario de Windows (no mezclar admin/no-admin por servicio).
- Mantener `sdd-workspace` dentro del repo y no en directorios temporales.

---

## 9) Diagnóstico rápido cuando algo falle

Si vuelve a fallar, recolectar y compartir:

1. Salida completa de la terminal de `Gemini Service`.
2. Valor de `Work dir` mostrado al arrancar.
3. Resultado de:

```powershell
gemini --help
icacls F:\PRESENTABLE\sdd-workspace
```

4. Confirmación de si se puede crear `write_test.txt` manualmente.

Con eso se identifica en minutos si es incompatibilidad de CLI o permisos de carpeta.

---

## 10) Script Python de diagnóstico (recomendado)

Se agregó el script `setup_cc_sdd.py` en la raíz del proyecto para validar rápidamente el entorno.

### Ejecutar diagnóstico

```powershell
cd F:\PRESENTABLE
py .\setup_cc_sdd.py
```

### Ejecutar con raíz personalizada

```powershell
py .\setup_cc_sdd.py --root C:\Users\User\Desktop\BesserCC2\CC_BESSER
```

### Intentar reparación básica de ACL (Windows)

```powershell
py .\setup_cc_sdd.py --fix-acl
```

### Qué valida

- Python
- Node/NPM
- Gemini CLI (`gemini --help`)
- Estructura de carpetas crítica (`BESSER`, `modeling-agent`, `sdd-workspace`, `gemini_service`)
- Escritura real en `sdd-workspace`
- ACL de la carpeta con `icacls`

Si el resumen termina con `FAIL=0`, el entorno está listo para correr CC-SDD.

