#!/usr/bin/env bash
# =============================================================================
# uninstall.sh - Elimina el Access Point Configurator y la app vieja de AP
#
# Uso:
#   chmod +x uninstall.sh
#   sudo bash uninstall.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_section() { echo -e "\n${BLUE}==> $*${NC}"; }

if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}[ERROR]${NC} Ejecutar como root: sudo bash uninstall.sh"
    exit 1
fi

SERVICE_NAME="rpi-setup"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# ---------------------------------------------------------------------------
# 1. Detener y deshabilitar el servicio
# ---------------------------------------------------------------------------
log_section "Deteniendo servicios"

for svc in "$SERVICE_NAME" hostapd dnsmasq; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        systemctl stop "$svc" && log_info "Detenido: $svc"
    fi
    if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
        systemctl disable "$svc" && log_info "Deshabilitado: $svc"
    fi
done

# Matar cualquier proceso en puerto 80
PORT80_PID=$(lsof -ti :80 2>/dev/null || true)
if [[ -n "$PORT80_PID" ]]; then
    kill -9 $PORT80_PID 2>/dev/null || true
    log_info "Proceso en puerto 80 terminado"
fi

# ---------------------------------------------------------------------------
# 2. Eliminar archivo de servicio
# ---------------------------------------------------------------------------
log_section "Eliminando archivos de sistema"

if [[ -f "$SERVICE_FILE" ]]; then
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload
    log_info "Servicio eliminado: $SERVICE_FILE"
fi

# ---------------------------------------------------------------------------
# 3. Limpiar configuración de red generada
# ---------------------------------------------------------------------------
log_section "Limpiando configuración de red"

# Limpiar dhcpcd.conf (bloques de wlan0 y eth0-fallback generados por install.sh)
DHCPCD_CONF="/etc/dhcpcd.conf"
if [[ -f "$DHCPCD_CONF" ]]; then
    python3 - <<'PYEOF'
import re

conf_path = "/etc/dhcpcd.conf"
try:
    with open(conf_path, "r") as f:
        content = f.read()
    # Eliminar bloque del AP viejo (wlan0)
    content = re.sub(r'\n# Configuración estática del Access Point.*', '', content, flags=re.DOTALL)
    content = re.sub(r'\ninterface wlan0\nstatic ip_address=192\.168\.4\.1/24\nnohook wpa_supplicant\n', '\n', content)
    # Eliminar bloque de fallback eth0 generado por install.sh nuevo
    content = re.sub(r'\n# IP fallback para acceso inicial.*', '', content, flags=re.DOTALL)
    with open(conf_path, "w") as f:
        f.write(content.rstrip() + '\n')
    print("dhcpcd.conf limpiado")
except Exception as e:
    print(f"Advertencia: {e}")
PYEOF
    log_info "dhcpcd.conf limpiado"
fi

# Limpiar conexión NetworkManager de fallback
if command -v nmcli &>/dev/null; then
    nmcli con delete "eth0-fallback" 2>/dev/null && log_info "Conexión eth0-fallback eliminada" || true
fi

# Eliminar archivo de interfaces si fue creado por el instalador
if [[ -f /etc/network/interfaces.d/eth0-fallback ]]; then
    rm -f /etc/network/interfaces.d/eth0-fallback
    log_info "Eliminado: /etc/network/interfaces.d/eth0-fallback"
fi

# Limpiar configuración de hostapd
for f in /etc/hostapd/hostapd.conf /etc/default/hostapd.bak /etc/dnsmasq.conf.bak /etc/dhcpcd.conf.bak; do
    if [[ -f "$f" ]]; then
        rm -f "$f"
        log_info "Eliminado: $f"
    fi
done

# Restaurar dnsmasq.conf desde backup si existe
if [[ -f /etc/dnsmasq.conf.bak ]]; then
    mv /etc/dnsmasq.conf.bak /etc/dnsmasq.conf
    log_info "dnsmasq.conf restaurado desde backup"
fi

# ---------------------------------------------------------------------------
# Listo
# ---------------------------------------------------------------------------
log_section "Desinstalación completada"
echo ""
log_info "El Access Point Configurator fue removido del sistema."
log_warn "Los archivos del proyecto en /home/pi/ NO fueron eliminados."
log_warn "Para eliminarlos manualmente: rm -rf /home/pi/'Access Point'"
echo ""
