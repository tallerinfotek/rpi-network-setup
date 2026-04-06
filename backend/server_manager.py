"""
server_manager.py - Gestiona la migración del servidor web entre IPs.

Cuando eth0 se configura con una IP real, el servidor se migra automáticamente
de la IP fallback (192.168.1.2) a esa IP.
"""

import json
import logging
import os
import re
import threading
import time
from typing import Dict, Any, Optional

from config import FALLBACK_IP, FALLBACK_INTERFACE
from network_manager import NetworkManager

logger = logging.getLogger(__name__)

# Archivo para persistir la configuración de IP fallback
_SERVER_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "server_config.json")


class ServerManager:
    """
    Monitorea la interfaz eth0 y detecta cuando obtiene una IP real.
    Notifica a la aplicación para que se reubique a esa IP.
    """

    def __init__(self):
        self.nm = NetworkManager()
        self.fallback_ip = self._load_fallback_ip()
        self.current_ip = self.fallback_ip
        self.current_iface = FALLBACK_INTERFACE
        self.monitoring = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.callbacks = []

    def _load_fallback_ip(self) -> str:
        """Carga la IP fallback desde el archivo de configuración o usa la default."""
        try:
            if os.path.exists(_SERVER_CONFIG_FILE):
                with open(_SERVER_CONFIG_FILE, "r") as f:
                    config = json.load(f)
                    return config.get("fallback_ip", FALLBACK_IP)
        except Exception as exc:
            logger.warning("Error cargando IP fallback desde archivo: %s", exc)
        return FALLBACK_IP

    def _save_fallback_ip(self):
        """Persiste la IP fallback en archivo."""
        try:
            os.makedirs(os.path.dirname(_SERVER_CONFIG_FILE), exist_ok=True)
            config = {"fallback_ip": self.fallback_ip}
            with open(_SERVER_CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
            logger.info("IP fallback guardada: %s", self.fallback_ip)
        except Exception as exc:
            logger.error("Error guardando IP fallback: %s", exc)

    def register_callback(self, callback):
        """Registra un callback que se llamará cuando cambie la IP."""
        self.callbacks.append(callback)

    def _notify_callbacks(self, old_ip: str, new_ip: str, iface: str):
        """Notifica a todos los callbacks registrados."""
        for callback in self.callbacks:
            try:
                callback(old_ip=old_ip, new_ip=new_ip, iface=iface)
            except Exception as exc:
                logger.error("Error en callback: %s", exc)

    def get_current_binding(self) -> Dict[str, Any]:
        """Devuelve a qué IP y puerto está escuchando actualmente el servidor."""
        return {
            "ip": self.current_ip,
            "iface": self.current_iface,
            "port": 80,
        }

    def start_monitoring(self, interval: int = 5):
        """
        Inicia el monitoreo de cambios en eth0.
        interval: segundos entre chequeos.
        """
        if self.monitoring:
            logger.warning("Monitoreo ya activo")
            return

        self.monitoring = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True
        )
        self.monitor_thread.start()
        logger.info("Monitoreo de interfaces iniciado (intervalo: %ds)", interval)

    def stop_monitoring(self):
        """Detiene el monitoreo."""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        logger.info("Monitoreo de interfaces detenido")

    def _monitor_loop(self, interval: int):
        """Loop de monitoreo que chequea cambios en eth0."""
        while self.monitoring:
            try:
                iface_info = self.nm.get_interface_config(FALLBACK_INTERFACE)

                # Si eth0 tiene una IP y es diferente a la actual, migra
                if iface_info.get("ip") and iface_info["ip"] != self.current_ip:
                    new_ip = iface_info["ip"]
                    old_ip = self.current_ip

                    logger.info(
                        "IP detectada en %s: %s → migrando servidor",
                        FALLBACK_INTERFACE, new_ip
                    )

                    self.current_ip = new_ip
                    self.current_iface = FALLBACK_INTERFACE
                    self._notify_callbacks(old_ip, new_ip, FALLBACK_INTERFACE)

                elif not iface_info.get("ip") and self.current_ip != self.fallback_ip:
                    # Si eth0 perdió su IP, vuelve a la IP fallback configurada
                    logger.warning(
                        "%s perdió su IP, volviendo a fallback %s",
                        FALLBACK_INTERFACE, self.fallback_ip
                    )
                    old_ip = self.current_ip
                    self.current_ip = self.fallback_ip
                    self.current_iface = FALLBACK_INTERFACE
                    self._notify_callbacks(old_ip, self.fallback_ip, FALLBACK_INTERFACE)

                time.sleep(interval)
            except Exception as exc:
                logger.error("Error en loop de monitoreo: %s", exc)
                time.sleep(interval)

    def set_fallback_ip(self, new_ip: str) -> Dict[str, Any]:
        """
        Establece una nueva IP fallback.
        Valida el formato y la persiste en archivo.
        """
        if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", new_ip):
            return {"success": False, "error": f"IP inválida: {new_ip}"}

        self.fallback_ip = new_ip
        self.current_ip = new_ip  # Si no hay IP en eth0, usamos la nueva
        self._save_fallback_ip()

        return {
            "success": True,
            "fallback_ip": new_ip,
            "message": "IP fallback actualizada. Toma efecto cuando eth0 pierda su IP."
        }

    def get_server_status(self) -> Dict[str, Any]:
        """Devuelve estado actual del binding del servidor."""
        binding = self.get_current_binding()
        return {
            "binding": binding,
            "monitoring": self.monitoring,
            "monitoring_interface": FALLBACK_INTERFACE,
            "fallback_ip": self.fallback_ip,
            "access_url": f"http://{binding['ip']}:{binding['port']}/",
        }
