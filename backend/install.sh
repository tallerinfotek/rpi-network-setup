#!/usr/bin/env bash
# =============================================================================
# install.sh - Instalador del Access Point Configurator
# para Raspberry Pi (Raspberry Pi OS / Debian Bookworm o Bullseye)
#
# Uso:
#   chmod +x install.sh
#   sudo bash install.sh [--fallback-ip 192.168.1.2]
#
# Este script:
#   1. Limpia cualquier instalación anterior (hostapd/dnsmasq/servicio viejo)
#   2. Instala dependencias Python en un virtualenv
#   3. Configura IP fija en eth0 (192.168.1.2) para acceso inicial sin red
#   4. Crea e instala el servicio systemd
#   5. Arranca el servidor web en el puerto 80
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*"; }
log_section() { echo -e "\n${BLUE}==> $*${NC}"; }

# ---------------------------------------------------------------------------
# Argumentos
# ---------------------------------------------------------------------------
FALLBACK_IP="192.168.1.2"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --fallback-ip)
            FALLBACK_IP="$2"
            shift 2
            ;;
        *)
            log_warn "Argumento desconocido: $1"
            shift
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Verificaciones previas
# ---------------------------------------------------------------------------
log_section "Verificando requisitos"

if [[ $EUID -ne 0 ]]; then
    log_error "Ejecutar como root: sudo bash install.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_NAME="rpi-setup"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
VENV_DIR="$SCRIPT_DIR/venv"
PYTHON_BIN="python3"

log_info "Backend: $SCRIPT_DIR"
log_info "IP fallback: $FALLBACK_IP"

# ---------------------------------------------------------------------------
# 1. Limpiar instalación anterior
# ---------------------------------------------------------------------------
log_section "Limpiando instalación anterior"

# Detener y deshabilitar servicio viejo si existe
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl stop "$SERVICE_NAME"
    log_info "Servicio $SERVICE_NAME detenido"
fi
if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl disable "$SERVICE_NAME"
    log_info "Servicio $SERVICE_NAME deshabilitado"
fi

# Detener y deshabilitar hostapd y dnsmasq (ya no los necesitamos)
for svc in hostapd dnsmasq; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        systemctl stop "$svc" && log_info "Detenido: $svc"
    fi
    if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
        systemctl disable "$svc" && log_info "Deshabilitado: $svc"
    fi
done

# Limpiar cualquier proceso escuchando en el puerto 80
PORT80_PID=$(lsof -ti :80 2>/dev/null || true)
if [[ -n "$PORT80_PID" ]]; then
    kill -9 $PORT80_PID 2>/dev/null || true
    log_info "Proceso en puerto 80 terminado (PID: $PORT80_PID)"
fi

# Limpiar bloque de wlan0 con IP fija de AP (192.168.4.1) en dhcpcd.conf
DHCPCD_CONF="/etc/dhcpcd.conf"
if [[ -f "$DHCPCD_CONF" ]]; then
    # Eliminar bloque anterior de wlan0 generado por el instalador viejo
    python3 - <<'PYEOF'
import re

conf_path = "/etc/dhcpcd.conf"
try:
    with open(conf_path, "r") as f:
        content = f.read()
    # Eliminar bloque de wlan0 (AP viejo)
    content = re.sub(r'\n# Configuración estática del Access Point.*?(?=\ninterface|\Z)', '', content, flags=re.DOTALL)
    content = re.sub(r'\ninterface wlan0\nstatic ip_address=192\.168\.4\.1/24\nnohook wpa_supplicant\n', '\n', content)
    with open(conf_path, "w") as f:
        f.write(content)
    print("dhcpcd.conf: bloque de AP (wlan0) eliminado")
except Exception as e:
    print(f"Advertencia limpiando dhcpcd.conf: {e}")
PYEOF
    log_info "dhcpcd.conf limpiado"
fi

# ---------------------------------------------------------------------------
# 2. Instalar paquetes del sistema
# ---------------------------------------------------------------------------
log_section "Instalando paquetes del sistema"

apt-get update -qq

PACKAGES=(
    python3
    python3-pip
    python3-venv
    iw
    wireless-tools
    net-tools
    curl
    lsof
)

apt-get install -y --no-install-recommends "${PACKAGES[@]}"
log_info "Paquetes instalados"

# ---------------------------------------------------------------------------
# 3. Instalar dependencias Python en virtualenv
# ---------------------------------------------------------------------------
log_section "Instalando dependencias Python"

if [[ ! -d "$VENV_DIR" ]]; then
    $PYTHON_BIN -m venv "$VENV_DIR"
    log_info "Virtualenv creado en: $VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r "$SCRIPT_DIR/requirements.txt" -q
deactivate
log_info "Dependencias instaladas"

# ---------------------------------------------------------------------------
# 4. Configurar IP fija en eth0 para acceso inicial sin DHCP
# ---------------------------------------------------------------------------
log_section "Configurando IP fallback en eth0 ($FALLBACK_IP)"

# Si existe dhcpcd (Bullseye y anteriores)
if command -v dhcpcd &>/dev/null; then
    python3 - "$FALLBACK_IP" <<'PYEOF'
import re, sys

conf_path = "/etc/dhcpcd.conf"
iface = "eth0"
ip = sys.argv[1]

try:
    with open(conf_path, "r") as f:
        content = f.read()
    # Eliminar bloque anterior de eth0
    content = re.sub(rf'\ninterface\s+{re.escape(iface)}\n(?:(?!interface).*\n)*', '\n', content)
    # Agregar nueva config de fallback
    content += f"""
# IP fallback para acceso inicial sin red - rpi-setup
# Se puede cambiar con: POST /api/server/fallback-ip
interface {iface}
fallback static_{iface}

profile static_{iface}
static ip_address={ip}/24
"""
    with open(conf_path, "w") as f:
        f.write(content)
    print(f"IP fallback {ip} configurada en {iface} (dhcpcd fallback profile)")
except Exception as e:
    print(f"Advertencia: {e}")
PYEOF
    log_info "IP fallback configurada via dhcpcd"

# Si existe NetworkManager (Bookworm)
elif command -v nmcli &>/dev/null; then
    # Crear conexión fallback con baja prioridad - solo se activa si no hay DHCP
    nmcli con delete "eth0-fallback" 2>/dev/null || true
    nmcli con add \
        type ethernet \
        con-name "eth0-fallback" \
        ifname eth0 \
        ipv4.method manual \
        ipv4.addresses "${FALLBACK_IP}/24" \
        ipv4.route-metric 1000 \
        connection.autoconnect-priority -100 \
        autoconnect yes
    log_info "Conexión fallback eth0-fallback creada via NetworkManager"
else
    log_warn "No se encontró dhcpcd ni NetworkManager. Configurando IP vía ip addr."
    cat > /etc/network/interfaces.d/eth0-fallback <<EOF
# IP fallback para rpi-setup
auto eth0
iface eth0 inet static
    address $FALLBACK_IP
    netmask 255.255.255.0
EOF
    log_info "IP fallback configurada en /etc/network/interfaces.d/eth0-fallback"
fi

# Persistir la IP fallback en el archivo de config de la app
cat > "$PROJECT_DIR/server_config.json" <<EOF
{
  "fallback_ip": "$FALLBACK_IP"
}
EOF
log_info "server_config.json actualizado con fallback_ip=$FALLBACK_IP"

# ---------------------------------------------------------------------------
# 5. Crear servicio systemd
# ---------------------------------------------------------------------------
log_section "Instalando servicio systemd: $SERVICE_NAME"

# Copiar y personalizar el archivo de servicio
sed \
    -e "s|%SCRIPT_DIR%|$SCRIPT_DIR|g" \
    -e "s|%VENV_DIR%|$VENV_DIR|g" \
    "$SCRIPT_DIR/rpi-setup.service" > "$SERVICE_FILE"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
log_info "Servicio $SERVICE_NAME habilitado para autostart"

# ---------------------------------------------------------------------------
# 6. Arrancar el servicio
# ---------------------------------------------------------------------------
log_section "Iniciando servicio"

systemctl start "$SERVICE_NAME"
sleep 2

if systemctl is-active --quiet "$SERVICE_NAME"; then
    log_info "Servicio $SERVICE_NAME activo y corriendo"
else
    log_warn "El servicio tardó en iniciar. Ver logs: journalctl -u $SERVICE_NAME -f"
fi

# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------
log_section "Instalación completada"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        Access Point Configurator instalado            ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC}  IP de acceso inicial:  ${YELLOW}http://$FALLBACK_IP${NC}"
echo -e "${GREEN}║${NC}  Puerto:                ${YELLOW}80${NC}"
echo -e "${GREEN}║${NC}  Servicio:              ${YELLOW}systemctl status $SERVICE_NAME${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC}  Conecta un cable ethernet al mismo switch/router ${NC}"
echo -e "${GREEN}║${NC}  y accede a ${YELLOW}http://$FALLBACK_IP${NC} desde el browser. ${NC}"
echo -e "${GREEN}║${NC}  Una vez que eth0 obtenga IP por DHCP, la app     ${NC}"
echo -e "${GREEN}║${NC}  migrará automáticamente a esa IP.                ${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Logs en vivo: ${YELLOW}journalctl -u $SERVICE_NAME -f${NC}"
echo -e "  Reiniciar:    ${YELLOW}systemctl restart $SERVICE_NAME${NC}"
echo ""
