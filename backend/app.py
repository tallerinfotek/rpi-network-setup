"""
app.py - Flask app principal del Access Point Configurator.

Expone una API REST completa para:
  - Estado del sistema y de la red
  - Configuración de interfaces (IP estática / DHCP)
  - Escaneo y configuración WiFi
  - Gestión del Access Point (hostapd/dnsmasq)
  - Información del sistema (CPU, RAM, temperatura, uptime)
  - Hostname y reinicio

Sirve también el frontend compilado (Vite/React) desde ../frontend/dist.
"""

import logging
import os
from functools import wraps
from typing import Any, Dict, Tuple

from flask import Flask, jsonify, request, send_from_directory, abort
from flask_cors import CORS

from config import (
    AP_IP,
    DEV_MODE,
    FRONTEND_DIR,
    FRONTEND_DIST_DIR,
    SERVER_DEBUG,
    SERVER_HOST,
    SERVER_PORT,
    LOG_FORMAT,
    LOG_LEVEL,
)
from ap_manager import APManager
from network_manager import NetworkManager
from server_manager import ServerManager
import update_manager
from system_info import (
    get_full_system_info,
    get_hostname,
    get_temperature,
    set_hostname,
    reboot,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=getattr(logging, LOG_LEVEL, "INFO"), format=LOG_FORMAT)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Instancias de managers
# ---------------------------------------------------------------------------

nm = NetworkManager()
ap = APManager()
sm = ServerManager()

# ---------------------------------------------------------------------------
# Configuración Flask
# ---------------------------------------------------------------------------

# Determinar directorio de archivos estáticos del frontend
_static_folder: str = None
if os.path.isdir(FRONTEND_DIST_DIR):
    _static_folder = FRONTEND_DIST_DIR
    logger.info("Frontend servido desde: %s", FRONTEND_DIST_DIR)
elif os.path.isdir(FRONTEND_DIR):
    _static_folder = FRONTEND_DIR
    logger.info("Frontend servido desde: %s", FRONTEND_DIR)
else:
    logger.warning(
        "No se encontró directorio del frontend. "
        "Solo disponible la API. (Buscado en: %s)",
        FRONTEND_DIST_DIR,
    )

app = Flask(
    __name__,
    static_folder=_static_folder,
    static_url_path="",
)

# CORS habilitado para todos los orígenes en desarrollo
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ---------------------------------------------------------------------------
# Helpers de respuesta
# ---------------------------------------------------------------------------


def ok(data: Any = None, message: str = "OK", code: int = 200) -> Tuple[Any, int]:
    """Respuesta JSON exitosa estándar."""
    payload = {"success": True, "message": message}
    if data is not None:
        payload["data"] = data
    return jsonify(payload), code


def err(message: str, code: int = 400, details: Any = None) -> Tuple[Any, int]:
    """Respuesta JSON de error estándar."""
    payload = {"success": False, "error": message}
    if details is not None:
        payload["details"] = details
    return jsonify(payload), code


def require_json(f):
    """Decorador que valida que la petición contenga JSON."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not request.is_json:
            return err("El cuerpo de la petición debe ser JSON (Content-Type: application/json).", 415)
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Rutas estáticas (frontend)
# ---------------------------------------------------------------------------


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path: str):
    """
    Sirve el frontend React/Vite compilado.
    Para rutas de la SPA que no son archivos reales, devuelve index.html.
    """
    if _static_folder is None:
        return jsonify({
            "message": "Access Point Configurator API",
            "version": "1.0.0",
            "docs": "/api/status",
            "dev_mode": DEV_MODE,
            "ap_ip": AP_IP,
        })

    # Si el path apunta a un archivo real, servirlo
    full_path = os.path.join(_static_folder, path)
    if path and os.path.exists(full_path) and os.path.isfile(full_path):
        return send_from_directory(_static_folder, path)

    # Para todas las demás rutas, devolver index.html (SPA routing)
    index = os.path.join(_static_folder, "index.html")
    if os.path.exists(index):
        return send_from_directory(_static_folder, "index.html")

    return jsonify({"message": "Frontend no encontrado. Use /api/status para acceder a la API."}), 404


# ---------------------------------------------------------------------------
# API: Estado general
# ---------------------------------------------------------------------------


@app.route("/api/status", methods=["GET"])
def api_status():
    """
    GET /api/status
    Estado actual de todas las interfaces de red y del sistema.
    """
    try:
        interfaces = nm.get_interfaces()
        ap_status = ap.get_ap_status()
        sys_info = get_full_system_info()

        return ok({
            "interfaces": interfaces,
            "ap": ap_status,
            "system": sys_info,
            "dev_mode": DEV_MODE,
            "ap_ip": AP_IP,
        })
    except Exception as exc:
        logger.exception("Error en /api/status")
        return err(f"Error obteniendo estado: {exc}", 500)


# ---------------------------------------------------------------------------
# API: Interfaces de red
# ---------------------------------------------------------------------------


@app.route("/api/network/interfaces", methods=["GET"])
def api_network_interfaces():
    """
    GET /api/network/interfaces
    Lista todas las interfaces de red disponibles con su configuración.
    """
    try:
        interfaces = nm.get_interfaces()
        return ok(interfaces)
    except Exception as exc:
        logger.exception("Error en /api/network/interfaces")
        return err(f"Error listando interfaces: {exc}", 500)


# ---------------------------------------------------------------------------
# API: Configuración de red
# ---------------------------------------------------------------------------


@app.route("/api/network/config", methods=["GET"])
def api_network_config_get():
    """
    GET /api/network/config
    Configuración completa de red: interfaces, conexiones y leases DHCP.
    """
    try:
        config = nm.get_network_config()
        return ok(config)
    except Exception as exc:
        logger.exception("Error en GET /api/network/config")
        return err(f"Error obteniendo configuración de red: {exc}", 500)


@app.route("/api/network/config", methods=["POST"])
@require_json
def api_network_config_post():
    """
    POST /api/network/config
    Guarda la configuración de red de una interfaz específica.

    Body JSON:
    {
        "iface": "eth0",
        "mode": "static" | "dhcp",
        "ip": "192.168.1.100",        (requerido si mode=static)
        "netmask": "255.255.255.0",   (opcional, defecto 255.255.255.0)
        "gateway": "192.168.1.1",     (opcional)
        "dns": ["8.8.8.8", "8.8.4.4"] (opcional)
    }
    """
    data = request.get_json()
    try:
        result = nm.apply_network_config(data)
        if result.get("success"):
            return ok(result, "Configuración de red guardada.")
        return err(result.get("error", "Error desconocido."), 400)
    except Exception as exc:
        logger.exception("Error en POST /api/network/config")
        return err(f"Error guardando configuración: {exc}", 500)


# ---------------------------------------------------------------------------
# API: Escanear WiFi
# ---------------------------------------------------------------------------


@app.route("/api/network/scan", methods=["GET"])
def api_network_scan():
    """
    GET /api/network/scan?iface=wlan0
    Escanea las redes WiFi disponibles en la interfaz indicada.
    """
    iface = request.args.get("iface", "wlan0")
    try:
        networks = nm.scan_wifi(iface)
        return ok({"networks": networks, "count": len(networks), "iface": iface})
    except Exception as exc:
        logger.exception("Error en /api/network/scan")
        return err(f"Error escaneando redes WiFi: {exc}", 500)


# ---------------------------------------------------------------------------
# API: Aplicar configuración de red
# ---------------------------------------------------------------------------


@app.route("/api/network/apply", methods=["POST"])
@require_json
def api_network_apply():
    """
    POST /api/network/apply
    Aplica la configuración de red y reinicia los servicios necesarios.

    Body JSON: igual que /api/network/config POST
    También acepta:
    {
        "wifi": {
            "ssid": "MiRed",
            "password": "clave",
            "iface": "wlan1"
        }
    }
    """
    data = request.get_json()

    results = {}
    try:
        # Aplicar configuración de interfaz si se especifica
        if "iface" in data:
            results["network"] = nm.apply_network_config(data)

        # Aplicar credenciales WiFi si se especifican
        if "wifi" in data:
            wifi = data["wifi"]
            results["wifi"] = nm.set_wifi_credentials(
                ssid=wifi.get("ssid", ""),
                password=wifi.get("password", ""),
                iface=wifi.get("iface", "wlan0"),
                country=wifi.get("country", "ES"),
            )

        if not results:
            return err("No se especificó ninguna configuración para aplicar.", 400)

        all_ok = all(r.get("success", False) for r in results.values())
        if all_ok:
            return ok(results, "Configuración aplicada correctamente.")
        return err("Algunos cambios fallaron.", 400, results)

    except Exception as exc:
        logger.exception("Error en /api/network/apply")
        return err(f"Error aplicando configuración: {exc}", 500)


# ---------------------------------------------------------------------------
# API: Hostname
# ---------------------------------------------------------------------------


@app.route("/api/hostname", methods=["GET"])
def api_hostname_get():
    """
    GET /api/hostname
    Devuelve el hostname actual del sistema.
    """
    try:
        hostname = get_hostname()
        return ok({"hostname": hostname})
    except Exception as exc:
        logger.exception("Error en GET /api/hostname")
        return err(f"Error obteniendo hostname: {exc}", 500)


@app.route("/api/hostname", methods=["POST"])
@require_json
def api_hostname_post():
    """
    POST /api/hostname
    Cambia el hostname del sistema.

    Body JSON: { "hostname": "nuevo-nombre" }
    """
    data = request.get_json()
    new_hostname = data.get("hostname", "").strip()
    if not new_hostname:
        return err("El campo 'hostname' es requerido.", 400)

    try:
        result = set_hostname(new_hostname)
        if result.get("success"):
            return ok(result, f"Hostname cambiado a '{new_hostname}'.")
        return err(result.get("error", "Error cambiando hostname."), 400)
    except Exception as exc:
        logger.exception("Error en POST /api/hostname")
        return err(f"Error cambiando hostname: {exc}", 500)


# ---------------------------------------------------------------------------
# API: Reinicio
# ---------------------------------------------------------------------------


@app.route("/api/reboot", methods=["POST"])
def api_reboot():
    """
    POST /api/reboot
    Reinicia el sistema Raspberry Pi.
    En modo dev simula el reinicio.
    """
    try:
        result = reboot()
        if result.get("success"):
            return ok(result, "El sistema se reiniciará en breve.")
        return err(result.get("error", "Error al reiniciar."), 500)
    except Exception as exc:
        logger.exception("Error en /api/reboot")
        return err(f"Error al reiniciar: {exc}", 500)


# ---------------------------------------------------------------------------
# API: Access Point
# ---------------------------------------------------------------------------


@app.route("/api/ap/config", methods=["GET"])
def api_ap_config_get():
    """
    GET /api/ap/config
    Devuelve la configuración actual del Access Point.
    """
    try:
        config = ap.get_ap_config()
        return ok(config)
    except Exception as exc:
        logger.exception("Error en GET /api/ap/config")
        return err(f"Error obteniendo configuración AP: {exc}", 500)


@app.route("/api/ap/config", methods=["POST"])
@require_json
def api_ap_config_post():
    """
    POST /api/ap/config
    Configura el Access Point.

    Body JSON:
    {
        "ssid": "MiRed",
        "password": "clave1234",   (vacío para red abierta)
        "channel": 6,
        "band": "2.4GHz" | "5GHz" | "g" | "a",
        "hidden": false,
        "interface": "wlan0",
        "country_code": "ES"
    }
    """
    data = request.get_json()
    try:
        result = ap.set_ap_config(
            ssid=data.get("ssid", "RPI-Setup"),
            password=data.get("password", ""),
            channel=int(data.get("channel", 6)),
            band=data.get("band", "g"),
            hidden=bool(data.get("hidden", False)),
            interface=data.get("interface", "wlan0"),
            country_code=data.get("country_code", "ES"),
        )
        if result.get("success"):
            return ok(result, "Configuración del AP guardada.")
        return err(result.get("error", "Error guardando AP config."), 400)
    except Exception as exc:
        logger.exception("Error en POST /api/ap/config")
        return err(f"Error configurando AP: {exc}", 500)


@app.route("/api/ap/status", methods=["GET"])
def api_ap_status():
    """
    GET /api/ap/status
    Estado actual del AP: activo/inactivo, clientes, configuración.
    """
    try:
        status = ap.get_ap_status()
        return ok(status)
    except Exception as exc:
        logger.exception("Error en /api/ap/status")
        return err(f"Error obteniendo estado del AP: {exc}", 500)


@app.route("/api/ap/start", methods=["POST"])
def api_ap_start():
    """
    POST /api/ap/start
    Inicia el Access Point (hostapd + dnsmasq).
    """
    try:
        result = ap.start_ap()
        if result.get("success"):
            return ok(result, "Access Point iniciado.")
        return err("Error iniciando el AP.", 500, result)
    except Exception as exc:
        logger.exception("Error en /api/ap/start")
        return err(f"Error iniciando AP: {exc}", 500)


@app.route("/api/ap/stop", methods=["POST"])
def api_ap_stop():
    """
    POST /api/ap/stop
    Detiene el Access Point.
    """
    try:
        result = ap.stop_ap()
        if result.get("success"):
            return ok(result, "Access Point detenido.")
        return err("Error deteniendo el AP.", 500, result)
    except Exception as exc:
        logger.exception("Error en /api/ap/stop")
        return err(f"Error deteniendo AP: {exc}", 500)


@app.route("/api/ap/restart", methods=["POST"])
def api_ap_restart():
    """
    POST /api/ap/restart
    Reinicia el Access Point.
    """
    try:
        result = ap.restart_ap()
        if result.get("success"):
            return ok(result, "Access Point reiniciado.")
        return err("Error reiniciando el AP.", 500, result)
    except Exception as exc:
        logger.exception("Error en /api/ap/restart")
        return err(f"Error reiniciando AP: {exc}", 500)


@app.route("/api/ap/state", methods=["POST"])
@require_json
def api_ap_state():
    """
    POST /api/ap/state
    Activa o desactiva el Access Point sin apagar el webserver.
    Body JSON: { "enabled": true | false }

    Cuando enabled=false: detiene hostapd y dnsmasq pero el webserver
    sigue escuchando en todas las interfaces (eth0, wlan0, etc.)

    Cuando enabled=true: reactiva hostapd y dnsmasq con la configuración
    actual de hostapd.conf (modo AP de configuración inicial).
    """
    data = request.get_json()
    enabled = data.get("enabled", False)

    try:
        if enabled:
            result = ap.start_ap()
            msg = "Access Point activado."
        else:
            result = ap.stop_ap()
            msg = "Access Point detenido. El configurador web sigue activo en la IP de red."

        if result.get("success"):
            return ok(result, msg)
        return err("Error cambiando estado del AP.", 500, result)
    except Exception as exc:
        logger.exception("Error en /api/ap/state")
        return err(f"Error cambiando estado del AP: {exc}", 500)


@app.route("/api/ap/setup", methods=["POST"])
def api_ap_setup():
    """
    POST /api/ap/setup
    Ejecuta la configuración inicial del AP (primer arranque).
    SSID=RPI-Setup, sin contraseña, IP=192.168.4.1.
    """
    try:
        result = ap.setup_initial_ap()
        if result.get("success"):
            return ok(result, "AP inicial configurado correctamente.")
        return err("Error en la configuración inicial del AP.", 500, result)
    except Exception as exc:
        logger.exception("Error en /api/ap/setup")
        return err(f"Error en setup inicial: {exc}", 500)


# ---------------------------------------------------------------------------
# API: DHCP Leases
# ---------------------------------------------------------------------------


@app.route("/api/dhcp/leases", methods=["GET"])
def api_dhcp_leases():
    """
    GET /api/dhcp/leases
    Devuelve la lista de clientes DHCP activos (arrendamientos de dnsmasq).
    """
    try:
        leases = nm.get_dhcp_leases()
        return ok({"leases": leases, "count": len(leases)})
    except Exception as exc:
        logger.exception("Error en /api/dhcp/leases")
        return err(f"Error obteniendo leases DHCP: {exc}", 500)


# ---------------------------------------------------------------------------
# API: Información del sistema
# ---------------------------------------------------------------------------


@app.route("/api/system/info", methods=["GET"])
def api_system_info():
    """
    GET /api/system/info
    Información completa del sistema: CPU, RAM, temperatura, uptime, disco, hostname.
    """
    try:
        info = get_full_system_info()
        return ok(info)
    except Exception as exc:
        logger.exception("Error en /api/system/info")
        return err(f"Error obteniendo info del sistema: {exc}", 500)


@app.route("/api/system/temperature", methods=["GET"])
def api_system_temperature():
    """
    GET /api/system/temperature
    Temperatura actual de la CPU.
    """
    try:
        temp = get_temperature()
        return ok(temp)
    except Exception as exc:
        logger.exception("Error en /api/system/temperature")
        return err(f"Error obteniendo temperatura: {exc}", 500)


# ---------------------------------------------------------------------------
# API: WiFi Credentials
# ---------------------------------------------------------------------------


@app.route("/api/wifi/connect", methods=["POST"])
@require_json
def api_wifi_connect():
    """
    POST /api/wifi/connect
    Conecta la Raspberry Pi a una red WiFi como cliente.

    Body JSON:
    {
        "ssid": "NombreRed",
        "password": "contraseña",
        "iface": "wlan1",       (opcional, defecto wlan1 para no interferir con el AP)
        "country": "ES"         (opcional)
    }
    """
    data = request.get_json()
    ssid = data.get("ssid", "").strip()
    password = data.get("password", "")
    iface = data.get("iface", "wlan1")
    country = data.get("country", "ES")

    if not ssid:
        return err("El campo 'ssid' es requerido.", 400)

    try:
        result = nm.set_wifi_credentials(ssid, password, iface, country)
        if result.get("success"):
            return ok(result, f"Credenciales WiFi configuradas para '{ssid}'.")
        return err(result.get("error", "Error configurando WiFi."), 400)
    except Exception as exc:
        logger.exception("Error en /api/wifi/connect")
        return err(f"Error conectando WiFi: {exc}", 500)


# ---------------------------------------------------------------------------
# API: WiFi Debug Logs
# ---------------------------------------------------------------------------


@app.route("/api/wifi/debug-log", methods=["GET"])
def api_wifi_debug_log():
    """
    GET /api/wifi/debug-log
    Devuelve el contenido del log de depuración WiFi.
    """
    import os
    from network_manager import WIFI_DEBUG_LOG
    
    if not os.path.exists(WIFI_DEBUG_LOG):
        return ok({"log": "Log file not found yet. Try connecting to a WiFi network.", "exists": False})
    
    try:
        with open(WIFI_DEBUG_LOG, "r") as f:
            content = f.read()
        return ok({"log": content, "exists": True, "path": WIFI_DEBUG_LOG})
    except Exception as exc:
        logger.exception("Error reading WiFi debug log")
        return err(f"Error leyendo log de debug: {exc}", 500)
        logger.exception("Error en /api/wifi/connect")
        return err(f"Error conectando WiFi: {exc}", 500)


# ---------------------------------------------------------------------------
# Aliases para compatibilidad con el frontend
# ---------------------------------------------------------------------------

@app.route("/api/interfaces", methods=["GET"])
def api_interfaces_alias():
    try:
        interfaces = nm.get_interfaces_with_status()
        return ok(interfaces)
    except Exception as exc:
        logger.exception("Error en /api/interfaces")
        return err(f"Error listando interfaces: {exc}", 500)

@app.route("/api/network/internet", methods=["GET"])
def api_network_internet():
    """GET /api/network/internet — verifica acceso a internet."""
    try:
        from network_manager import check_internet
        result = check_internet()
        return ok(result)
    except Exception as exc:
        return err(f"Error verificando internet: {exc}", 500)

@app.route("/api/interfaces/<iface>", methods=["GET"])
def api_interface_get(iface: str):
    try:
        interfaces = nm.get_interfaces()
        match = next((i for i in interfaces if i.get("name") == iface), None)
        if match is None:
            return err(f"Interfaz '{iface}' no encontrada.", 404)
        return ok(match)
    except Exception as exc:
        return err(f"Error obteniendo interfaz: {exc}", 500)

@app.route("/api/interfaces/<iface>", methods=["POST"])
@require_json
def api_interface_set(iface: str):
    data = request.get_json()
    data["iface"] = iface
    try:
        result = nm.apply_network_config(data)
        if result.get("success"):
            return ok(result, "Configuración aplicada.")
        return err(result.get("error", "Error aplicando config."), 400)
    except Exception as exc:
        return err(f"Error configurando interfaz: {exc}", 500)

@app.route("/api/wifi/scan", methods=["GET"])
def api_wifi_scan_alias():
    return api_network_scan()

@app.route("/api/wifi/status", methods=["GET"])
def api_wifi_status():
    iface = request.args.get("iface", "wlan0")
    try:
        interfaces = nm.get_interfaces()
        match = next((i for i in interfaces if i.get("name") == iface), None)
        return ok(match or {})
    except Exception as exc:
        return err(f"Error obteniendo estado WiFi: {exc}", 500)

@app.route("/api/dhcp/clients", methods=["GET"])
def api_dhcp_clients_alias():
    return api_dhcp_leases()

@app.route("/api/system", methods=["GET"])
def api_system_alias():
    return api_system_info()

@app.route("/api/system/hostname", methods=["POST"])
@require_json
def api_system_hostname():
    return api_hostname_post()

@app.route("/api/system/reboot", methods=["POST"])
def api_system_reboot():
    return api_reboot()

@app.route("/api/system/apply", methods=["POST"])
def api_system_apply():
    return ok({}, "Cambios aplicados.")


# ---------------------------------------------------------------------------
# API: Servicios
# ---------------------------------------------------------------------------

MONITORED_SERVICES = [
    {"name": "Node-RED",    "port": 1880, "id": "nodered"},
    {"name": "n8n",         "port": 5678, "id": "n8n"},
    {"name": "Grafana",     "port": 3000, "id": "grafana"},
    {"name": "PostgreSQL",  "port": 5432, "id": "postgresql"},
    {"name": "MQTT",        "port": 1883, "id": "mqtt"},
]

@app.route("/api/services/status", methods=["GET"])
def api_services_status():
    import socket
    results = []
    for svc in MONITORED_SERVICES:
        running = False
        try:
            with socket.create_connection(("127.0.0.1", svc["port"]), timeout=1):
                running = True
        except (ConnectionRefusedError, OSError):
            pass
        results.append({
            "id":      svc["id"],
            "name":    svc["name"],
            "port":    svc["port"],
            "running": running,
            "url":     f"http://{request.host.split(':')[0]}:{svc['port']}" if running else None,
        })
    return ok(results)


# ---------------------------------------------------------------------------
# API: Servidor y binding
# ---------------------------------------------------------------------------


@app.route("/api/server/status", methods=["GET"])
def api_server_status():
    """
    GET /api/server/status
    Estado actual del servidor: IP, puerto, interfaz de escucha.
    """
    try:
        status = sm.get_server_status()
        return ok(status)
    except Exception as exc:
        logger.exception("Error en /api/server/status")
        return err(f"Error obteniendo estado del servidor: {exc}", 500)


@app.route("/api/server/binding", methods=["GET"])
def api_server_binding():
    """
    GET /api/server/binding
    IP y puerto actuales donde está escuchando el servidor.
    """
    try:
        binding = sm.get_current_binding()
        return ok(binding)
    except Exception as exc:
        logger.exception("Error en /api/server/binding")
        return err(f"Error obteniendo binding: {exc}", 500)


@app.route("/api/server/fallback-ip", methods=["GET"])
def api_server_fallback_ip_get():
    """
    GET /api/server/fallback-ip
    Obtiene la IP fallback configurada.
    """
    try:
        return ok({"fallback_ip": sm.fallback_ip})
    except Exception as exc:
        logger.exception("Error en GET /api/server/fallback-ip")
        return err(f"Error obteniendo IP fallback: {exc}", 500)


@app.route("/api/server/fallback-ip", methods=["POST"])
@require_json
def api_server_fallback_ip_post():
    """
    POST /api/server/fallback-ip
    Cambia la IP fallback.

    Body JSON: { "fallback_ip": "192.168.1.5" }

    La nueva IP fallback se persiste en server_config.json.
    Si eth0 pierde su IP, el servidor vuelve a esta IP.
    """
    data = request.get_json()
    new_fallback_ip = data.get("fallback_ip", "").strip()

    if not new_fallback_ip:
        return err("El campo 'fallback_ip' es requerido.", 400)

    try:
        result = sm.set_fallback_ip(new_fallback_ip)
        if result.get("success"):
            return ok(result, result.get("message"))
        return err(result.get("error", "Error desconocido."), 400)
    except ValueError as exc:
        return err(str(exc), 400)
    except Exception as exc:
        logger.exception("Error configurando IP fallback")
        return err(f"Error configurando IP fallback: {exc}", 500)


# ---------------------------------------------------------------------------
# Manejo de errores globales
# ---------------------------------------------------------------------------


@app.errorhandler(404)
def not_found(e):
    return err("Ruta no encontrada.", 404)


@app.errorhandler(405)
def method_not_allowed(e):
    return err("Método HTTP no permitido en esta ruta.", 405)


@app.errorhandler(500)
def internal_error(e):
    return err("Error interno del servidor.", 500)


# ---------------------------------------------------------------------------
# OTA Updates
# ---------------------------------------------------------------------------

@app.route("/api/update/status")
def update_status():
    return ok(update_manager.get_update_status())


@app.route("/api/update/check", methods=["POST"])
def update_check():
    update_manager.check_now()
    return ok({"message": "Check iniciado"})


@app.route("/api/update/install", methods=["POST"])
def update_install():
    started = update_manager.install_update()
    if not started:
        return err("Ya hay una instalación en curso", 409)
    return ok({"message": "Instalación iniciada"})


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    logger.info(
        "Iniciando Access Point Configurator en %s:%d (debug=%s, dev_mode=%s)",
        SERVER_HOST,
        SERVER_PORT,
        SERVER_DEBUG,
        DEV_MODE,
    )

    # Inicia checker de actualizaciones OTA
    update_manager.start_background_checker()

    # Inicia monitoreo de cambios en eth0
    sm.start_monitoring(interval=5)
    logger.info("Servidor inicialmente en: %s", sm.get_server_status()["access_url"])

    try:
        app.run(
            host=SERVER_HOST,
            port=SERVER_PORT,
            debug=SERVER_DEBUG,
            use_reloader=SERVER_DEBUG,
        )
    finally:
        sm.stop_monitoring()
