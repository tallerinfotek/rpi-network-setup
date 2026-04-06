"""
Módulo NetworkManager: detecta y gestiona las interfaces de red de la Raspberry Pi.
Soporta sistemas con dhcpcd/wpa_supplicant y con NetworkManager.
En modo desarrollo (PC Windows/Mac/Linux sin Raspberry Pi) simula todas las operaciones.
"""

import os
import re
import shutil
import subprocess
import shutil
import subprocess
import logging
import threading
import time
from typing import Any, Dict, List, Optional
from datetime import datetime

from config import (
    DEV_MODE,
    DHCPCD_CONF,
    WPA_SUPPLICANT_CONF,
    DNSMASQ_LEASES,
    SERVICES_NETWORKING,
)

logger = logging.getLogger(__name__)

WIFI_DEBUG_LOG = "/var/log/wifi_connect.log"


def _wifi_debug_log(msg: str, level: str = "INFO"):
    """Escribe logs de depuración WiFi a un archivo persistente."""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(WIFI_DEBUG_LOG, "a") as f:
            f.write(f"[{timestamp}] [{level}] {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

_wifi_lock = threading.Lock()

def _run(cmd: List[str], timeout: int = 10) -> subprocess.CompletedProcess:
    """Ejecuta un comando y devuelve el resultado. No lanza excepciones."""
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.debug("Comando '%s' falló: %s", " ".join(cmd), exc)
        # Devolver un resultado vacío para que el llamador lo maneje
        result = subprocess.CompletedProcess(cmd, returncode=1)
        result.stdout = ""
        result.stderr = str(exc)
        return result


_internet_cache: Dict[str, Any] = {"result": None, "ts": 0.0, "failures": 0}
_INTERNET_CACHE_TTL = 15      # segundos entre checks
_INTERNET_FAIL_THRESHOLD = 3  # fallos consecutivos para declarar offline

def check_internet(host: str = "8.8.8.8", timeout: int = 2, iface: str = None) -> Dict[str, Any]:
    """Verifica si hay acceso a internet haciendo ping a un host externo.
    Cachea el último resultado conocido. Solo marca offline tras 3 fallos consecutivos."""
    import time
    if DEV_MODE:
        return {"online": True, "latency_ms": 12.5, "simulated": True}
    if iface is None:
        now = time.time()
        if _internet_cache["result"] is not None and (now - _internet_cache["ts"]) < _INTERNET_CACHE_TTL:
            return _internet_cache["result"]
    try:
        cmd = ["ping", "-c", "1", "-W", str(timeout)]
        if iface:
            cmd += ["-I", iface]
        cmd.append(host)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 1)
        if result.returncode == 0:
            match = re.search(r"time=([\d.]+)", result.stdout)
            latency = float(match.group(1)) if match else None
            ret = {"online": True, "latency_ms": latency}
            if iface is None:
                _internet_cache["result"] = ret
                _internet_cache["ts"] = time.time()
                _internet_cache["failures"] = 0
            return ret
        else:
            if iface is None:
                _internet_cache["failures"] += 1
                _internet_cache["ts"] = time.time()
                if _internet_cache["failures"] >= _INTERNET_FAIL_THRESHOLD:
                    _internet_cache["result"] = {"online": False, "latency_ms": None}
                # Devolver el último resultado conocido mientras no se supere el umbral
                if _internet_cache["result"] is not None:
                    return _internet_cache["result"]
            return {"online": False, "latency_ms": None}
    except Exception:
        if iface is None and _internet_cache["result"] is not None:
            _internet_cache["ts"] = time.time()
            return _internet_cache["result"]
        return {"online": False, "latency_ms": None}


def _has_network_manager() -> bool:
    """Detecta si NetworkManager está activo en el sistema."""
    result = _run(["systemctl", "is-active", "NetworkManager"])
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Datos de simulación para modo dev
# ---------------------------------------------------------------------------

_DEV_INTERFACES = {
    "eth0": {
        "name": "eth0",
        "type": "ethernet",
        "up": True,
        "ip": "192.168.1.100",
        "netmask": "255.255.255.0",
        "gateway": "192.168.1.1",
        "dns": ["8.8.8.8", "8.8.4.4"],
        "mode": "dhcp",
        "mac": "b8:27:eb:aa:bb:cc",
        "speed": "100Mb/s",
    },
    "wlan0": {
        "name": "wlan0",
        "type": "wifi",
        "up": True,
        "ip": "192.168.4.1",
        "netmask": "255.255.255.0",
        "gateway": None,
        "dns": ["8.8.8.8"],
        "mode": "static",
        "mac": "b8:27:eb:dd:ee:ff",
        "ssid": "RPI-Setup",
        "frequency": "2.4GHz",
        "signal": -50,
    },
    "lo": {
        "name": "lo",
        "type": "loopback",
        "up": True,
        "ip": "127.0.0.1",
        "netmask": "255.0.0.0",
        "gateway": None,
        "dns": [],
        "mode": "static",
        "mac": "00:00:00:00:00:00",
    },
}

_DEV_WIFI_NETWORKS = [
    {"ssid": "MiCasaWifi", "bssid": "aa:bb:cc:11:22:33", "signal": -45, "channel": 6, "frequency": "2.4GHz", "security": "WPA2"},
    {"ssid": "Vecinos_5G", "bssid": "aa:bb:cc:44:55:66", "signal": -70, "channel": 36, "frequency": "5GHz", "security": "WPA2"},
    {"ssid": "OpenNetwork", "bssid": "aa:bb:cc:77:88:99", "signal": -80, "channel": 11, "frequency": "2.4GHz", "security": "Open"},
    {"ssid": "Fibra_Optica_2.4", "bssid": "aa:bb:cc:aa:bb:cc", "signal": -55, "channel": 1, "frequency": "2.4GHz", "security": "WPA2"},
]

_DEV_DHCP_LEASES = [
    {"mac": "dc:a6:32:11:22:33", "ip": "192.168.4.11", "hostname": "android-phone", "expiry": "1711670400"},
    {"mac": "3c:22:fb:44:55:66", "ip": "192.168.4.12", "hostname": "laptop-casa", "expiry": "1711670400"},
]


# ---------------------------------------------------------------------------
# Clase principal
# ---------------------------------------------------------------------------

class NetworkManager:
    """
    Gestiona la configuración de red de la Raspberry Pi.
    Detecta automáticamente el backend de red disponible.
    """

    def __init__(self):
        self.use_network_manager = _has_network_manager() if not DEV_MODE else False
        logger.info(
            "NetworkManager iniciado. Dev=%s, NM=%s",
            DEV_MODE,
            self.use_network_manager,
        )

    # ------------------------------------------------------------------
    # Interfaces
    # ------------------------------------------------------------------

    def get_interfaces(self) -> List[Dict[str, Any]]:
        """
        Detecta todas las interfaces de red disponibles usando 'ip link show'.
        Complementa con información inalámbrica de 'iw dev'.
        """
        if DEV_MODE:
            return list(_DEV_INTERFACES.values())

        interfaces = []
        # Obtener lista de interfaces con 'ip link show'
        result = _run(["ip", "link", "show"])
        if result.returncode != 0:
            logger.error("Error ejecutando 'ip link show': %s", result.stderr)
            return []

        # Parsear interfaces
        iface_pattern = re.compile(r"^\d+:\s+(\S+?)(?:@\S+)?:\s+<([^>]*)>", re.MULTILINE)
        for match in iface_pattern.finditer(result.stdout):
            iface_name = match.group(1)
            flags = match.group(2)
            if iface_name == "lo":
                continue  # Omitir loopback
            if iface_name.startswith(("br-", "veth", "docker", "virbr")):
                continue  # Omitir interfaces virtuales de Docker/libvirt
            iface_info = self.get_interface_config(iface_name)
            iface_info["up"] = "UP" in flags
            interfaces.append(iface_info)

        return interfaces

    def get_interface_config(self, iface: str) -> Dict[str, Any]:
        """
        Obtiene la configuración completa de una interfaz: IP, máscara, gateway, DNS, modo.
        """
        if DEV_MODE:
            return _DEV_INTERFACES.get(iface, {
                "name": iface, "type": "unknown", "up": False,
                "ip": None, "netmask": None, "gateway": None,
                "dns": [], "mode": "unknown", "mac": "00:00:00:00:00:00",
            })

        info: Dict[str, Any] = {
            "name": iface,
            "type": self._detect_iface_type(iface),
            "up": False,
            "ip": None,
            "netmask": None,
            "broadcast": None,
            "gateway": None,
            "dns": [],
            "mode": "unknown",
            "mac": None,
        }

        # IP y MAC con 'ip addr show <iface>'
        result = _run(["ip", "addr", "show", iface])
        if result.returncode == 0:
            # MAC
            mac_match = re.search(r"link/\w+\s+([\da-f:]{17})", result.stdout)
            if mac_match:
                info["mac"] = mac_match.group(1)

            # Flags UP
            if "UP" in result.stdout.split("\n")[0]:
                info["up"] = True

            # IPv4
            ip_match = re.search(r"inet\s+([\d.]+)(?:/(\d+))?", result.stdout)
            if ip_match:
                info["ip"] = ip_match.group(1)
                prefix = int(ip_match.group(2)) if ip_match.group(2) else 24
                info["netmask"] = self._prefix_to_netmask(prefix)

        # Gateway con 'ip route show dev <iface>'
        route_result = _run(["ip", "route", "show", "dev", iface])
        if route_result.returncode == 0:
            gw_match = re.search(r"default\s+via\s+([\d.]+)", route_result.stdout)
            if gw_match:
                info["gateway"] = gw_match.group(1)

        # DNS desde /etc/resolv.conf
        info["dns"] = self._get_dns_servers()

        # Modo (DHCP o estático): revisar dhcpcd.conf
        info["mode"] = self._detect_ip_mode(iface)

        # Info WiFi adicional
        if info["type"] == "wifi":
            wifi_info = self._get_wifi_info(iface)
            info.update(wifi_info)

        return info

    def _detect_iface_type(self, iface: str) -> str:
        """Determina si la interfaz es wifi, ethernet o loopback."""
        if iface.startswith("lo"):
            return "loopback"
        if iface.startswith(("wlan", "wlp", "wlx")):
            return "wifi"
        if iface.startswith(("eth", "enp", "ens", "enx")):
            return "ethernet"
        # Verificar con iw
        result = _run(["iw", "dev", iface, "info"])
        if result.returncode == 0:
            return "wifi"
        return "ethernet"

    def _get_wifi_info(self, iface: str) -> Dict[str, Any]:
        """Obtiene SSID, frecuencia y señal de una interfaz WiFi."""
        info = {"ssid": None, "frequency": None, "signal": None, "signal_dbm": None, "signal_quality": None, "signal_label": None}
        result = _run(["iw", iface, "link"])
        if result.returncode == 0 and "Connected" in result.stdout:
            ssid_match = re.search(r"SSID:\s*(.+)", result.stdout)
            if ssid_match:
                info["ssid"] = ssid_match.group(1).strip()
            freq_match = re.search(r"freq:\s*(\d+)", result.stdout)
            if freq_match:
                freq_mhz = int(freq_match.group(1))
                info["frequency"] = "5GHz" if freq_mhz > 4000 else "2.4GHz"
                info["freq_mhz"] = freq_mhz
            signal_match = re.search(r"signal:\s*(-?\d+)", result.stdout)
            if signal_match:
                dbm = int(signal_match.group(1))
                info["signal"] = dbm
                info["signal_dbm"] = dbm
                info["signal_quality"] = max(0, min(100, 2 * (dbm + 100)))
                if dbm > -50:
                    info["signal_label"] = "Excelente"
                elif dbm > -65:
                    info["signal_label"] = "Bueno"
                elif dbm > -75:
                    info["signal_label"] = "Regular"
                else:
                    info["signal_label"] = "Débil"
        return info

    def _detect_ip_mode(self, iface: str) -> str:
        """
        Determina si la interfaz usa DHCP o IP estática.
        Revisa dhcpcd.conf buscando la sección de la interfaz.
        """
        if not os.path.exists(DHCPCD_CONF):
            return "dhcp"  # Por defecto asumimos DHCP
        try:
            with open(DHCPCD_CONF, "r") as f:
                content = f.read()
            # Busca 'interface <iface>' con bloque static ip_address
            pattern = rf"interface\s+{re.escape(iface)}.*?static\s+ip_address"
            if re.search(pattern, content, re.DOTALL):
                return "static"
        except Exception as exc:
            logger.warning("Error leyendo dhcpcd.conf: %s", exc)
        return "dhcp"

    def _get_dns_servers(self) -> List[str]:
        """Lee los servidores DNS desde /etc/resolv.conf."""
        dns = []
        try:
            with open("/etc/resolv.conf", "r") as f:
                for line in f:
                    match = re.match(r"nameserver\s+([\d.]+)", line.strip())
                    if match:
                        dns.append(match.group(1))
        except Exception:
            pass
        return dns

    @staticmethod
    def _prefix_to_netmask(prefix: int) -> str:
        """Convierte longitud de prefijo CIDR a notación decimal."""
        mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
        return ".".join(str((mask >> (8 * i)) & 0xFF) for i in reversed(range(4)))

    @staticmethod
    def _netmask_to_prefix(netmask: str) -> int:
        """Convierte máscara de red en notación decimal a longitud de prefijo."""
        return sum(bin(int(x)).count("1") for x in netmask.split("."))

    # ------------------------------------------------------------------
    # Configurar IP estática
    # ------------------------------------------------------------------

    def set_static_ip(
        self,
        iface: str,
        ip: str,
        netmask: str = "255.255.255.0",
        gateway: str = "",
        dns: List[str] = None,
    ) -> Dict[str, Any]:
        """
        Configura una IP estática para la interfaz especificada en /etc/dhcpcd.conf.
        Hace una copia de seguridad antes de modificar.
        """
        if not dns:
            dns = ["8.8.8.8", "8.8.4.4"]

        if DEV_MODE:
            logger.info(
                "[DEV] set_static_ip simulado: %s %s/%s gw=%s dns=%s",
                iface, ip, netmask, gateway, dns,
            )
            if iface in _DEV_INTERFACES:
                _DEV_INTERFACES[iface].update({
                    "ip": ip, "netmask": netmask,
                    "gateway": gateway or None,
                    "dns": dns, "mode": "static",
                })
            return {"success": True, "simulated": True, "iface": iface, "ip": ip}

        # Validaciones básicas
        if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
            return {"success": False, "error": f"IP inválida: {ip}"}

        try:
            prefix = self._netmask_to_prefix(netmask)
        except Exception:
            return {"success": False, "error": f"Máscara de red inválida: {netmask}"}

        try:
            self._backup_file(DHCPCD_CONF)
            content = ""
            if os.path.exists(DHCPCD_CONF):
                with open(DHCPCD_CONF, "r") as f:
                    content = f.read()

            # Eliminar bloque existente para esta interfaz
            content = self._remove_iface_block(content, iface)

            # Agregar nuevo bloque estático
            dns_str = " ".join(dns)
            block = f"\ninterface {iface}\nstatic ip_address={ip}/{prefix}\n"
            if gateway:
                block += f"static routers={gateway}\n"
            block += f"static domain_name_servers={dns_str}\n"
            content += block

            with open(DHCPCD_CONF, "w") as f:
                f.write(content)

            logger.info("IP estática configurada: %s en %s", ip, iface)
            return {"success": True, "iface": iface, "ip": ip, "prefix": prefix, "gateway": gateway}

        except PermissionError:
            return {"success": False, "error": "Permisos insuficientes. Ejecutar como root."}
        except Exception as exc:
            logger.error("Error configurando IP estática: %s", exc)
            self._restore_backup(DHCPCD_CONF)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Configurar DHCP
    # ------------------------------------------------------------------

    def set_dhcp(self, iface: str) -> Dict[str, Any]:
        """
        Configura una interfaz para usar DHCP eliminando la configuración
        estática de /etc/dhcpcd.conf.
        """
        if DEV_MODE:
            logger.info("[DEV] set_dhcp simulado para %s", iface)
            if iface in _DEV_INTERFACES:
                _DEV_INTERFACES[iface]["mode"] = "dhcp"
            return {"success": True, "simulated": True, "iface": iface, "mode": "dhcp"}

        try:
            self._backup_file(DHCPCD_CONF)
            if not os.path.exists(DHCPCD_CONF):
                return {"success": True, "iface": iface, "mode": "dhcp"}

            with open(DHCPCD_CONF, "r") as f:
                content = f.read()

            content = self._remove_iface_block(content, iface)

            with open(DHCPCD_CONF, "w") as f:
                f.write(content)

            logger.info("DHCP configurado para %s", iface)
            return {"success": True, "iface": iface, "mode": "dhcp"}

        except PermissionError:
            return {"success": False, "error": "Permisos insuficientes. Ejecutar como root."}
        except Exception as exc:
            logger.error("Error configurando DHCP: %s", exc)
            self._restore_backup(DHCPCD_CONF)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Escaneo WiFi
    # ------------------------------------------------------------------

    def scan_wifi(self, iface: str = "wlan0") -> List[Dict[str, Any]]:
        """
        Escanea las redes WiFi disponibles.
        Usa 'nmcli' si está disponible, de lo contrario 'iwlist scan'.
        """
        if DEV_MODE:
            logger.info("[DEV] scan_wifi simulado en %s", iface)
            return _DEV_WIFI_NETWORKS

        networks = []

        # Asegurar que la interfaz esté UP antes de escanear
        iface_check = _run(["ip", "link", "show", iface])
        if "state DOWN" in iface_check.stdout:
            _run(["ip", "link", "set", iface, "up"])
            import time; time.sleep(1)

        # Intento 1: nmcli (NetworkManager)
        if shutil.which("nmcli"):
            _run(["sudo", "nmcli", "device", "wifi", "rescan", "ifname", iface])
            import time; time.sleep(2)
            networks = self._scan_with_nmcli(iface)
            if networks:
                return networks

        # Intento 2: iwlist scan
        if shutil.which("iwlist"):
            networks = self._scan_with_iwlist(iface)
            if networks:
                return networks

        logger.warning("No se pudo escanear redes WiFi: nmcli e iwlist no disponibles")
        return []

    def _scan_with_nmcli(self, iface: str) -> List[Dict[str, Any]]:
        """Usa nmcli --terse para escanear redes WiFi. Separador | evita ambigüedad con SSIDs con espacios."""
        result = _run(["nmcli", "--terse", "--fields", "SSID,BSSID,SIGNAL,CHAN,FREQ,SECURITY",
                        "device", "wifi", "list", "ifname", iface], timeout=15)
        if result.returncode != 0:
            return []

        networks = []
        seen_bssids = set()
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            # --terse usa ':' como separador pero escapa ':' internos con '\:'
            # Dividimos solo en ':' no escapados
            parts = re.split(r'(?<!\\):', line)
            if len(parts) < 6:
                continue

            ssid     = parts[0].replace("\\:", ":").strip()
            bssid    = parts[1].replace("\\:", ":").strip()
            sig_str  = parts[2].strip()
            chan_str = parts[3].strip()
            freq_str = parts[4].strip()   # ej: "2417 MHz"
            security = parts[5].replace("\\:", ":").strip()

            if not ssid or ssid == "--":
                continue
            if not re.match(r'^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$', bssid):
                continue
            if bssid in seen_bssids:
                continue
            seen_bssids.add(bssid)

            # SIGNAL es porcentaje 0-100; convertir a dBm aproximado: dBm = (pct/2) - 100
            try:
                pct = int(sig_str)
            except ValueError:
                pct = 0
            signal_dbm = (pct // 2) - 100  # ej: 100% → -50 dBm, 70% → -65 dBm

            channel = int(chan_str) if chan_str.isdigit() else None

            frequency = None
            freq_match = re.search(r'([\d.]+)', freq_str)
            if freq_match:
                try:
                    freq_mhz = float(freq_match.group(1))
                    frequency = "5GHz" if freq_mhz >= 4000 else "2.4GHz"
                except ValueError:
                    pass

            if signal_dbm > -50:
                signal_label = "Excelente"
            elif signal_dbm > -65:
                signal_label = "Bueno"
            elif signal_dbm > -75:
                signal_label = "Regular"
            else:
                signal_label = "Débil"

            networks.append({
                "ssid": ssid,
                "bssid": bssid,
                "signal": signal_dbm,
                "signal_pct": pct,
                "channel": channel,
                "frequency": frequency,
                "security": security or "Open",
                "signal_label": signal_label,
            })

        # Ordenar por señal descendente
        networks.sort(key=lambda n: n["signal"], reverse=True)
        return networks

    def _scan_with_iwlist(self, iface: str) -> List[Dict[str, Any]]:
        """Usa iwlist para escanear redes WiFi."""
        result = _run(["sudo", "iwlist", iface, "scan"], timeout=15)
        if result.returncode != 0:
            return []

        networks = []
        current: Dict[str, Any] = {}
        for line in result.stdout.split("\n"):
            line = line.strip()
            if line.startswith("Cell"):
                if current:
                    networks.append(current)
                current = {}
                bssid_match = re.search(r"Address:\s*([\dA-F:]+)", line, re.I)
                if bssid_match:
                    current["bssid"] = bssid_match.group(1)
            elif "ESSID:" in line:
                ssid_match = re.search(r'ESSID:"(.*?)"', line)
                if ssid_match:
                    current["ssid"] = ssid_match.group(1)
            elif "Frequency:" in line:
                freq_match = re.search(r"Frequency:([\d.]+)", line)
                if freq_match:
                    freq = float(freq_match.group(1))
                    current["frequency"] = "5GHz" if freq > 4.0 else "2.4GHz"
                    current["freq_ghz"] = freq
            elif "Channel:" in line:
                ch_match = re.search(r"Channel:(\d+)", line)
                if ch_match:
                    current["channel"] = int(ch_match.group(1))
            elif "Signal level=" in line:
                sig_match = re.search(r"Signal level=(-?\d+)", line)
                if sig_match:
                    current["signal"] = int(sig_match.group(1))
            elif "Encryption key:" in line:
                current["security"] = "WPA" if "on" in line.lower() else "Open"
        if current:
            networks.append(current)
        return networks

    # ------------------------------------------------------------------
    # Credenciales WiFi (WPA Supplicant)
    # ------------------------------------------------------------------

    def set_wifi_credentials(
        self, ssid: str, password: str, iface: str = "wlan0", country: str = "ES"
    ) -> Dict[str, Any]:
        """
        Conecta la interfaz WiFi a una red.
        Usa NetworkManager (nmcli) si está disponible, sino wpa_supplicant.
        """
        if DEV_MODE:
            logger.info("[DEV] set_wifi_credentials simulado: SSID=%s iface=%s", ssid, iface)
            return {"success": True, "simulated": True, "ssid": ssid}

        if shutil.which("nmcli"):
            return self._set_wifi_nmcli(ssid, password, iface)
        return self._set_wifi_wpa_supplicant(ssid, password, iface, country)

    def _set_wifi_nmcli(self, ssid: str, password: str, iface: str) -> Dict[str, Any]:
        """Conecta via NetworkManager usando connection add+modify+up (headless-safe).
        No usa 'device wifi connect' para evitar dependencia del agente de secretos (polkit)."""
        _wifi_debug_log(f"=== INICIO CONEXION WiFi === SSID: {ssid}, Interfaz: {iface}", "INFO")

        try:
            def _connect():
                if not _wifi_lock.acquire(blocking=False):
                    _wifi_debug_log(f"Conexión en curso, ignorando nuevo intento para '{ssid}'", "WARN")
                    return
                try:
                    pw = password.strip() if password else ""
                    if password and pw != password:
                        _wifi_debug_log("ADVERTENCIA: La contraseña tenía espacios al inicio/final, se recortaron", "WARN")

                    # 1. Deshabilitar autoconnect en todas las conexiones WiFi existentes
                    _wifi_debug_log("1. Deshabilitando autoconnect en otras conexiones WiFi...", "DEBUG")
                    res = _run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
                    for line in res.stdout.strip().split("\n"):
                        parts = line.split(":")
                        if len(parts) >= 2 and parts[1] == "802-11-wireless":
                            _run(["nmcli", "connection", "modify", parts[0], "connection.autoconnect", "no"])
                            _wifi_debug_log(f"   autoconnect=no en: {parts[0]}", "DEBUG")

                    # 2. Eliminar perfil anterior con el mismo nombre si existe
                    _wifi_debug_log(f"2. Eliminando perfil anterior '{ssid}' si existe...", "DEBUG")
                    _run(["nmcli", "connection", "delete", ssid])

                    # 3. Crear perfil nuevo
                    _wifi_debug_log(f"3. Creando perfil '{ssid}'...", "DEBUG")
                    add_res = _run(["nmcli", "connection", "add",
                                    "type", "wifi",
                                    "con-name", ssid,
                                    "ifname", iface,
                                    "ssid", ssid])
                    _wifi_debug_log(f"   add: rc={add_res.returncode} {add_res.stderr.strip()[:150]}", "DEBUG")
                    if add_res.returncode != 0:
                        _wifi_debug_log(f"ERROR creando perfil: {add_res.stderr.strip()}", "ERROR")
                        return

                    # 4. Inyectar credenciales y ajustes de seguridad directamente (sin agente)
                    _wifi_debug_log(f"4. Configurando seguridad WPA2-PSK (sin agente)...", "DEBUG")
                    modify_args = ["nmcli", "connection", "modify", ssid,
                                   "connection.autoconnect", "yes",
                                   "connection.autoconnect-priority", "10"]
                    if pw:
                        modify_args += [
                            "wifi-sec.key-mgmt", "wpa-psk",
                            "wifi-sec.psk", pw,
                            "wifi-sec.psk-flags", "0",   # guardar en disco, sin agente
                            "wifi-sec.pmf", "1",          # desactivar PMF (evita problemas SAE/WPA3)
                        ]
                    else:
                        modify_args += ["802-11-wireless-security.key-mgmt", "none"]

                    mod_res = _run(modify_args)
                    _wifi_debug_log(f"   modify: rc={mod_res.returncode} {mod_res.stderr.strip()[:150]}", "DEBUG")
                    if mod_res.returncode != 0:
                        _wifi_debug_log(f"ERROR modificando perfil: {mod_res.stderr.strip()}", "ERROR")

                    # 5. Activar la conexión
                    _wifi_debug_log(f"5. Activando conexión '{ssid}' en {iface}...", "INFO")
                    up_res = _run(["nmcli", "connection", "up", ssid, "ifname", iface], timeout=45)
                    _wifi_debug_log(f"   up: rc={up_res.returncode}", "INFO")
                    _wifi_debug_log(f"   stdout: {up_res.stdout.strip()[:200]}", "DEBUG")
                    _wifi_debug_log(f"   stderr: {up_res.stderr.strip()[:200]}", "DEBUG")

                    if up_res.returncode == 0:
                        _wifi_debug_log(f"CONEXION EXITOSA a '{ssid}'", "INFO")
                        logger.info("Conexión WiFi exitosa a '%s'", ssid)
                    else:
                        err = up_res.stderr.strip()
                        _wifi_debug_log(f"FALLO al activar '{ssid}': {err}", "ERROR")
                        logger.error("Error activando conexión '%s': %s", ssid, err)
                        # Limpiar el perfil fallido para no dejar basura
                        _run(["nmcli", "connection", "delete", ssid])

                finally:
                    _wifi_lock.release()
                    _wifi_debug_log("=== FIN INTENTO CONEXION WiFi ===", "DEBUG")

            threading.Thread(target=_connect, daemon=True).start()
            return {"success": True, "ssid": ssid, "iface": iface, "connecting": True}
        except Exception as exc:
            _wifi_debug_log(f"Excepción en _set_wifi_nmcli: {exc}", "ERROR")
            logger.error("Error iniciando conexión WiFi: %s", exc)
            return {"success": False, "error": str(exc)}

    def _set_wifi_wpa_supplicant(self, ssid: str, password: str, iface: str, country: str) -> Dict[str, Any]:
        """Conecta via wpa_supplicant."""
        try:
            self._backup_file(WPA_SUPPLICANT_CONF)
            content = f"""ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country={country}

network={{
    ssid="{ssid}"
    psk="{password}"
    key_mgmt=WPA-PSK
    scan_ssid=1
}}
"""
            with open(WPA_SUPPLICANT_CONF, "w") as f:
                f.write(content)
            _run(["wpa_cli", "-i", iface, "reconfigure"])
            logger.info("Credenciales WiFi configuradas para SSID: %s", ssid)
            return {"success": True, "ssid": ssid, "iface": iface}
        except PermissionError:
            return {"success": False, "error": "Permisos insuficientes. Ejecutar como root."}
        except Exception as exc:
            logger.error("Error configurando WiFi: %s", exc)
            self._restore_backup(WPA_SUPPLICANT_CONF)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Estado de conexiones
    # ------------------------------------------------------------------

    def get_current_connections(self) -> Dict[str, Any]:
        """
        Devuelve el estado actual de todas las conexiones de red activas.
        """
        if DEV_MODE:
            return {
                "interfaces": list(_DEV_INTERFACES.values()),
                "default_gateway": "192.168.1.1",
                "dns_servers": ["8.8.8.8", "8.8.4.4"],
            }

        interfaces = self.get_interfaces()
        gateway = self._get_default_gateway()
        dns = self._get_dns_servers()
        return {
            "interfaces": interfaces,
            "default_gateway": gateway,
            "dns_servers": dns,
        }

    def _get_default_gateway(self) -> Optional[str]:
        """Obtiene el gateway por defecto del sistema."""
        result = _run(["ip", "route", "show", "default"])
        if result.returncode == 0:
            match = re.search(r"default via ([\d.]+)", result.stdout)
            if match:
                return match.group(1)
        return None

    # ------------------------------------------------------------------
    # Reiniciar servicios de red
    # ------------------------------------------------------------------

    def restart_networking(self) -> Dict[str, Any]:
        """
        Reinicia los servicios de red disponibles.
        Intenta en orden: NetworkManager, dhcpcd, networking.
        """
        if DEV_MODE:
            logger.info("[DEV] restart_networking simulado")
            return {"success": True, "simulated": True, "restarted": SERVICES_NETWORKING}

        restarted = []
        errors = []
        for service in SERVICES_NETWORKING:
            result = _run(["systemctl", "is-enabled", service])
            if result.returncode == 0:
                restart = _run(["sudo", "systemctl", "restart", service])
                if restart.returncode == 0:
                    restarted.append(service)
                    logger.info("Servicio reiniciado: %s", service)
                else:
                    errors.append(f"{service}: {restart.stderr.strip()}")

        return {
            "success": len(errors) == 0,
            "restarted": restarted,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # DHCP Leases
    # ------------------------------------------------------------------

    def get_dhcp_leases(self) -> List[Dict[str, Any]]:
        """
        Lee los arrendamientos DHCP activos desde el archivo de leases de dnsmasq.
        Formato: <epoch_expiry> <mac> <ip> <hostname> <client_id>
        """
        if DEV_MODE:
            logger.info("[DEV] get_dhcp_leases simulado")
            return _DEV_DHCP_LEASES

        leases = []
        if not os.path.exists(DNSMASQ_LEASES):
            logger.info("Archivo de leases no encontrado: %s", DNSMASQ_LEASES)
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
                            "client_id": parts[4] if len(parts) > 4 else "",
                        })
        except Exception as exc:
            logger.error("Error leyendo leases DHCP: %s", exc)

        return leases

    # ------------------------------------------------------------------
    # Configuración completa de red (GET/SET)
    # ------------------------------------------------------------------

    def get_interfaces_with_status(self) -> List[Dict[str, Any]]:
        """
        Igual que get_interfaces() pero agrega has_ip, has_gateway, has_internet por interfaz.
        """
        interfaces = self.get_interfaces()
        internet = check_internet()
        has_internet = internet.get("online", False)
        latency = internet.get("latency_ms")
        for iface in interfaces:
            iface["has_ip"] = iface.get("ip") is not None
            iface["has_gateway"] = iface.get("gateway") is not None
            # Si tiene IP y gateway, asumir internet igual al check global
            if iface.get("ip") and iface.get("gateway"):
                iface["has_internet"] = has_internet
                iface["internet_latency_ms"] = latency
            else:
                iface["has_internet"] = False
                iface["internet_latency_ms"] = None
        return interfaces

    def get_network_config(self) -> Dict[str, Any]:
        """
        Devuelve la configuración de red completa del sistema.
        """
        return {
            "interfaces": self.get_interfaces(),
            "connections": self.get_current_connections(),
            "leases": self.get_dhcp_leases(),
        }

    def apply_network_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Aplica una configuración de red completa.
        Primero valida, luego aplica. Si falla, restaura el backup.

        config espera:
        {
            "iface": "eth0",
            "mode": "static" | "dhcp",
            "ip": "...",          # solo si mode==static
            "netmask": "...",     # solo si mode==static
            "gateway": "...",     # solo si mode==static
            "dns": ["...", ...]   # solo si mode==static
        }
        """
        iface = config.get("iface")
        mode = config.get("mode", "dhcp")

        if not iface:
            return {"success": False, "error": "Falta el campo 'iface'."}

        if mode == "static":
            ip = config.get("ip")
            netmask = config.get("netmask", "255.255.255.0")
            gateway = config.get("gateway", "")
            dns = config.get("dns", ["8.8.8.8", "8.8.4.4"])
            if not ip:
                return {"success": False, "error": "Se requiere 'ip' para modo estático."}
            result = self.set_static_ip(iface, ip, netmask, gateway, dns)
        else:
            result = self.set_dhcp(iface)

        if result.get("success"):
            restart_result = self.restart_networking()
            result["networking_restarted"] = restart_result
        return result

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

    @staticmethod
    def _remove_iface_block(content: str, iface: str) -> str:
        """
        Elimina el bloque de configuración de una interfaz específica en dhcpcd.conf.
        El bloque empieza con 'interface <iface>' y termina antes del siguiente 'interface'.
        """
        pattern = rf"\ninterface\s+{re.escape(iface)}\n(?:(?!interface).*\n)*"
        return re.sub(pattern, "\n", content)
