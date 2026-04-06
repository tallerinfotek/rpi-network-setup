"""
Configuración global del proyecto Access Point Configurator.
Detecta automáticamente si estamos en una Raspberry Pi o en un PC de desarrollo.
"""

import os
import platform

# ---------------------------------------------------------------------------
# Detección del entorno
# ---------------------------------------------------------------------------

def _is_raspberry_pi() -> bool:
    """
    Devuelve True si el proceso se está ejecutando en una Raspberry Pi real.
    Comprueba /proc/cpuinfo en busca del modelo ARM de Broadcom.
    """
    if platform.system() != "Linux":
        return False
    try:
        with open("/proc/cpuinfo", "r") as f:
            content = f.read()
        return "Raspberry Pi" in content or "BCM" in content
    except OSError:
        return False

IS_RASPBERRY_PI: bool = _is_raspberry_pi()
DEV_MODE: bool = not IS_RASPBERRY_PI

# ---------------------------------------------------------------------------
# Red / Access Point
# ---------------------------------------------------------------------------

AP_IP: str = "192.168.4.1"
AP_NETMASK: str = "255.255.255.0"
AP_NETWORK: str = "192.168.4.0/24"
AP_DHCP_RANGE_START: str = "192.168.4.10"
AP_DHCP_RANGE_END: str = "192.168.4.100"
AP_DHCP_LEASE_TIME: str = "12h"

AP_SSID_DEFAULT: str = "RPI-Setup"
AP_PASSWORD_DEFAULT: str = ""          # Sin contraseña en el AP de configuración inicial
AP_CHANNEL_DEFAULT: int = 6
AP_BAND_DEFAULT: str = "g"             # "g" = 2.4 GHz, "a" = 5 GHz
AP_INTERFACE_DEFAULT: str = "wlan0"
AP_HIDDEN_DEFAULT: bool = False

# ---------------------------------------------------------------------------
# Servidor web - Configuración de red fallback
# ---------------------------------------------------------------------------

# IP fija a la que se expone el servidor cuando no hay red en eth0
FALLBACK_IP: str = "192.168.1.2"       # Configurable: IP fija para acceso inicial
FALLBACK_NETMASK: str = "255.255.255.0"
FALLBACK_INTERFACE: str = "eth0"        # Interfaz que monitoreamos

# ---------------------------------------------------------------------------
# Servidor web - Host y puerto
# ---------------------------------------------------------------------------

# El servidor escucha en la IP fallback inicialmente.
# Cuando eth0 obtiene una IP, un monitor notifica el cambio.
# En desarrollo se usa el 5000 para no requerir privilegios.
SERVER_HOST: str = "0.0.0.0"  # Escuchar en todas las interfaces para máxima compatibilidad
SERVER_PORT: int = 80 if IS_RASPBERRY_PI else 5000
SERVER_DEBUG: bool = DEV_MODE

# Directorio donde Flask buscará el frontend compilado (Vite/React build)
_backend_dir = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIST_DIR: str = os.path.join(_backend_dir, "..", "frontend", "dist")
FRONTEND_DIR: str = os.path.join(_backend_dir, "..", "frontend")

# ---------------------------------------------------------------------------
# Rutas de archivos de configuración del sistema
# ---------------------------------------------------------------------------

HOSTAPD_CONF: str = "/etc/hostapd/hostapd.conf"
HOSTAPD_DEFAULT: str = "/etc/default/hostapd"
DHCPCD_CONF: str = "/etc/dhcpcd.conf"
WPA_SUPPLICANT_CONF: str = "/etc/wpa_supplicant/wpa_supplicant.conf"
DNSMASQ_CONF: str = "/etc/dnsmasq.conf"
DNSMASQ_LEASES: str = "/var/lib/misc/dnsmasq.leases"
HOSTNAME_FILE: str = "/etc/hostname"
HOSTS_FILE: str = "/etc/hosts"
INTERFACES_FILE: str = "/etc/network/interfaces"
SYSCTL_CONF: str = "/etc/sysctl.conf"
THERMAL_ZONE: str = "/sys/class/thermal/thermal_zone0/temp"

# ---------------------------------------------------------------------------
# Servicios systemd
# ---------------------------------------------------------------------------

SERVICES_NETWORKING = ["networking", "dhcpcd", "NetworkManager"]
SERVICES_AP = ["hostapd", "dnsmasq"]

# ---------------------------------------------------------------------------
# Utilidades de logging
# ---------------------------------------------------------------------------

LOG_LEVEL: str = "DEBUG" if DEV_MODE else "INFO"
LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
