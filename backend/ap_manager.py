"""
Módulo APManager: gestiona el Access Point WiFi usando hostapd y dnsmasq.
En modo desarrollo simula todas las operaciones para facilitar las pruebas en PC.
"""

import os
import re
import shutil
import subprocess
import logging
from typing import Any, Dict, List, Optional

from config import (
    DEV_MODE,
    HOSTAPD_CONF,
    HOSTAPD_DEFAULT,
    DNSMASQ_CONF,
    DNSMASQ_LEASES,
    AP_IP,
    AP_NETMASK,
    AP_DHCP_RANGE_START,
    AP_DHCP_RANGE_END,
    AP_DHCP_LEASE_TIME,
    AP_SSID_DEFAULT,
    AP_PASSWORD_DEFAULT,
    AP_CHANNEL_DEFAULT,
    AP_BAND_DEFAULT,
    AP_INTERFACE_DEFAULT,
    AP_HIDDEN_DEFAULT,
    SERVICES_AP,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _run(cmd: List[str], timeout: int = 15) -> subprocess.CompletedProcess:
    """Ejecuta un comando y devuelve el resultado sin lanzar excepciones."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        result = subprocess.CompletedProcess(cmd, returncode=1)
        result.stdout = ""
        result.stderr = str(exc)
        return result


def _service_is_active(service: str) -> bool:
    """Comprueba si un servicio systemd está activo."""
    result = _run(["systemctl", "is-active", service])
    return result.stdout.strip() == "active"


# ---------------------------------------------------------------------------
# Datos de simulación para modo dev
# ---------------------------------------------------------------------------

_DEV_AP_CONFIG: Dict[str, Any] = {
    "ssid": AP_SSID_DEFAULT,
    "password": AP_PASSWORD_DEFAULT,
    "channel": AP_CHANNEL_DEFAULT,
    "band": AP_BAND_DEFAULT,
    "interface": AP_INTERFACE_DEFAULT,
    "hidden": AP_HIDDEN_DEFAULT,
    "hw_mode": "g",
    "country_code": "ES",
}

_DEV_AP_ACTIVE: bool = True

_DEV_CONNECTED_CLIENTS: List[Dict[str, Any]] = [
    {
        "mac": "dc:a6:32:11:22:33",
        "ip": "192.168.4.11",
        "hostname": "android-phone",
        "connected_since": "2024-03-28T10:00:00",
        "signal": -55,
        "tx_bytes": 1024 * 512,
        "rx_bytes": 1024 * 256,
    }
]


# ---------------------------------------------------------------------------
# Clase principal
# ---------------------------------------------------------------------------

class APManager:
    """
    Gestiona el Access Point WiFi de la Raspberry Pi.
    Utiliza hostapd para el AP y dnsmasq para el servidor DHCP/DNS del AP.
    """

    def __init__(self):
        logger.info("APManager iniciado. Dev=%s", DEV_MODE)

    # ------------------------------------------------------------------
    # Configuración del AP (leer)
    # ------------------------------------------------------------------

    def _get_ap_interface(self) -> str:
        """Devuelve la interfaz configurada en hostapd.conf, o 'wlan0' por defecto."""
        try:
            with open(HOSTAPD_CONF, "r") as f:
                for line in f:
                    if line.startswith("interface="):
                        return line.split("=", 1)[1].strip()
        except Exception:
            pass
        return AP_INTERFACE_DEFAULT

    def get_ap_config(self) -> Dict[str, Any]:
        """
        Lee la configuración actual del AP desde /etc/hostapd/hostapd.conf.
        Devuelve un diccionario con todos los parámetros configurados.
        """
        if DEV_MODE:
            logger.debug("[DEV] get_ap_config simulado")
            return dict(_DEV_AP_CONFIG)

        config: Dict[str, Any] = {
            "ssid": AP_SSID_DEFAULT,
            "password": "",
            "channel": AP_CHANNEL_DEFAULT,
            "band": AP_BAND_DEFAULT,
            "interface": AP_INTERFACE_DEFAULT,
            "hidden": AP_HIDDEN_DEFAULT,
            "hw_mode": "g",
            "country_code": "ES",
        }

        if not os.path.exists(HOSTAPD_CONF):
            logger.info("hostapd.conf no existe, devolviendo configuración por defecto")
            return config

        try:
            with open(HOSTAPD_CONF, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()

                    if key == "ssid":
                        config["ssid"] = value
                    elif key == "wpa_passphrase":
                        config["password"] = value
                    elif key == "channel":
                        config["channel"] = int(value)
                    elif key == "hw_mode":
                        config["hw_mode"] = value
                        config["band"] = "5GHz" if value == "a" else "2.4GHz"
                    elif key == "interface":
                        config["interface"] = value
                    elif key == "ignore_broadcast_ssid":
                        config["hidden"] = value == "1"
                    elif key == "country_code":
                        config["country_code"] = value

        except Exception as exc:
            logger.error("Error leyendo hostapd.conf: %s", exc)

        config["enabled"] = _service_is_active("hostapd")
        return config

    # ------------------------------------------------------------------
    # Configuración del AP (escribir)
    # ------------------------------------------------------------------

    def set_ap_config(
        self,
        ssid: str = AP_SSID_DEFAULT,
        password: str = AP_PASSWORD_DEFAULT,
        channel: int = AP_CHANNEL_DEFAULT,
        band: str = AP_BAND_DEFAULT,
        hidden: bool = AP_HIDDEN_DEFAULT,
        interface: str = AP_INTERFACE_DEFAULT,
        country_code: str = "ES",
    ) -> Dict[str, Any]:
        """
        Escribe la configuración del AP en /etc/hostapd/hostapd.conf.
        Hace backup antes de modificar. Si la nueva configuración falla,
        restaura el backup automáticamente.
        """
        # Validaciones
        if not ssid or len(ssid) > 32:
            return {"success": False, "error": "SSID inválido (máx. 32 caracteres)."}
        if password and len(password) < 8:
            return {"success": False, "error": "La contraseña del AP debe tener mínimo 8 caracteres."}
        if channel < 1 or channel > 165:
            return {"success": False, "error": f"Canal inválido: {channel}."}

        # Convertir band a hw_mode de hostapd
        if band in ("a", "5GHz", "5ghz", "5"):
            hw_mode = "a"
        else:
            hw_mode = "g"  # 2.4GHz

        if DEV_MODE:
            logger.info("[DEV] set_ap_config simulado: SSID=%s canal=%s band=%s", ssid, channel, band)
            _DEV_AP_CONFIG.update({
                "ssid": ssid,
                "password": password,
                "channel": channel,
                "band": band,
                "interface": interface,
                "hidden": hidden,
                "hw_mode": hw_mode,
                "country_code": country_code,
            })
            return {"success": True, "simulated": True, "config": dict(_DEV_AP_CONFIG)}

        # Construir contenido de hostapd.conf
        wpa_section = ""
        if password:
            wpa_section = f"""
# Seguridad WPA2
wpa=2
wpa_passphrase={password}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
"""
        else:
            wpa_section = "\n# Sin contraseña (red abierta)\n"

        conf_content = f"""# Configuración hostapd generada por RPI-Setup
# No editar manualmente si usas el configurador web.

interface={interface}
driver=nl80211
ssid={ssid}
hw_mode={hw_mode}
channel={channel}
country_code={country_code}

# Ocultar SSID (0=visible, 1=oculto)
ignore_broadcast_ssid={"1" if hidden else "0"}

# IEEE 802.11 estándares
ieee80211n=1
wmm_enabled=1

# Autenticación
auth_algs=1
{wpa_section}
"""
        try:
            # Crear directorio si no existe
            os.makedirs(os.path.dirname(HOSTAPD_CONF), exist_ok=True)

            # Backup del archivo existente
            self._backup_file(HOSTAPD_CONF)

            with open(HOSTAPD_CONF, "w") as f:
                f.write(conf_content)

            # Actualizar /etc/default/hostapd para que apunte al archivo correcto
            self._update_hostapd_default()

            logger.info("hostapd.conf escrito: SSID=%s canal=%s", ssid, channel)
            return {
                "success": True,
                "ssid": ssid,
                "channel": channel,
                "band": band,
                "hidden": hidden,
                "interface": interface,
            }

        except PermissionError:
            return {"success": False, "error": "Permisos insuficientes. Ejecutar como root."}
        except Exception as exc:
            logger.error("Error escribiendo hostapd.conf: %s", exc)
            self._restore_backup(HOSTAPD_CONF)
            return {"success": False, "error": str(exc)}

    def _update_hostapd_default(self):
        """Actualiza /etc/default/hostapd para apuntar al archivo de configuración."""
        if not os.path.exists(HOSTAPD_DEFAULT):
            return
        try:
            with open(HOSTAPD_DEFAULT, "r") as f:
                content = f.read()
            # Descomentar o reemplazar la línea DAEMON_CONF
            content = re.sub(
                r"#?\s*DAEMON_CONF=.*",
                f'DAEMON_CONF="{HOSTAPD_CONF}"',
                content,
            )
            with open(HOSTAPD_DEFAULT, "w") as f:
                f.write(content)
        except Exception as exc:
            logger.warning("No se pudo actualizar /etc/default/hostapd: %s", exc)

    # ------------------------------------------------------------------
    # Control del servicio AP
    # ------------------------------------------------------------------

    def start_ap(self) -> Dict[str, Any]:
        """
        Inicia los servicios hostapd y dnsmasq para activar el AP.
        """
        if DEV_MODE:
            global _DEV_AP_ACTIVE
            _DEV_AP_ACTIVE = True
            logger.info("[DEV] start_ap simulado")
            return {"success": True, "simulated": True, "status": "active"}

        results = {}
        for service in SERVICES_AP:
            result = _run(["sudo", "systemctl", "start", service])
            results[service] = {
                "started": result.returncode == 0,
                "error": result.stderr.strip() if result.returncode != 0 else None,
            }
            if result.returncode == 0:
                logger.info("Servicio iniciado: %s", service)
            else:
                logger.error("Error iniciando %s: %s", service, result.stderr.strip())

        success = all(v["started"] for v in results.values())
        return {"success": success, "services": results}

    def stop_ap(self) -> Dict[str, Any]:
        """
        Detiene hostapd y dnsmasq.
        """
        if DEV_MODE:
            global _DEV_AP_ACTIVE
            _DEV_AP_ACTIVE = False
            logger.info("[DEV] stop_ap simulado")
            return {"success": True, "simulated": True, "status": "inactive"}

        results = {}
        for service in SERVICES_AP:
            result = _run(["sudo", "systemctl", "stop", service])
            results[service] = {
                "stopped": result.returncode == 0,
                "error": result.stderr.strip() if result.returncode != 0 else None,
            }
            if result.returncode == 0:
                logger.info("Servicio detenido: %s", service)

        success = all(v["stopped"] for v in results.values())

        # Reconectar wlan0 como cliente WiFi
        if success:
            import shutil
            iface = self._get_ap_interface()
            if shutil.which("nmcli"):
                # NetworkManager: reconectar la interfaz
                _run(["nmcli", "device", "connect", iface])
                logger.info("%s reconectado via NetworkManager", iface)
            else:
                # dhcpcd / wpa_supplicant
                _run(["ip", "link", "set", iface, "down"])
                _run(["ip", "link", "set", iface, "up"])
                _run(["wpa_supplicant", "-B", "-i", iface, "-c", "/etc/wpa_supplicant/wpa_supplicant.conf"])
                _run(["dhclient", iface])
                logger.info("%s reconectado via wpa_supplicant", iface)

        return {"success": success, "services": results}

    def restart_ap(self) -> Dict[str, Any]:
        """
        Reinicia los servicios del AP (stop + start).
        """
        if DEV_MODE:
            logger.info("[DEV] restart_ap simulado")
            return {"success": True, "simulated": True, "status": "active"}

        results = {}
        for service in SERVICES_AP:
            result = _run(["sudo", "systemctl", "restart", service])
            results[service] = {
                "restarted": result.returncode == 0,
                "error": result.stderr.strip() if result.returncode != 0 else None,
            }

        success = all(v["restarted"] for v in results.values())
        return {"success": success, "services": results}

    # ------------------------------------------------------------------
    # Estado del AP
    # ------------------------------------------------------------------

    def get_ap_status(self) -> Dict[str, Any]:
        """
        Devuelve el estado actual del AP: activo/inactivo, config actual, clientes.
        """
        if DEV_MODE:
            return {
                "active": _DEV_AP_ACTIVE,
                "config": dict(_DEV_AP_CONFIG),
                "clients_count": len(_DEV_CONNECTED_CLIENTS),
                "clients": _DEV_CONNECTED_CLIENTS,
                "ip": AP_IP,
                "simulated": True,
            }

        hostapd_active = _service_is_active("hostapd")
        dnsmasq_active = _service_is_active("dnsmasq")
        clients = self.get_connected_clients()
        config = self.get_ap_config()

        return {
            "active": hostapd_active,
            "hostapd_active": hostapd_active,
            "dnsmasq_active": dnsmasq_active,
            "config": config,
            "clients_count": len(clients),
            "clients": clients,
            "ip": AP_IP,
        }

    # ------------------------------------------------------------------
    # Clientes conectados
    # ------------------------------------------------------------------

    def get_connected_clients(self) -> List[Dict[str, Any]]:
        """
        Devuelve la lista de clientes conectados al AP.
        Cruza la información de hostapd (asociados) con los leases de dnsmasq.
        """
        if DEV_MODE:
            return list(_DEV_CONNECTED_CLIENTS)

        clients = []

        # Obtener MACs asociadas desde iw
        config = self.get_ap_config()
        iface = config.get("interface", AP_INTERFACE_DEFAULT)
        result = _run(["iw", "dev", iface, "station", "dump"])

        associated_macs: Dict[str, Dict[str, Any]] = {}
        if result.returncode == 0:
            current_mac = None
            for line in result.stdout.split("\n"):
                line = line.strip()
                if line.startswith("Station"):
                    mac_match = re.match(r"Station ([\da-f:]+)", line, re.I)
                    if mac_match:
                        current_mac = mac_match.group(1).lower()
                        associated_macs[current_mac] = {"mac": current_mac}
                elif current_mac:
                    if "signal:" in line.lower():
                        sig_match = re.search(r"(-?\d+)", line)
                        if sig_match:
                            associated_macs[current_mac]["signal"] = int(sig_match.group(1))
                    elif "tx bytes:" in line.lower():
                        num_match = re.search(r"(\d+)", line)
                        if num_match:
                            associated_macs[current_mac]["tx_bytes"] = int(num_match.group(1))
                    elif "rx bytes:" in line.lower():
                        num_match = re.search(r"(\d+)", line)
                        if num_match:
                            associated_macs[current_mac]["rx_bytes"] = int(num_match.group(1))

        # Cruzar con leases de dnsmasq para obtener IP y hostname
        leases = self._read_leases()
        lease_map = {l["mac"].lower(): l for l in leases}

        for mac, info in associated_macs.items():
            client = dict(info)
            if mac in lease_map:
                client["ip"] = lease_map[mac]["ip"]
                client["hostname"] = lease_map[mac]["hostname"]
            else:
                client["ip"] = None
                client["hostname"] = None
            clients.append(client)

        # Si no hay info de iw, usar sólo los leases
        if not clients and leases:
            clients = leases

        return clients

    def _read_leases(self) -> List[Dict[str, Any]]:
        """Lee el archivo de leases de dnsmasq."""
        leases = []
        if not os.path.exists(DNSMASQ_LEASES):
            return leases
        try:
            with open(DNSMASQ_LEASES, "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 4:
                        leases.append({
                            "expiry": parts[0],
                            "mac": parts[1],
                            "ip": parts[2],
                            "hostname": parts[3] if parts[3] != "*" else "",
                        })
        except Exception as exc:
            logger.error("Error leyendo leases: %s", exc)
        return leases

    # ------------------------------------------------------------------
    # Configuración inicial del AP (primer arranque)
    # ------------------------------------------------------------------

    def setup_initial_ap(self) -> Dict[str, Any]:
        """
        Configura el AP de primer arranque:
        - SSID: RPI-Setup (sin contraseña)
        - IP del AP: 192.168.4.1
        - DHCP: 192.168.4.10 - 192.168.4.100
        Escribe hostapd.conf, dnsmasq.conf y configura la interfaz con IP fija.
        """
        if DEV_MODE:
            logger.info("[DEV] setup_initial_ap simulado")
            return {"success": True, "simulated": True, "ap_ip": AP_IP, "ssid": AP_SSID_DEFAULT}

        results = {}

        # 1. Escribir configuración de hostapd
        results["hostapd"] = self.set_ap_config(
            ssid=AP_SSID_DEFAULT,
            password=AP_PASSWORD_DEFAULT,
            channel=AP_CHANNEL_DEFAULT,
            band=AP_BAND_DEFAULT,
            hidden=AP_HIDDEN_DEFAULT,
        )

        # 2. Configurar IP fija en la interfaz del AP
        results["static_ip"] = self._set_ap_static_ip()

        # 3. Escribir configuración de dnsmasq para el AP
        results["dnsmasq"] = self._write_dnsmasq_config()

        # 4. Habilitar IP forwarding
        results["ip_forwarding"] = self._enable_ip_forwarding()

        # 5. Iniciar servicios
        results["services"] = self.start_ap()

        success = all(
            r.get("success", False) for r in results.values() if isinstance(r, dict)
        )
        logger.info(
            "Setup inicial AP %s: SSID=%s IP=%s",
            "exitoso" if success else "con errores",
            AP_SSID_DEFAULT,
            AP_IP,
        )
        return {"success": success, "ap_ip": AP_IP, "ssid": AP_SSID_DEFAULT, "details": results}

    def _set_ap_static_ip(self) -> Dict[str, Any]:
        """
        Asigna la IP fija 192.168.4.1 a la interfaz del AP en dhcpcd.conf.
        """
        from network_manager import NetworkManager
        nm = NetworkManager()
        return nm.set_static_ip(
            iface=AP_INTERFACE_DEFAULT,
            ip=AP_IP,
            netmask=AP_NETMASK,
            gateway="",
            dns=["8.8.8.8", "8.8.4.4"],
        )

    def _write_dnsmasq_config(self) -> Dict[str, Any]:
        """
        Escribe la configuración de dnsmasq para el AP.
        Sólo sirve DHCP en la interfaz del AP.
        """
        content = f"""# dnsmasq config para Access Point - RPI-Setup
# Generado automáticamente. No editar manualmente.

# Solo escuchar en la interfaz del AP
interface={AP_INTERFACE_DEFAULT}
bind-dynamic

# Rango DHCP del AP
dhcp-range={AP_DHCP_RANGE_START},{AP_DHCP_RANGE_END},{AP_NETMASK},{AP_DHCP_LEASE_TIME}

# Gateway y DNS para los clientes del AP
dhcp-option=3,{AP_IP}
dhcp-option=6,{AP_IP}

# Servidor DNS incorporado
server=8.8.8.8
server=8.8.4.4

# Guardar leases
dhcp-leasefile={DNSMASQ_LEASES}

# Log de asignaciones DHCP
log-dhcp
"""
        try:
            self._backup_file(DNSMASQ_CONF)
            os.makedirs(os.path.dirname(DNSMASQ_CONF), exist_ok=True)
            with open(DNSMASQ_CONF, "w") as f:
                f.write(content)
            logger.info("dnsmasq.conf escrito")
            return {"success": True}
        except PermissionError:
            return {"success": False, "error": "Permisos insuficientes para escribir dnsmasq.conf"}
        except Exception as exc:
            self._restore_backup(DNSMASQ_CONF)
            return {"success": False, "error": str(exc)}

    def _enable_ip_forwarding(self) -> Dict[str, Any]:
        """
        Habilita el IP forwarding del kernel para enrutar tráfico entre interfaces.
        Escribe en /proc/sys/net/ipv4/ip_forward y en /etc/sysctl.conf.
        """
        try:
            with open("/proc/sys/net/ipv4/ip_forward", "w") as f:
                f.write("1\n")

            # Persistir en sysctl.conf
            sysctl_path = "/etc/sysctl.conf"
            if os.path.exists(sysctl_path):
                with open(sysctl_path, "r") as f:
                    content = f.read()
                if "net.ipv4.ip_forward=1" not in content:
                    with open(sysctl_path, "a") as f:
                        f.write("\n# Habilitado por RPI-Setup\nnet.ipv4.ip_forward=1\n")

            logger.info("IP forwarding habilitado")
            return {"success": True}
        except PermissionError:
            return {"success": False, "error": "Permisos insuficientes para habilitar IP forwarding"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Utilidades de backup/restore
    # ------------------------------------------------------------------

    @staticmethod
    def _backup_file(path: str) -> bool:
        """Crea una copia de seguridad de un archivo con extensión .bak."""
        if os.path.exists(path):
            try:
                shutil.copy2(path, path + ".bak")
                logger.debug("Backup creado: %s.bak", path)
                return True
            except Exception as exc:
                logger.warning("No se pudo crear backup de %s: %s", path, exc)
        return False

    @staticmethod
    def _restore_backup(path: str) -> bool:
        """Restaura el archivo desde su copia de seguridad .bak."""
        backup = path + ".bak"
        if os.path.exists(backup):
            try:
                shutil.copy2(backup, path)
                logger.info("Configuración restaurada desde backup: %s", path)
                return True
            except Exception as exc:
                logger.error("No se pudo restaurar backup de %s: %s", path, exc)
        return False
