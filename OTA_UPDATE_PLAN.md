# Plan de implementación: OTA Updates vía GitHub Releases

## Visión general

Cada Raspberry Pi corre la app Flask como servicio systemd. Periódicamente consulta
GitHub para detectar si hay una versión nueva publicada manualmente como Release.
Si la hay, notifica al usuario en la UI. El usuario decide cuándo actualizar con un
botón, y la app se actualiza y reinicia sola.

---

## Estructura de archivos nuevos/modificados

```
Access Point/
  version.json                   ← versión local instalada
  backend/
    update_manager.py            ← módulo nuevo: polling, descarga, instalación
    app.py                       ← agregar endpoints /api/update/*
  frontend/
    index.html                   ← agregar badge/banner de actualización
    app.js                       ← lógica de notificación y botón actualizar
    style.css                    ← estilos del banner de actualización
```

---

## Paso 1 — Versionado local (`version.json`)

Archivo en la raíz del proyecto, commiteado en el repo:

```json
{
  "version": "1.0.0",
  "release_date": "2026-04-04",
  "release_notes": "Versión inicial"
}
```

- La Raspberry lee este archivo para saber qué versión tiene instalada.
- Cada vez que se publica un Release en GitHub, se actualiza este archivo en el código.
- Tras una actualización exitosa, el archivo local queda con la versión nueva.

---

## Paso 2 — GitHub Release (proceso manual)

Cada nueva versión se publica así:

1. Actualizar `version.json` con la nueva versión y release notes
2. Hacer commit y push al repo
3. En GitHub → Releases → "Draft a new release"
4. Tag: `v1.0.1` (debe coincidir con el campo `version` del JSON)
5. Título y descripción del release
6. GitHub genera automáticamente el ZIP del código fuente

La Raspberry va a descargar el ZIP del release más reciente.

---

## Paso 3 — `update_manager.py` (módulo nuevo)

Responsabilidades:
- Leer versión local desde `version.json`
- Consultar GitHub API para obtener el release más reciente
- Comparar versiones (semver simple: mayor.menor.patch)
- Descargar y aplicar la actualización
- Hacer backup antes de actualizar y rollback si algo falla

### 3.1 — Polling de versión

```python
GITHUB_REPO = "owner/rpi-network-setup"   # a definir
CHECK_INTERVAL_HOURS = 6
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
```

- Corre en un thread daemon al arrancar el servidor Flask
- Cada 6 horas consulta la API (o al arrancar si nunca chequeó)
- Guarda el resultado en memoria: `{ "update_available": bool, "latest_version": str, "release_notes": str, "download_url": str, "checked_at": timestamp }`
- Si GitHub no responde, no hace nada (no bloquea el servicio)

### 3.2 — Comparación de versiones

Comparación simple de strings semver:
- `"1.0.1" > "1.0.0"` → hay actualización
- Se ignoran pre-releases (tags con `-beta`, `-rc`, etc.)

### 3.3 — Descarga e instalación

Al recibir la orden de actualizar:

1. **Backup**: copiar carpeta actual a `/home/admin/rpi-setup-backup/`
2. **Descarga**: GET al `zipball_url` del release → `/tmp/rpi-update.zip`
3. **Descompresión**: extraer en `/tmp/rpi-update-extracted/`
4. **Reemplazo**: copiar `frontend/` y `backend/` sobre la instalación actual
   - NO tocar `server_config.json` (tiene IP fallback configurada)
   - NO tocar el `venv/` de Python
5. **Restart**: `sudo systemctl restart rpi-setup`
6. **Verificación**: esperar 10s y verificar que el servicio levantó
7. **Rollback**: si el servicio no levanta, restaurar backup y reiniciar

### 3.4 — Estados del proceso

```
idle → checking → update_available → downloading → installing → restarting → done
                                                                            → error → rolled_back
```

---

## Paso 4 — Endpoints en `app.py`

```
GET  /api/update/status      → versión local, última versión, si hay update, última vez chequeado
POST /api/update/check       → forzar check inmediato (botón "buscar actualizaciones")
POST /api/update/install     → iniciar instalación en background
GET  /api/update/progress    → estado del proceso de instalación (para polling desde UI)
```

---

## Paso 5 — UI

### Badge en el header

Cuando hay actualización disponible, aparece un badge naranja junto al nombre de la app:

```
Infotek Ingeniería  [↑ v1.0.1]
```

Al hacer click abre un modal con:
- Versión instalada actual
- Versión nueva disponible
- Release notes
- Botón "Actualizar ahora"
- Botón "Recordar después"

### Banner en el Dashboard

Adicionalmente, en la sección Dashboard aparece un banner informativo:

```
┌─────────────────────────────────────────────────────┐
│  Nueva versión disponible: v1.0.1                   │
│  "Mejoras en escaneo WiFi y fixes de estabilidad"   │
│                          [Ver detalles] [Actualizar] │
└─────────────────────────────────────────────────────┘
```

### Pantalla de progreso de actualización

Al confirmar la actualización, la UI muestra pasos en tiempo real:
```
✓ Backup creado
✓ Descargando v1.0.1... (2.3 MB)
✓ Instalando archivos...
⟳ Reiniciando servicio...
```

Cuando el servicio reinicia, la página recarga automáticamente.

---

## Paso 6 — Seguridad y consideraciones

- **Repo público**: sin token necesario, GitHub API tiene rate limit de 60 req/hora
  para IPs sin autenticar — con polling de 6hs es más que suficiente para cualquier
  cantidad de Raspberries.
- **Verificación de integridad**: comparar el SHA del ZIP descargado con el que
  reporta GitHub API (campo `assets[].digest`) antes de instalar.
- **No actualizar el venv**: si cambian dependencias Python, incluir un script
  `post_install.sh` en el release que el update_manager ejecute después de copiar
  los archivos. Por ahora no es necesario.
- **Permiso sudo para restart**: el servicio ya corre como root (systemd), así que
  el restart no necesita configuración extra de sudoers.

---

## Orden de implementación

| # | Tarea | Archivo | Complejidad |
|---|-------|---------|-------------|
| 1 | Crear `version.json` | raíz | Trivial |
| 2 | Crear repo en GitHub y primer Release | GitHub | Manual |
| 3 | Implementar `update_manager.py` (polling + comparación) | backend | Media |
| 4 | Agregar endpoints `/api/update/*` en `app.py` | backend | Baja |
| 5 | Implementar descarga + instalación + rollback | backend | Alta |
| 6 | Badge en header + modal de detalles | frontend | Media |
| 7 | Banner en Dashboard | frontend | Baja |
| 8 | Pantalla de progreso con polling | frontend | Media |
| 9 | Prueba end-to-end con release real | - | - |

---

## Lo que NO hace este sistema

- No actualiza automáticamente sin confirmación del usuario
- No maneja múltiples branches (solo `latest` release)
- No actualiza dependencias Python del venv automáticamente
- No soporta rollback manual desde la UI (solo automático si el servicio no levanta)

---

*Documento generado: 2026-04-04*
