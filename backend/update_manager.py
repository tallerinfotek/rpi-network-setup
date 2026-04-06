"""
update_manager.py — Sistema de actualizaciones OTA vía GitHub Releases.

Flujo:
  1. Al arrancar, inicia un thread que consulta GitHub cada CHECK_INTERVAL_HOURS horas.
  2. Compara la versión del último release con la versión local (version.json).
  3. Si hay una versión nueva, lo reporta vía get_update_status().
  4. Al recibir la orden de instalar, descarga el ZIP, hace backup, reemplaza
     los archivos y reinicia el servicio systemd.
  5. Si el servicio no levanta, restaura el backup automáticamente.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

GITHUB_REPO       = "tallerinfotek/rpi-network-setup"
GITHUB_API_URL    = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
CHECK_INTERVAL_H  = 6          # horas entre checks automáticos
RESTART_WAIT_S    = 15         # segundos para verificar que el servicio levantó
BACKUP_DIR        = "/tmp/rpi-setup-backup"
DOWNLOAD_PATH     = "/tmp/rpi-update.zip"
EXTRACT_PATH      = "/tmp/rpi-update-extracted"
SERVICE_NAME      = "rpi-setup"

# Archivos/carpetas que NO se tocan durante la actualización
PRESERVE = {
    "server_config.json",
    "backend/venv",      # Preservar venv (instalado localmente)
    "venv",              # Alternativa si está en raíz
}

# Ruta raíz del proyecto (un nivel arriba de este archivo)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VERSION_FILE = os.path.join(PROJECT_ROOT, "version.json")

# ---------------------------------------------------------------------------
# Estado en memoria
# ---------------------------------------------------------------------------

_state: Dict[str, Any] = {
    "local_version":   None,
    "latest_version":  None,
    "release_notes":   None,
    "download_url":    None,   # zipball_url del release
    "update_available": False,
    "checked_at":      None,
    "status":          "idle",  # idle | checking | downloading | installing | restarting | done | error
    "status_msg":      "",
    "error":           None,
}

_install_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_local_version() -> Optional[str]:
    try:
        with open(VERSION_FILE, "r") as f:
            data = json.load(f)
        return data.get("version")
    except Exception as exc:
        logger.warning("No se pudo leer version.json: %s", exc)
        return None


def _parse_version(v: str):
    """Convierte '1.2.3' en tupla (1, 2, 3) para comparar."""
    try:
        return tuple(int(x) for x in re.findall(r"\d+", v))
    except Exception:
        return (0, 0, 0)


def _fetch_latest_release() -> Optional[Dict[str, Any]]:
    """Consulta GitHub API y devuelve info del último release, o None si falla."""
    try:
        req = urllib.request.Request(
            GITHUB_API_URL,
            headers={"User-Agent": "rpi-network-setup-updater/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        tag = data.get("tag_name", "")
        version = tag.lstrip("v")

        # Ignorar pre-releases
        if data.get("prerelease") or data.get("draft"):
            return None

        return {
            "version":      version,
            "tag":          tag,
            "release_notes": data.get("body", ""),
            "download_url": data.get("zipball_url", ""),
            "published_at": data.get("published_at", ""),
        }
    except Exception as exc:
        logger.warning("Error consultando GitHub API: %s", exc)
        return None


def _set_status(status: str, msg: str = "", error: str = None):
    _state["status"]     = status
    _state["status_msg"] = msg
    _state["error"]      = error
    logger.info("[OTA] %s — %s", status, msg)


# ---------------------------------------------------------------------------
# Polling en background
# ---------------------------------------------------------------------------

def _check_loop():
    """Thread daemon que verifica updates periódicamente."""
    # Primer check al arrancar (esperar 30s para que el servidor levante)
    time.sleep(30)
    while True:
        _do_check()
        time.sleep(CHECK_INTERVAL_H * 3600)


def _do_check():
    """Consulta GitHub y actualiza el estado."""
    _state["local_version"] = _read_local_version()
    _set_status("checking", "Consultando GitHub...")

    release = _fetch_latest_release()
    _state["checked_at"] = datetime.now().isoformat()

    if not release:
        _set_status("idle", "No se pudo contactar GitHub")
        return

    _state["latest_version"] = release["version"]
    _state["release_notes"]  = release["release_notes"]
    _state["download_url"]   = release["download_url"]

    local  = _parse_version(_state["local_version"] or "0.0.0")
    latest = _parse_version(release["version"])

    if latest > local:
        _state["update_available"] = True
        _set_status("idle", f"Nueva versión disponible: v{release['version']}")
        logger.info("[OTA] Update disponible: %s → %s", _state["local_version"], release["version"])
    else:
        _state["update_available"] = False
        _set_status("idle", "El sistema está actualizado")


# ---------------------------------------------------------------------------
# Instalación
# ---------------------------------------------------------------------------

def _do_install():
    """Descarga e instala la actualización. Corre en un thread separado."""
    if not _install_lock.acquire(blocking=False):
        logger.warning("[OTA] Instalación ya en curso")
        return

    try:
        download_url = _state.get("download_url")
        new_version  = _state.get("latest_version")

        if not download_url or not new_version:
            _set_status("error", "No hay URL de descarga", "Sin datos de release")
            return

        # 1. Backup
        _set_status("downloading", "Creando backup...")
        if os.path.exists(BACKUP_DIR):
            shutil.rmtree(BACKUP_DIR)
        shutil.copytree(PROJECT_ROOT, BACKUP_DIR,
                        ignore=shutil.ignore_patterns("venv", "__pycache__", "*.pyc"))
        logger.info("[OTA] Backup creado en %s", BACKUP_DIR)

        # 2. Descarga
        _set_status("downloading", f"Descargando v{new_version}...")
        req = urllib.request.Request(
            download_url,
            headers={"User-Agent": "rpi-network-setup-updater/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(DOWNLOAD_PATH, "wb") as f:
                shutil.copyfileobj(resp, f)
        logger.info("[OTA] ZIP descargado: %s", DOWNLOAD_PATH)

        # 3. Descompresión
        _set_status("installing", "Descomprimiendo...")
        if os.path.exists(EXTRACT_PATH):
            shutil.rmtree(EXTRACT_PATH)
        os.makedirs(EXTRACT_PATH)
        with zipfile.ZipFile(DOWNLOAD_PATH, "r") as z:
            z.extractall(EXTRACT_PATH)

        # GitHub pone el contenido dentro de una subcarpeta con el nombre del repo
        subdirs = [d for d in os.listdir(EXTRACT_PATH)
                   if os.path.isdir(os.path.join(EXTRACT_PATH, d))]
        if not subdirs:
            _set_status("error", "ZIP inválido", "No se encontró contenido")
            return
        source_root = os.path.join(EXTRACT_PATH, subdirs[0])

        # 4. Reemplazo de archivos
        _set_status("installing", "Instalando archivos...")
        _copy_update(source_root, PROJECT_ROOT)

        # 5. Actualizar version.json local
        version_src = os.path.join(source_root, "version.json")
        if os.path.exists(version_src):
            shutil.copy2(version_src, VERSION_FILE)

        # 5b. Recrear venv si no existe (post-instalación)
        _set_status("installing", "Preparando entorno Python...")
        backend_path = os.path.join(PROJECT_ROOT, "backend")
        venv_path = os.path.join(backend_path, "venv")
        if not os.path.exists(venv_path):
            logger.info("[OTA] venv no encontrado, recreando...")
            try:
                subprocess.run([sys.executable, "-m", "venv", venv_path],
                              capture_output=True, timeout=120)
                pip_exe = os.path.join(venv_path, "bin", "pip")
                req_file = os.path.join(backend_path, "requirements.txt")
                if os.path.exists(req_file):
                    subprocess.run([pip_exe, "install", "-r", req_file],
                                  capture_output=True, timeout=300)
                logger.info("[OTA] venv recreado exitosamente")
            except Exception as exc:
                logger.warning("[OTA] Error recreando venv (continuando anyway): %s", exc)

        # 6. Reiniciar servicio
        _set_status("restarting", "Reiniciando servicio...")
        subprocess.run(["sudo", "systemctl", "restart", SERVICE_NAME],
                       capture_output=True, timeout=30)

        # 7. Verificar que levantó
        time.sleep(RESTART_WAIT_S)
        result = subprocess.run(["systemctl", "is-active", SERVICE_NAME],
                                capture_output=True, text=True)
        if result.stdout.strip() == "active":
            _state["local_version"]   = new_version
            _state["update_available"] = False
            _set_status("done", f"Actualizado a v{new_version} correctamente")
            logger.info("[OTA] Actualización exitosa a v%s", new_version)
        else:
            raise RuntimeError("El servicio no levantó después del restart")

    except Exception as exc:
        logger.error("[OTA] Error durante instalación: %s", exc)
        _set_status("error", "Error durante la instalación", str(exc))
        _do_rollback()

    finally:
        _install_lock.release()
        # Limpiar temporales
        for path in [DOWNLOAD_PATH, EXTRACT_PATH]:
            try:
                if os.path.isfile(path):
                    os.remove(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path)
            except Exception:
                pass


def _copy_update(source: str, dest: str):
    """Copia los archivos del update sobre el proyecto, respetando PRESERVE."""
    for item in os.listdir(source):
        src_path  = os.path.join(source, item)
        dest_path = os.path.join(dest, item)

        # Respetar archivos/carpetas preservadas
        rel = os.path.relpath(dest_path, PROJECT_ROOT)
        if any(rel == p or rel.startswith(p + os.sep) for p in PRESERVE):
            logger.info("[OTA] Preservado: %s", rel)
            continue

        if os.path.isdir(src_path):
            if os.path.exists(dest_path):
                shutil.rmtree(dest_path)
            shutil.copytree(src_path, dest_path,
                            ignore=shutil.ignore_patterns("venv", "__pycache__", "*.pyc"))
        else:
            shutil.copy2(src_path, dest_path)


def _do_rollback():
    """Restaura el backup y reinicia el servicio."""
    if not os.path.exists(BACKUP_DIR):
        logger.error("[OTA] No hay backup para restaurar")
        return
    try:
        logger.warning("[OTA] Iniciando rollback...")
        _copy_update(BACKUP_DIR, PROJECT_ROOT)
        subprocess.run(["sudo", "systemctl", "restart", SERVICE_NAME],
                       capture_output=True, timeout=30)
        _set_status("error", "Actualización fallida — versión anterior restaurada",
                    _state.get("error"))
        logger.info("[OTA] Rollback completado")
    except Exception as exc:
        logger.error("[OTA] Error en rollback: %s", exc)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def get_update_status() -> Dict[str, Any]:
    return {
        "local_version":    _state["local_version"] or _read_local_version(),
        "latest_version":   _state["latest_version"],
        "update_available": _state["update_available"],
        "release_notes":    _state["release_notes"],
        "checked_at":       _state["checked_at"],
        "status":           _state["status"],
        "status_msg":       _state["status_msg"],
        "error":            _state["error"],
    }


def check_now():
    """Fuerza un check inmediato en background."""
    threading.Thread(target=_do_check, daemon=True).start()


def install_update():
    """Inicia la instalación en background. Devuelve False si ya hay una en curso."""
    if _install_lock.locked():
        return False
    threading.Thread(target=_do_install, daemon=True).start()
    return True


def start_background_checker():
    """Inicia el thread de polling. Llamar una vez al arrancar la app."""
    _state["local_version"] = _read_local_version()
    t = threading.Thread(target=_check_loop, daemon=True, name="ota-checker")
    t.start()
    logger.info("[OTA] Checker iniciado. Versión local: %s", _state["local_version"])
