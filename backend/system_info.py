"""
Módulo para obtener información del sistema operativo y hardware de la Raspberry Pi.
En modo desarrollo (PC) simula los valores para facilitar las pruebas.
"""

import os
import re
import subprocess
import time
import logging
from datetime import timedelta
from typing import Dict, Any

import psutil

from config import DEV_MODE, THERMAL_ZONE, HOSTNAME_FILE, HOSTS_FILE

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------

def get_cpu_usage() -> float:
    """
    Devuelve el porcentaje de uso de la CPU (promedio de todos los núcleos).
    En modo dev devuelve el valor real del host.
    """
    try:
        return psutil.cpu_percent(interval=0.5)
    except Exception as exc:
        logger.warning("No se pudo obtener uso de CPU: %s", exc)
        return 0.0


def get_cpu_frequency() -> Dict[str, float]:
    """
    Devuelve la frecuencia actual, mínima y máxima de la CPU en MHz.
    """
    try:
        freq = psutil.cpu_freq()
        if freq:
            return {
                "current": round(freq.current, 1),
                "min": round(freq.min, 1),
                "max": round(freq.max, 1),
            }
    except Exception as exc:
        logger.warning("No se pudo obtener frecuencia de CPU: %s", exc)
    return {"current": 0.0, "min": 0.0, "max": 0.0}


# ---------------------------------------------------------------------------
# Memoria RAM
# ---------------------------------------------------------------------------

def get_memory_info() -> Dict[str, Any]:
    """
    Devuelve información de la memoria RAM en bytes y porcentaje.
    Claves: total, used, free, available, percent.
    """
    try:
        mem = psutil.virtual_memory()
        return {
            "total": mem.total,
            "used": mem.used,
            "free": mem.free,
            "available": mem.available,
            "percent": mem.percent,
            "total_mb": round(mem.total / 1024 / 1024, 1),
            "used_mb": round(mem.used / 1024 / 1024, 1),
            "free_mb": round(mem.free / 1024 / 1024, 1),
        }
    except Exception as exc:
        logger.warning("No se pudo obtener info de memoria: %s", exc)
        return {
            "total": 0, "used": 0, "free": 0, "available": 0, "percent": 0.0,
            "total_mb": 0.0, "used_mb": 0.0, "free_mb": 0.0,
        }


# ---------------------------------------------------------------------------
# Temperatura
# ---------------------------------------------------------------------------

def get_temperature() -> Dict[str, Any]:
    """
    Lee la temperatura de la CPU desde el sistema térmico del kernel.
    En Raspberry Pi el valor está en /sys/class/thermal/thermal_zone0/temp (en miligrados).
    En modo dev intenta usar psutil; si no está disponible, devuelve simulado.
    """
    # Intento 1: archivo del kernel (Raspberry Pi y muchos Linux ARM)
    if os.path.exists(THERMAL_ZONE):
        try:
            with open(THERMAL_ZONE, "r") as f:
                raw = int(f.read().strip())
            celsius = raw / 1000.0
            return {"celsius": round(celsius, 1), "fahrenheit": round(celsius * 9 / 5 + 32, 1), "source": "thermal_zone"}
        except Exception as exc:
            logger.warning("Error leyendo zona térmica: %s", exc)

    # Intento 2: psutil (disponible en algunos sistemas)
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            # Buscar la primera entrada disponible
            for sensor_name, entries in temps.items():
                if entries:
                    celsius = entries[0].current
                    return {
                        "celsius": round(celsius, 1),
                        "fahrenheit": round(celsius * 9 / 5 + 32, 1),
                        "source": sensor_name,
                    }
    except (AttributeError, Exception) as exc:
        logger.debug("psutil.sensors_temperatures no disponible: %s", exc)

    # Fallback en modo dev
    if DEV_MODE:
        return {"celsius": 42.0, "fahrenheit": 107.6, "source": "simulated"}

    return {"celsius": None, "fahrenheit": None, "source": "unavailable"}


# ---------------------------------------------------------------------------
# Uptime
# ---------------------------------------------------------------------------

def get_uptime() -> Dict[str, Any]:
    """
    Devuelve el tiempo que lleva encendido el sistema.
    """
    try:
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        delta = timedelta(seconds=int(uptime_seconds))
        days = delta.days
        hours, remainder = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return {
            "seconds": int(uptime_seconds),
            "days": days,
            "hours": hours,
            "minutes": minutes,
            "formatted": f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}",
        }
    except Exception as exc:
        logger.warning("No se pudo obtener uptime: %s", exc)
        return {"seconds": 0, "days": 0, "hours": 0, "minutes": 0, "formatted": "0d 00:00:00"}


# ---------------------------------------------------------------------------
# Disco
# ---------------------------------------------------------------------------

def get_disk_info() -> Dict[str, Any]:
    """
    Devuelve información del disco raíz (/).
    En sistemas Windows devuelve el disco C:.
    """
    path = "/" if os.name != "nt" else "C:\\"
    try:
        usage = psutil.disk_usage(path)
        return {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": usage.percent,
            "total_gb": round(usage.total / 1024 ** 3, 2),
            "used_gb": round(usage.used / 1024 ** 3, 2),
            "free_gb": round(usage.free / 1024 ** 3, 2),
        }
    except Exception as exc:
        logger.warning("No se pudo obtener info de disco: %s", exc)
        return {
            "total": 0, "used": 0, "free": 0, "percent": 0.0,
            "total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0,
        }


# ---------------------------------------------------------------------------
# Hostname
# ---------------------------------------------------------------------------

def get_hostname() -> str:
    """
    Devuelve el hostname actual del sistema.
    """
    try:
        import socket
        return socket.gethostname()
    except Exception as exc:
        logger.warning("No se pudo obtener el hostname: %s", exc)
        return "raspberrypi"


def set_hostname(name: str) -> Dict[str, Any]:
    """
    Cambia el hostname del sistema de forma permanente.
    Modifica /etc/hostname y /etc/hosts.
    Requiere permisos de root en Raspberry Pi.
    """
    # Validar nombre
    if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$', name):
        return {"success": False, "error": "Nombre de hostname inválido. Solo letras, números y guiones."}

    if DEV_MODE:
        logger.info("[DEV] set_hostname simulado: %s", name)
        return {"success": True, "hostname": name, "simulated": True}

    try:
        old_hostname = get_hostname()

        # Escribir /etc/hostname
        with open(HOSTNAME_FILE, "w") as f:
            f.write(name + "\n")

        # Actualizar /etc/hosts reemplazando el hostname viejo
        with open(HOSTS_FILE, "r") as f:
            hosts_content = f.read()

        hosts_content = hosts_content.replace(old_hostname, name)
        with open(HOSTS_FILE, "w") as f:
            f.write(hosts_content)

        # Aplicar el cambio en el kernel sin reiniciar
        subprocess.run(["hostname", name], check=True, capture_output=True)

        logger.info("Hostname cambiado de '%s' a '%s'", old_hostname, name)
        return {"success": True, "hostname": name, "previous": old_hostname}

    except PermissionError:
        return {"success": False, "error": "Permisos insuficientes. Ejecutar como root."}
    except Exception as exc:
        logger.error("Error cambiando hostname: %s", exc)
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Reboot
# ---------------------------------------------------------------------------

def reboot() -> Dict[str, Any]:
    """
    Reinicia el sistema Raspberry Pi usando 'sudo reboot'.
    En modo dev simula el reinicio.
    """
    if DEV_MODE:
        logger.info("[DEV] reboot simulado")
        return {"success": True, "message": "Reinicio simulado en modo desarrollo.", "simulated": True}

    try:
        logger.info("Iniciando reinicio del sistema...")
        subprocess.Popen(["sudo", "reboot"])
        return {"success": True, "message": "El sistema se reiniciará en breve."}
    except Exception as exc:
        logger.error("Error al reiniciar: %s", exc)
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Info completa del sistema
# ---------------------------------------------------------------------------

def get_full_system_info() -> Dict[str, Any]:
    """
    Agrega toda la información del sistema en un único diccionario.
    """
    mem = get_memory_info()
    temp = get_temperature()
    uptime = get_uptime()
    disk = get_disk_info()
    cpu_pct = get_cpu_usage()

    return {
        "hostname": get_hostname(),
        "cpu": {
            "usage_percent": cpu_pct,
            "frequency": get_cpu_frequency(),
            "count": psutil.cpu_count(logical=True),
            "count_physical": psutil.cpu_count(logical=False),
        },
        "memory": mem,
        "temperature": temp,
        "uptime": uptime,
        "disk": disk,
        "platform": {
            "system": os.uname().sysname if hasattr(os, "uname") else "Windows",
            "release": os.uname().release if hasattr(os, "uname") else "",
            "machine": os.uname().machine if hasattr(os, "uname") else "",
        },
        "dev_mode": DEV_MODE,
        # Aliases directos para el frontend
        "cpu_percent": cpu_pct,
        "ram_total_mb": mem["total_mb"],
        "ram_used_mb": mem["used_mb"],
        "uptime_seconds": uptime["seconds"],
        "temp_celsius": temp["celsius"],
        "disk_percent": disk["percent"],
    }
