/* ============================================================
   Access Point Configurator - app.js
   Vanilla JavaScript — No frameworks
   ============================================================ */

'use strict';

/* ============================================================
   CONFIGURATION
   ============================================================ */
const API_BASE_URL = (() => {
  // Auto-detect: if we're already on the AP IP, use it
  const host = window.location.hostname;
  if (host && host !== 'localhost' && host !== '127.0.0.1') {
    return `http://${host}`;
  }
  return 'http://192.168.4.1';
})();

const POLL_INTERVAL_MS = 5000; // Status polling interval
const TOAST_DURATION_MS = 4000;

/* ============================================================
   API CLIENT
   ============================================================ */
class APIClient {
  constructor(baseURL = API_BASE_URL) {
    this.baseURL = baseURL;
    this.timeout = 8000;
  }

  async _request(method, path, body = null, signal = null) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);

    const opts = {
      method,
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      signal: signal || controller.signal,
    };
    if (body !== null) opts.body = JSON.stringify(body);

    try {
      const res = await fetch(`${this.baseURL}${path}`, opts);
      clearTimeout(timer);
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { const d = await res.json(); msg = d.error || d.message || msg; } catch (_) {}
        throw new Error(msg);
      }
      const json = await res.json();
      // Desenvolver el wrapper { success, message, data: {...} } del backend
      return (json && json.success !== undefined && json.data !== undefined) ? json.data : json;
    } catch (err) {
      clearTimeout(timer);
      if (err.name === 'AbortError') throw new Error('La solicitud superó el tiempo de espera');
      throw err;
    }
  }

  get(path)             { return this._request('GET',    path); }
  post(path, body)      { return this._request('POST',   path, body); }
  put(path, body)       { return this._request('PUT',    path, body); }
  delete(path)          { return this._request('DELETE', path); }

  // ---- Status ----
  getStatus()           { return this.get('/api/status'); }

  // ---- Debug WiFi ----
  getWifiDebugLog()     { return this.get('/api/wifi/debug-log'); }

  // ---- Interfaces ----
  getInterfaces()       { return this.get('/api/interfaces'); }
  getInterface(iface)   { return this.get(`/api/interfaces/${iface}`); }
  setInterface(iface, cfg) { return this.post(`/api/interfaces/${iface}`, cfg); }

  // ---- WiFi Scan ----
  scanWifi(iface)       { return this.get(`/api/wifi/scan?iface=${iface}`); }
  connectWifi(cfg)      { return this.post('/api/wifi/connect', cfg); }
  getWifiStatus(iface)  { return this.get(`/api/wifi/status?iface=${iface}`); }

  // ---- Access Point ----
  getAPConfig()         { return this.get('/api/ap/config'); }
  setAPConfig(cfg)      { return this.post('/api/ap/config', cfg); }
  setAPState(enabled)   { return this.post('/api/ap/state', { enabled }); }

  // ---- DHCP Clients ----
  getDHCPClients()      { return this.get('/api/dhcp/clients'); }

  // ---- System ----
  getSystem()           { return this.get('/api/system'); }
  setHostname(name)     { return this.post('/api/system/hostname', { hostname: name }); }
  applyChanges()        { return this.post('/api/system/apply', {}); }
  reboot()              { return this.post('/api/system/reboot', {}); }
}

/* ============================================================
   TOAST NOTIFICATION SYSTEM
   ============================================================ */
class ToastManager {
  constructor() {
    this.container = document.getElementById('toast-container');
    this.queue = [];
  }

  show(title, message = '', type = 'info', duration = TOAST_DURATION_MS) {
    const icons = {
      success: '✓',
      error:   '✕',
      warning: '!',
      info:    'ℹ️',
    };

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
      <span class="toast-icon">${icons[type] || icons.info}</span>
      <div class="toast-body">
        <div class="toast-title">${escapeHTML(title)}</div>
        ${message ? `<div class="toast-msg">${escapeHTML(message)}</div>` : ''}
      </div>
      <button class="toast-close" aria-label="Cerrar">✕</button>
    `;

    toast.querySelector('.toast-close').addEventListener('click', () => this._remove(toast));
    this.container.appendChild(toast);

    const timer = setTimeout(() => this._remove(toast), duration);
    toast._timer = timer;
    return toast;
  }

  success(title, msg)  { return this.show(title, msg, 'success'); }
  error(title, msg)    { return this.show(title, msg, 'error'); }
  warning(title, msg)  { return this.show(title, msg, 'warning'); }
  info(title, msg)     { return this.show(title, msg, 'info'); }

  _remove(toast) {
    if (toast._timer) clearTimeout(toast._timer);
    toast.classList.add('removing');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
  }
}

/* ============================================================
   MODAL SYSTEM
   ============================================================ */
class ModalManager {
  constructor() {
    this.overlay = document.getElementById('modal-overlay');
    this.iconEl  = document.getElementById('modal-icon');
    this.titleEl = document.getElementById('modal-title');
    this.bodyEl  = document.getElementById('modal-body');
    this.confirmBtn = document.getElementById('modal-confirm');
    this.cancelBtn  = document.getElementById('modal-cancel');
    this._resolve = null;

    this.cancelBtn.addEventListener('click',  () => this._close(false));
    this.overlay.addEventListener('click', (e) => { if (e.target === this.overlay) this._close(false); });
  }

  confirm(title, body, { icon = '!', confirmText = 'Confirmar', confirmClass = 'btn-primary', cancelText = 'Cancelar' } = {}) {
    return new Promise(resolve => {
      this._resolve = resolve;
      this.iconEl.textContent  = icon;
      this.titleEl.textContent = title;
      this.bodyEl.textContent  = body;
      this.confirmBtn.textContent = confirmText;
      this.confirmBtn.className   = `btn ${confirmClass}`;
      this.cancelBtn.textContent  = cancelText;

      // Remove old listener, add new one
      const newBtn = this.confirmBtn.cloneNode(true);
      this.confirmBtn.replaceWith(newBtn);
      this.confirmBtn = newBtn;
      this.confirmBtn.addEventListener('click', () => this._close(true));

      this.overlay.classList.add('open');
    });
  }

  _close(result) {
    this.overlay.classList.remove('open');
    if (this._resolve) { this._resolve(result); this._resolve = null; }
  }
}

/* ============================================================
   BUTTON LOADING HELPER
   ============================================================ */
function setButtonLoading(btn, loading, originalText = null) {
  if (loading) {
    btn.dataset.originalText = btn.querySelector('.btn-text')?.textContent || btn.textContent;
    btn.classList.add('loading');
    const spinner = btn.querySelector('.btn-spinner');
    if (spinner) spinner.style.display = 'inline-block';
    const textEl = btn.querySelector('.btn-text');
    if (textEl) textEl.textContent = 'Procesando...';
  } else {
    btn.classList.remove('loading');
    const spinner = btn.querySelector('.btn-spinner');
    if (spinner) spinner.style.display = 'none';
    const textEl = btn.querySelector('.btn-text');
    if (textEl) textEl.textContent = originalText || btn.dataset.originalText || textEl.textContent;
  }
}

/* ============================================================
   UTILITY FUNCTIONS
   ============================================================ */
function escapeHTML(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function formatUptime(seconds) {
  if (!seconds && seconds !== 0) return 'N/A';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function signalClass(dbm) {
  if (dbm >= -50) return 's5';
  if (dbm >= -60) return 's4';
  if (dbm >= -70) return 's3';
  if (dbm >= -80) return 's2';
  return 's1';
}

function signalText(dbm) {
  if (dbm >= -50) return 'Excelente';
  if (dbm >= -60) return 'Buena';
  if (dbm >= -70) return 'Regular';
  if (dbm >= -80) return 'Débil';
  return 'Muy débil';
}

function ipValid(ip) {
  return /^(\d{1,3}\.){3}\d{1,3}$/.test(ip) &&
    ip.split('.').every(n => parseInt(n, 10) <= 255);
}

function buildSignalBars(dbm) {
  const cls = signalClass(dbm);
  return `<div class="signal-bars ${cls}">
    <span></span><span></span><span></span><span></span><span></span>
  </div>`;
}

/* ============================================================
   PENDING CHANGES TRACKER
   ============================================================ */
class PendingChanges {
  constructor() {
    this._store = {};
    this._callbacks = [];
    this._load();
  }

  _load() {
    try {
      const raw = localStorage.getItem('apconf_pending');
      if (raw) this._store = JSON.parse(raw);
    } catch (_) { this._store = {}; }
  }

  _save() {
    try { localStorage.setItem('apconf_pending', JSON.stringify(this._store)); } catch (_) {}
    this._callbacks.forEach(fn => fn());
  }

  set(key, data) { this._store[key] = { data, ts: Date.now() }; this._save(); }
  get(key)       { return this._store[key]?.data; }
  remove(key)    { delete this._store[key]; this._save(); }
  clear()        { this._store = {}; this._save(); }
  count()        { return Object.keys(this._store).length; }
  keys()         { return Object.keys(this._store); }
  has(key)       { return key in this._store; }
  entries()      { return Object.entries(this._store); }
  onChange(fn)   { this._callbacks.push(fn); }
}

/* ============================================================
   MAIN UI CLASS
   ============================================================ */
class NetworkConfigUI {
  constructor() {
    this.api     = new APIClient();
    this.toast   = new ToastManager();
    this.modal   = new ModalManager();
    this.pending = new PendingChanges();

    // State
    this.state = {
      connected:      false,
      status:         null,
      interfaces:     [],
      wifiNetworks:   [],
      selectedWifi:   null,
      apConfig:       null,
      dhcpClients:    [],
      systemInfo:     null,
      currentSection: 'dashboard',
      pollTimer:      null,
      logs:           [],
    };

    this._init();
  }

  _init() {
    this._setupNavigation();
    this._setupSidebar();
    this._setupPendingBanner();
    this._setupEthernet();
    this._setupWifi();
    this._setupAP();
    this._setupDHCP();
    this._setupSystem();
    this._setupDebug();
    this._setupApplyButton();
    this._setupUpdateBadge();
    this._startPolling();
    this._startUpdatePolling();

    // Initial load
    this.loadAll();
  }

  /* ---- NAVIGATION ---- */
  _setupNavigation() {
    document.querySelectorAll('[data-section]').forEach(el => {
      el.addEventListener('click', () => {
        const sec = el.dataset.section;
        this._navigate(sec);
        // Close sidebar on mobile
        document.querySelector('.sidebar').classList.remove('open');
        document.querySelector('.sidebar-overlay').classList.remove('visible');
      });
    });
  }

  _navigate(section) {
    this.state.currentSection = section;
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('[data-section]').forEach(el => {
      el.classList.toggle('active', el.dataset.section === section);
    });
    const el = document.getElementById(`section-${section}`);
    if (el) el.classList.add('active');
    // Refresh section data
    this._refreshSection(section);
  }

  _refreshSection(section) {
    switch (section) {
      case 'dashboard': this.loadStatus(); break;
      case 'ethernet':  this.loadInterfaces(); break;
      case 'wifi':      this.loadInterfaces(); break;
      case 'ap':        this.loadAPConfig(); break;
      case 'dhcp':      this.loadDHCPClients(); break;
      case 'system':    this.loadSystem(); break;
      case 'debug':     this.loadWifiDebugLog(); break;
    }
  }

  /* ---- SIDEBAR (mobile) ---- */
  _setupSidebar() {
    const hamburger = document.getElementById('hamburger');
    const sidebar   = document.querySelector('.sidebar');
    const overlay   = document.querySelector('.sidebar-overlay');
    if (!hamburger) return;

    hamburger.addEventListener('click', () => {
      sidebar.classList.toggle('open');
      overlay.classList.toggle('visible');
    });
    overlay.addEventListener('click', () => {
      sidebar.classList.remove('open');
      overlay.classList.remove('visible');
    });
  }

  /* ---- PENDING BANNER ---- */
  _setupPendingBanner() {
    const banner = document.getElementById('pending-banner');

    // Mapeo de prefijos de clave pending → sección
    const keyToSection = (key) => {
      if (key.startsWith('eth_'))      return 'ethernet';
      if (key.startsWith('ap_'))       return 'ap';
      if (key.startsWith('hostname'))  return 'system';
      return null;
    };

    const update = () => {
      const keys = this.pending.keys ? this.pending.keys() : [];
      const count = this.pending.count();
      banner?.classList.toggle('visible', count > 0);
      const el = document.getElementById('pending-count-text');
      if (el) el.textContent = `${count} cambio(s) pendiente(s) de aplicar al sistema`;

      // Activar solo los dots de las secciones con cambios
      document.querySelectorAll('.pending-dot[data-for]').forEach(d => {
        const section = d.dataset.for;
        const hasPending = keys.some(k => keyToSection(k) === section);
        d.classList.toggle('visible', hasPending);
      });
    };

    this.pending.onChange(update);
    update();
  }

  /* ---- APPLY ALL CHANGES ---- */
  _setupApplyButton() {
    document.querySelectorAll('.btn-apply-all').forEach(btn => {
      btn.addEventListener('click', () => this.applyChanges(btn));
    });
  }

  /* ====================================================
     DATA LOADING
     ==================================================== */

  async loadAll() {
    await Promise.allSettled([
      this.loadStatus(),
      this.loadInterfaces(),
      this.loadAPConfig(),
      this.loadSystem(),
    ]);
  }

  async loadStatus() {
    try {
      const data = await this.api.getStatus();
      this.state.status = data;
      this._renderStatus(data);
      this._setConnected(true);
    } catch (err) {
      this._setConnected(false);
      this._renderStatusOffline();
    }
  }

  async loadInterfaces() {
    try {
      const data = await this.api.getInterfaces();
      const ifaces = Array.isArray(data) ? data : [];
      this.state.interfaces = ifaces;
      this._renderEthernetInterfaceSelect();
      this._renderWifiInterfaceSelect();
      this._renderNetworkStatus(ifaces);
      // Actualizar estado WiFi actual con el ssid de la interfaz wlan activa
      const wlan = ifaces.find(i => i.type === 'wifi' && i.ssid);
      const el = document.getElementById('wifi-current-status');
      if (el) {
        if (wlan && wlan.ssid) {
          el.innerHTML = `<span class="badge badge-success">● Conectado</span><span class="text-sm" style="margin-left:.5rem">${escapeHTML(wlan.ssid)}</span>`;
        } else {
          el.innerHTML = `<span class="badge badge-danger">● Desconectado</span>`;
        }
      }
    } catch (err) {
      this._log('error', `No se pudo cargar interfaces: ${err.message}`);
    }
  }

  async loadAPConfig() {
    try {
      const data = await this.api.getAPConfig();
      this.state.apConfig = data;
      this._renderAPConfig(data);
    } catch (err) {
      // Use defaults
      this._renderAPConfig({
        enabled: false,
        ssid: 'RPI-Setup',
        password: '',
        channel: 6,
        band: 'g',
        hidden: false,
        ip: '192.168.4.1',
        dhcp_start: '192.168.4.10',
        dhcp_end: '192.168.4.100',
      });
    }
  }

  async loadDHCPClients() {
    const container = document.getElementById('dhcp-clients-table');
    if (!container) return;
    container.innerHTML = `<div class="loading-row"><div class="spinner"></div> Cargando clientes...</div>`;
    try {
      const data = await this.api.getDHCPClients();
      const clients = data.clients || [];
      this.state.dhcpClients = clients;
      this._renderDHCPClients(clients);
    } catch (err) {
      container.innerHTML = `<div class="empty-state">
        <div class="empty-state-text">No se pudo cargar los clientes</div>
        <div class="empty-state-sub">${escapeHTML(err.message)}</div>
      </div>`;
    }
  }

  async loadSystem() {
    try {
      const data = await this.api.getSystem();
      this.state.systemInfo = data;
      this._renderSystem(data);
    } catch (err) {
      this._renderSystemOffline();
    }
    this._loadServices();
  }

  async _loadServices() {
    try {
      const services = await this.api.get('/api/services/status');
      const list = Array.isArray(services) ? services : [];
      for (const svc of list) {
        const badge = document.getElementById(`svc-${svc.id}-badge`);
        if (!badge) continue;
        if (svc.running) {
          badge.className = 'badge badge-success';
          badge.textContent = 'Activo';
          badge.style.cursor = svc.url ? 'pointer' : '';
          if (svc.url) badge.onclick = () => window.open(svc.url, '_blank');
        } else {
          badge.className = 'badge badge-danger';
          badge.textContent = 'Inactivo';
          badge.onclick = null;
        }
      }
    } catch (_) {}
  }

  /* ====================================================
     POLLING
     ==================================================== */
  _startPolling() {
    this._stopPolling();
    this.state.pollTimer = setInterval(() => {
      this.loadStatus();
      if (this.state.currentSection === 'dhcp')   this.loadDHCPClients();
      if (this.state.currentSection === 'system') this.loadSystem();
    }, POLL_INTERVAL_MS);
  }

  _stopPolling() {
    if (this.state.pollTimer) { clearInterval(this.state.pollTimer); this.state.pollTimer = null; }
  }

  /* ====================================================
     CONNECTION STATE
     ==================================================== */
  _setConnected(connected) {
    this.state.connected = connected;
    const badge = document.getElementById('connection-badge');
    if (!badge) return;
    badge.className = `connection-badge ${connected ? 'connected' : 'disconnected'}`;
    badge.querySelector('.badge-text').textContent = connected ? 'Conectado' : 'Sin conexión';
  }

  /* ====================================================
     RENDERS
     ==================================================== */

  /* ---- Status / Dashboard ---- */
  _renderStatus(data) {
    // El backend devuelve { interfaces, ap, system, dev_mode }
    // Los campos de sistema vienen en data.system
    const sys = data.system || data;

    const hn = sys.hostname || data.hostname || 'raspberrypi';
    document.getElementById('header-hostname').textContent = hn;
    document.getElementById('dash-hostname').textContent   = hn;

    const uptimeSec = sys.uptime_seconds ?? sys.uptime?.seconds ?? data.uptime_seconds ?? 0;
    document.getElementById('dash-uptime').textContent = formatUptime(uptimeSec);

    const apActive = data.ap?.active ?? data.ap_enabled ?? false;
    document.getElementById('dash-ap-status').textContent = apActive ? 'Activo' : 'Inactivo';
    document.getElementById('dash-ap-badge').className    = `badge ${apActive ? 'badge-success' : 'badge-danger'}`;
    document.getElementById('dash-ap-badge').textContent  = apActive ? 'ON' : 'OFF';

    // Temperatura
    const temp = sys.temp_celsius ?? sys.temperature?.celsius ?? data.cpu_temp_c ?? null;
    document.getElementById('dash-temp').textContent = temp !== null ? `${Number(temp).toFixed(1)} °C` : 'N/A';

    // RAM
    const ramTotal = sys.ram_total_mb ?? sys.memory?.total_mb ?? data.ram_total_mb ?? 0;
    const ramUsed  = sys.ram_used_mb  ?? sys.memory?.used_mb  ?? data.ram_used_mb  ?? 0;
    if (ramTotal) {
      const pct = Math.round((ramUsed / ramTotal) * 100);
      document.getElementById('dash-ram').textContent = `${Math.round(ramUsed)} / ${Math.round(ramTotal)} MB (${pct}%)`;
    }

    // Interfaces: badges de estado + cards
    const interfaces = data.interfaces || [];
    this._renderIfaceCards(interfaces);
    this._renderNetworkStatus(interfaces);

    // También renderizar sección sistema si hay datos
    if (data.system) this._renderSystem(data.system);
  }

  _renderStatusOffline() {
    document.getElementById('header-hostname').textContent = 'desconectado';
    document.getElementById('dash-ap-badge').className     = 'badge badge-danger';
    document.getElementById('dash-ap-badge').textContent   = 'OFF';
  }

  _renderIfaceCards(ifaces) {
    const container = document.getElementById('iface-cards');
    if (!container) return;
    if (!ifaces.length) {
      container.innerHTML = `<div class="empty-state" style="padding:1rem"><div class="empty-state-text">Sin interfaces detectadas</div></div>`;
      return;
    }
    container.innerHTML = ifaces.map(iface => {
      const isDocker = iface.name.startsWith('br-') || iface.name.startsWith('veth') || iface.name === 'docker0';
      const label = isDocker ? '<span class="badge badge-warning" style="font-size:.65rem;margin-left:.4rem">Docker</span>' : '';
      const typeLabel = iface.ssid ? `${escapeHTML(iface.type || '')} — ${escapeHTML(iface.ssid)}` : escapeHTML(iface.type || '');
      return `
      <div class="iface-card">
        <div class="iface-header">
          <span class="iface-name">${escapeHTML(iface.name)}${label}</span>
          <span class="badge ${iface.up ? 'badge-success' : 'badge-danger'}">${iface.up ? '● UP' : '● DOWN'}</span>
        </div>
        <div class="iface-ip">${escapeHTML(iface.ip || 'Sin IP')}</div>
        <div class="iface-type">${typeLabel}</div>
        ${iface.rx_bytes !== undefined ? `<div class="text-sm text-muted">↓ ${_fmtBytes(iface.rx_bytes)} ↑ ${_fmtBytes(iface.tx_bytes)}</div>` : ''}
      </div>`;
    }).join('');
  }

  /* ---- Ethernet ---- */
  _renderEthernetInterfaceSelect() {
    const sel = document.getElementById('eth-iface-select');
    if (!sel) return;
    const eth = this.state.interfaces.filter(i => i.type === 'ethernet' || i.name.startsWith('eth'));
    const current = sel.value;
    sel.innerHTML = eth.length
      ? eth.map(i => `<option value="${escapeHTML(i.name)}" ${i.name === current ? 'selected' : ''}>${escapeHTML(i.name)} ${i.ip ? '— ' + i.ip : ''}</option>`).join('')
      : '<option value="eth0">eth0</option>';
  }

  _setupEthernet() {
    const sel       = document.getElementById('eth-iface-select');
    const modeBtns  = document.querySelectorAll('#eth-mode-btn-dhcp, #eth-mode-btn-static');
    const staticSec = document.getElementById('eth-static-fields');
    const dynSec    = document.getElementById('eth-dhcp-fields');
    const saveBtn   = document.getElementById('eth-save-btn');

    if (!sel) return;

    sel.addEventListener('change', () => this._loadEthernetIface(sel.value));

    modeBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        modeBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const isStatic = btn.dataset.mode === 'static';
        staticSec.classList.toggle('hidden', !isStatic);
        dynSec.classList.toggle('hidden', isStatic);
      });
    });

    saveBtn.addEventListener('click', () => this.saveEthernetConfig(saveBtn));
  }

  async _loadEthernetIface(name) {
    try {
      const data = await this.api.getInterface(name);
      const isStatic = data.mode === 'static';
      document.getElementById('eth-mode-btn-dhcp').classList.toggle('active', !isStatic);
      document.getElementById('eth-mode-btn-static').classList.toggle('active', isStatic);
      document.getElementById('eth-static-fields').classList.toggle('hidden', !isStatic);
      document.getElementById('eth-dhcp-fields').classList.toggle('hidden', isStatic);
      document.getElementById('eth-ip').value      = data.ip      || '';
      document.getElementById('eth-mask').value    = data.netmask || '';
      document.getElementById('eth-gw').value      = data.gateway || '';
      document.getElementById('eth-dns1').value    = data.dns1    || '';
      document.getElementById('eth-dns2').value    = data.dns2    || '';
      document.getElementById('eth-dhcp-ip').value = data.ip      || 'Obteniendo...';
    } catch (err) {
      this.toast.warning('Interfaz', `No se pudo cargar config de ${name}: ${err.message}`);
    }
  }

  async saveEthernetConfig(btn) {
    const iface = document.getElementById('eth-iface-select').value;
    const mode  = document.getElementById('eth-mode-btn-static').classList.contains('active') ? 'static' : 'dhcp';
    const cfg   = { mode };

    if (mode === 'static') {
      const ip   = document.getElementById('eth-ip').value.trim();
      const mask = document.getElementById('eth-mask').value.trim();
      const gw   = document.getElementById('eth-gw').value.trim();
      if (!ipValid(ip)) { this.toast.error('IP inválida', 'Verifica el campo de dirección IP'); return; }
      if (!ipValid(mask)) { this.toast.error('Máscara inválida', 'Verifica la máscara de red'); return; }
      cfg.ip = ip; cfg.netmask = mask; cfg.gateway = gw;
      cfg.dns1 = document.getElementById('eth-dns1').value.trim();
      cfg.dns2 = document.getElementById('eth-dns2').value.trim();
    }

    const origText = btn.querySelector('.btn-text').textContent;
    setButtonLoading(btn, true);
    try {
      await this.api.setInterface(iface, cfg);
      this.pending.set(`eth_${iface}`, { iface, cfg });
      this.toast.success('Ethernet guardado', `Config de ${iface} guardada. Aplica los cambios para activar.`);
      this._log('ok', `Ethernet ${iface} config guardada (modo: ${mode})`);
    } catch (err) {
      this.toast.error('Error al guardar', err.message);
      this._log('err', `Error guardando ${iface}: ${err.message}`);
    } finally {
      setButtonLoading(btn, false, origText);
    }
  }

  /* ---- WiFi ---- */
  _renderWifiInterfaceSelect() {
    const sel = document.getElementById('wifi-iface-select');
    if (!sel) return;
    const wlan = this.state.interfaces.filter(i => i.type === 'wifi' || i.name.startsWith('wlan'));
    const current = sel.value;
    sel.innerHTML = wlan.length
      ? wlan.map(i => `<option value="${escapeHTML(i.name)}" ${i.name === current ? 'selected' : ''}>${escapeHTML(i.name)} ${i.ssid ? '— ' + i.ssid : ''}</option>`).join('')
      : '<option value="wlan0">wlan0</option>';
  }

  _setupWifi() {
    const modeBtns  = document.querySelectorAll('#wifi-mode-btn-dhcp, #wifi-mode-btn-static');
    const staticSec = document.getElementById('wifi-static-fields');
    const dynSec    = document.getElementById('wifi-dhcp-fields');
    const scanBtn   = document.getElementById('wifi-scan-btn');
    const saveBtn   = document.getElementById('wifi-save-btn');
    const pwField   = document.getElementById('wifi-pw-field');
    const pwToggle  = document.getElementById('wifi-pw-toggle');
    const pwInput   = document.getElementById('wifi-pw');

    modeBtns?.forEach(btn => {
      btn.addEventListener('click', () => {
        modeBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const isStatic = btn.dataset.mode === 'static';
        staticSec?.classList.toggle('hidden', !isStatic);
        dynSec?.classList.toggle('hidden', isStatic);
      });
    });

    scanBtn?.addEventListener('click', () => this.scanWifi(scanBtn));
    saveBtn?.addEventListener('click', () => this.saveWifiConfig(saveBtn));

    const refreshBtn = document.getElementById('wifi-refresh-btn');
    refreshBtn?.addEventListener('click', () => this.refreshWifiStatus(refreshBtn));

    // Mostrar campo de password al escribir un SSID manualmente
    document.getElementById('wifi-ssid-input')?.addEventListener('input', () => {
      const pwField = document.getElementById('wifi-pw-field');
      if (pwField) pwField.classList.remove('hidden');
    });

    // Modal WiFi
    document.getElementById('wifi-modal-cancel')?.addEventListener('click', () => this._closeWifiModal());
    document.getElementById('wifi-modal-overlay')?.addEventListener('click', e => {
      if (e.target === e.currentTarget) this._closeWifiModal();
    });
    document.getElementById('wifi-modal-pw-toggle')?.addEventListener('click', () => {
      const inp = document.getElementById('wifi-modal-pw');
      inp.type = inp.type === 'password' ? 'text' : 'password';
    });
    document.getElementById('wifi-modal-connect')?.addEventListener('click', () => this._connectFromModal());
    document.getElementById('wifi-modal-pw')?.addEventListener('keydown', e => {
      if (e.key === 'Enter') this._connectFromModal();
    });

    pwToggle?.addEventListener('click', () => {
      const show = pwInput.type === 'password';
      pwInput.type = show ? 'text' : 'password';
      pwToggle.textContent = show ? 'Ocultar' : 'Ver';
    });
  }

  async scanWifi(btn) {
    const iface = document.getElementById('wifi-iface-select').value;
    const list  = document.getElementById('wifi-network-list');
    const origText = btn.querySelector('.btn-text').textContent;

    setButtonLoading(btn, true);
    list.innerHTML = `<div class="loading-row"><div class="spinner"></div> Escaneando redes WiFi...</div>`;

    try {
      const data = await this.api.scanWifi(iface);
      const nets = data.networks || [];
      this.state.wifiNetworks = nets;
      this._renderWifiList(nets);
      if (!nets.length) {
        list.innerHTML = `<div class="empty-state"><div class="empty-state-text">No se encontraron redes</div><div class="empty-state-sub">Intenta nuevamente en unos segundos</div></div>`;
      }
      this.toast.success('Escaneo completo', `${nets.length} red(es) encontrada(s)`);
    } catch (err) {
      list.innerHTML = `<div class="empty-state"><div class="empty-state-text">Error al escanear</div><div class="empty-state-sub">${escapeHTML(err.message)}</div></div>`;
      this.toast.error('Error de escaneo', err.message);
    } finally {
      setButtonLoading(btn, false, origText);
    }
  }

  _renderWifiList(nets) {
    const list = document.getElementById('wifi-network-list');
    if (!list) return;
    list.innerHTML = nets.map(net => `
      <div class="wifi-item" data-ssid="${escapeHTML(net.ssid)}" data-secured="${net.security && net.security !== 'Open' ? '1' : '0'}" data-security="${escapeHTML(net.security || '')}">
        <div class="wifi-left">
          ${buildSignalBars(net.signal_dbm ?? -80)}
          <div>
            <div class="wifi-ssid">${escapeHTML(net.ssid)}</div>
            <div class="wifi-bssid">${escapeHTML(net.bssid || '')} · ${signalText(net.signal_dbm ?? -80)} (${net.signal_dbm ?? '?'} dBm)</div>
          </div>
        </div>
        <div class="wifi-right">
          <span class="text-muted text-sm">${net.security && net.security !== 'Open' ? 'WPA' : 'Abierta'}</span>
          <span class="badge badge-cyan">${net.channel ? 'Ch ' + net.channel : ''}</span>
        </div>
      </div>
    `).join('');

    list.querySelectorAll('.wifi-item').forEach(item => {
      item.addEventListener('click', () => {
        list.querySelectorAll('.wifi-item').forEach(i => i.classList.remove('selected'));
        item.classList.add('selected');
        const ssid = item.dataset.ssid;
        const secured = item.dataset.secured === '1';
        this.state.selectedWifi = ssid;
        document.getElementById('wifi-ssid-input').value = ssid;
        const pwField = document.getElementById('wifi-pw-field');
        if (pwField) pwField.classList.toggle('hidden', !secured);
      });
      item.addEventListener('dblclick', () => {
        this._openWifiModal(item.dataset.ssid, item.dataset.secured === '1', item.dataset.security || '');
      });
    });
  }

  _openWifiModal(ssid, secured, security) {
    document.getElementById('wifi-modal-ssid').textContent = ssid;
    document.getElementById('wifi-modal-security').textContent = security || (secured ? 'Protegida' : 'Abierta');
    const pwField = document.getElementById('wifi-modal-pw-field');
    if (pwField) pwField.style.display = secured ? '' : 'none';
    document.getElementById('wifi-modal-pw').value = '';
    document.getElementById('wifi-modal-overlay').style.display = '';
    if (secured) setTimeout(() => document.getElementById('wifi-modal-pw').focus(), 100);
    this._modalSsid = ssid;
    this._modalSecured = secured;
  }

  _closeWifiModal() {
    document.getElementById('wifi-modal-overlay').style.display = 'none';
    this._modalSsid = null;
  }

  async _connectFromModal() {
    const ssid = this._modalSsid;
    const pw   = document.getElementById('wifi-modal-pw').value;
    const iface = document.getElementById('wifi-iface-select').value;
    const btn   = document.getElementById('wifi-modal-connect');
    if (!ssid) return;
    setButtonLoading(btn, true);
    try {
      await this.api.connectWifi({ iface, ssid, password: pw, mode: 'dhcp' });
      this._closeWifiModal();
      this.toast.success('WiFi configurado', `Conectando a "${ssid}"...`);
      setTimeout(() => this.loadInterfaces(), 8000);
    } catch (err) {
      this.toast.error('Error WiFi', err.message);
    } finally {
      setButtonLoading(btn, false, 'Conectar');
    }
  }

  async saveWifiConfig(btn) {
    const iface = document.getElementById('wifi-iface-select').value;
    const ssid  = document.getElementById('wifi-ssid-input').value.trim();
    const pw    = document.getElementById('wifi-pw').value;
    const mode  = document.getElementById('wifi-mode-btn-static').classList.contains('active') ? 'static' : 'dhcp';

    if (!ssid) { this.toast.error('SSID requerido', 'Selecciona o escribe el nombre de la red'); return; }

    const cfg = { iface, ssid, password: pw, mode };
    if (mode === 'static') {
      cfg.ip      = document.getElementById('wifi-static-ip').value.trim();
      cfg.netmask = document.getElementById('wifi-static-mask').value.trim();
      cfg.gateway = document.getElementById('wifi-static-gw').value.trim();
      cfg.dns1    = document.getElementById('wifi-static-dns1').value.trim();
      if (!ipValid(cfg.ip)) { this.toast.error('IP inválida', 'Verifica la dirección IP estática'); return; }
    }

    const origText = btn.querySelector('.btn-text').textContent;
    setButtonLoading(btn, true);
    try {
      await this.api.connectWifi(cfg);
      this.toast.success('WiFi configurado', `Conectando a "${ssid}"...`);
      this._log('ok', `WiFi ${iface} → ${ssid}`);
    } catch (err) {
      this.toast.error('Error WiFi', err.message);
      this._log('err', `WiFi error: ${err.message}`);
    } finally {
      setButtonLoading(btn, false, origText);
    }
  }

  async refreshWifiStatus(btn) {
    const origText = btn.querySelector('.btn-text').textContent;
    setButtonLoading(btn, true);
    try {
      await this.loadInterfaces();
      this.toast.success('WiFi', 'Estado de red actualizado');
    } catch (err) {
      this.toast.error('Error', err.message || 'No se pudo actualizar el estado');
    } finally {
      setButtonLoading(btn, false, origText);
    }
  }

  _renderWifiCurrentStatus(ssid) {
    const el = document.getElementById('wifi-current-status');
    if (!el) return;
    el.innerHTML = `
      <span class="badge badge-success">● Conectado</span>
      <span class="text-sm" style="margin-left:.5rem">${escapeHTML(ssid)}</span>
    `;
  }

  /* ---- Access Point ---- */
  _renderAPConfig(data) {
    if (!data) return;
    const fields = {
      'ap-toggle':      data.enabled,
      'ap-ssid':        data.ssid        || 'RPI-Setup',
      'ap-password':    data.password    || '',
      'ap-channel':     data.channel     || 6,
      'ap-band':        data.band        || 'g',
      'ap-hidden':      data.hidden      || false,
      'ap-ip':          data.ip          || '192.168.4.1',
      'ap-dhcp-start':  data.dhcp_start  || '192.168.4.10',
      'ap-dhcp-end':    data.dhcp_end    || '192.168.4.100',
    };

    Object.entries(fields).forEach(([id, val]) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (el.type === 'checkbox') el.checked = val;
      else el.value = val;
    });

    this._updateAPStatusUI(data.enabled);
    this._updateAPPreview();
  }

  _updateAPStatusUI(enabled) {
    const sub = document.getElementById('ap-status-sub');
    if (sub) sub.textContent = enabled ? 'Access Point activo y transmitiendo' : 'Access Point detenido';
    const toggle = document.getElementById('ap-toggle');
    if (toggle) toggle.checked = enabled;
  }

  _setupAP() {
    const toggle  = document.getElementById('ap-toggle');
    const copyBtn = document.getElementById('ap-copy-ssid');
    const pwToggle = document.getElementById('ap-pw-toggle');
    const pwInput  = document.getElementById('ap-password');
    const saveBtn  = document.getElementById('ap-save-btn');
    const preview  = document.getElementById('ap-preview-btn');

    // Live preview update
    ['ap-ssid','ap-password','ap-channel','ap-band','ap-ip','ap-dhcp-start','ap-dhcp-end','ap-hidden'].forEach(id => {
      document.getElementById(id)?.addEventListener('input', () => this._updateAPPreview());
      document.getElementById(id)?.addEventListener('change', () => this._updateAPPreview());
    });

    // Mostrar advertencia si wlan0 tiene ssid (está en modo cliente)
    const warning = document.getElementById('ap-wifi-warning');
    if (warning) {
      const wlan = this.state.interfaces.find(i => i.type === 'wifi' && i.ssid);
      if (wlan) warning.style.display = '';
    }

    toggle?.addEventListener('change', async () => {
      const enabled = toggle.checked;
      this._updateAPStatusUI(enabled);
      try {
        await this.api.setAPState(enabled);
        this.toast.success(enabled ? 'AP Activado' : 'AP Detenido',
          enabled ? 'wlan0 ahora es un hotspot' : 'wlan0 intentará reconectarse al WiFi');
        this._log(enabled ? 'ok' : 'warn', `Access Point ${enabled ? 'activado' : 'detenido'}`);
        this.pending.set('ap_state', { enabled });
      } catch (err) {
        toggle.checked = !enabled; // Revert
        this._updateAPStatusUI(!enabled);
        this.toast.error('Error', err.message);
      }
    });

    copyBtn?.addEventListener('click', () => {
      const ssid = document.getElementById('ap-ssid').value;
      navigator.clipboard.writeText(ssid).then(() => {
        this.toast.info('Copiado', `SSID "${ssid}" copiado al portapapeles`);
      }).catch(() => {
        this.toast.warning('No disponible', 'No se pudo acceder al portapapeles');
      });
    });

    pwToggle?.addEventListener('click', () => {
      const show = pwInput.type === 'password';
      pwInput.type = show ? 'text' : 'password';
      pwToggle.textContent = show ? 'Ocultar' : 'Ver';
    });

    saveBtn?.addEventListener('click', () => this.saveAPConfig(saveBtn));
    preview?.addEventListener('click', () => this._updateAPPreview());
  }

  _updateAPPreview() {
    const el = document.getElementById('ap-config-preview');
    if (!el) return;

    const ssid     = document.getElementById('ap-ssid')?.value || '';
    const pw       = document.getElementById('ap-password')?.value || '';
    const channel  = document.getElementById('ap-channel')?.value || '6';
    const band     = document.getElementById('ap-band')?.value || 'g';
    const hidden   = document.getElementById('ap-hidden')?.checked || false;
    const ip       = document.getElementById('ap-ip')?.value || '192.168.4.1';
    const dhcpS    = document.getElementById('ap-dhcp-start')?.value || '192.168.4.10';
    const dhcpE    = document.getElementById('ap-dhcp-end')?.value || '192.168.4.100';
    const iface    = document.getElementById('ap-iface-select')?.value || 'wlan0';

    const hwMode = band === 'a' ? 'a' : 'g';

    el.innerHTML = [
      { k: '# /etc/hostapd/hostapd.conf', v: '', comment: true },
      { k: 'interface',        v: iface },
      { k: 'ssid',             v: ssid },
      { k: 'hw_mode',          v: hwMode },
      { k: 'channel',          v: channel },
      { k: 'ignore_broadcast_ssid', v: hidden ? '1' : '0' },
      { k: '', v: '', comment: true },
      { k: '# /etc/dnsmasq.conf (AP DHCP)', v: '', comment: true },
      { k: 'dhcp-range', v: `${dhcpS},${dhcpE},12h` },
      { k: 'address',    v: `/#/${ip}` },
      ...(pw ? [
        { k: '', v: '', comment: true },
        { k: '# WPA2', v: '', comment: true },
        { k: 'wpa',               v: '2' },
        { k: 'wpa_passphrase',    v: pw.replace(/./g, '*') },
        { k: 'wpa_key_mgmt',      v: 'WPA-PSK' },
        { k: 'rsn_pairwise',      v: 'CCMP' },
      ] : [
        { k: '# Sin contraseña (red abierta)', v: '', comment: true },
      ]),
    ].map(({ k, v, comment }) =>
      comment
        ? `<span class="comment">${escapeHTML(k)}</span>`
        : `<span class="key">${escapeHTML(k)}</span>=<span class="value">${escapeHTML(v)}</span>`
    ).join('\n');
  }

  async saveAPConfig(btn) {
    const cfg = {
      ssid:       document.getElementById('ap-ssid').value.trim(),
      password:   document.getElementById('ap-password').value,
      channel:    parseInt(document.getElementById('ap-channel').value, 10),
      band:       document.getElementById('ap-band').value,
      hidden:     document.getElementById('ap-hidden').checked,
      ip:         document.getElementById('ap-ip').value.trim(),
      dhcp_start: document.getElementById('ap-dhcp-start').value.trim(),
      dhcp_end:   document.getElementById('ap-dhcp-end').value.trim(),
      iface:      document.getElementById('ap-iface-select')?.value || 'wlan0',
    };

    if (!cfg.ssid) { this.toast.error('SSID requerido', 'El nombre de la red no puede estar vacío'); return; }
    if (cfg.password && cfg.password.length < 8) { this.toast.error('Contraseña muy corta', 'Mínimo 8 caracteres (WPA2)'); return; }
    if (!ipValid(cfg.ip)) { this.toast.error('IP del AP inválida', 'Ingresa una dirección IP válida'); return; }

    const origText = btn.querySelector('.btn-text').textContent;
    setButtonLoading(btn, true);
    try {
      await this.api.setAPConfig(cfg);
      this.pending.set('ap_config', cfg);
      this.toast.success('AP configurado', 'Configuración guardada. Aplica los cambios para activar.');
      this._log('ok', `AP config guardada: SSID=${cfg.ssid}, canal=${cfg.channel}`);
    } catch (err) {
      this.toast.error('Error al guardar AP', err.message);
      this._log('err', `Error guardando AP: ${err.message}`);
    } finally {
      setButtonLoading(btn, false, origText);
    }
  }

  /* ---- DHCP Clients ---- */
  _setupDHCP() {
    document.getElementById('dhcp-refresh-btn')?.addEventListener('click', (e) => {
      const btn = e.currentTarget;
      const origText = btn.querySelector('.btn-text')?.textContent || btn.textContent;
      setButtonLoading(btn, true);
      this.loadDHCPClients().finally(() => setButtonLoading(btn, false, origText));
    });
  }

  _renderDHCPClients(clients) {
    const container = document.getElementById('dhcp-clients-table');
    const countEl   = document.getElementById('dhcp-client-count');
    if (!container) return;

    if (countEl) countEl.textContent = `${clients.length} cliente(s) conectado(s)`;

    if (!clients.length) {
      container.innerHTML = `<div class="empty-state">
        <div class="empty-state-text">No hay clientes conectados</div>
        <div class="empty-state-sub">Los dispositivos aparecerán aquí cuando se conecten al AP</div>
      </div>`;
      return;
    }

    container.innerHTML = `
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>MAC</th>
              <th>IP Asignada</th>
              <th>Hostname</th>
              <th>Expira</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            ${clients.map(c => `
              <tr>
                <td class="mono">${escapeHTML(c.mac || 'N/A')}</td>
                <td class="mono">${escapeHTML(c.ip  || 'N/A')}</td>
                <td>${escapeHTML(c.hostname || '<sin nombre>')}</td>
                <td class="text-sm text-muted">${escapeHTML(c.expires || 'N/A')}</td>
                <td><span class="badge badge-success">● Activo</span></td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  }

  /* ---- System ---- */
  _setupSystem() {
    document.getElementById('sys-reboot-btn')?.addEventListener('click', () => this.reboot());
    document.getElementById('sys-hostname-btn')?.addEventListener('click', (e) => this.saveHostname(e.currentTarget));
    document.getElementById('sys-ap-activate-btn')?.addEventListener('click', () => this.activateAP());
  }

  /* ---- Debug WiFi ---- */
  _setupDebug() {
    document.getElementById('debug-refresh-btn')?.addEventListener('click', (e) => {
      const btn = e.currentTarget;
      const origText = btn.querySelector('.btn-text')?.textContent || btn.textContent;
      setButtonLoading(btn, true);
      this.loadWifiDebugLog().finally(() => setButtonLoading(btn, false, origText));
    });
  }

  async loadWifiDebugLog() {
    const container = document.getElementById('debug-log-content');
    if (!container) return;
    container.innerHTML = `<div class="loading-row"><div class="spinner"></div> Cargando logs...</div>`;
    try {
      const data = await this.api.getWifiDebugLog();
      const logContent = data.log || 'No hay logs disponibles';
      const lines = logContent.split('\n').filter(l => l.trim());
      if (!lines.length) {
        container.innerHTML = `<div class="empty-state">
          <div class="empty-state-text">No hay logs de WiFi</div>
          <div class="empty-state-sub">Intenta conectar a una red WiFi para generar logs</div>
        </div>`;
        return;
      }
      container.innerHTML = lines.map(line => {
        let cls = 'log-info';
        if (line.includes('[ERROR]')) cls = 'log-error';
        else if (line.includes('[WARN]')) cls = 'log-warning';
        else if (line.includes('[DEBUG]')) cls = 'log-debug';
        const timeMatch = line.match(/^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]/);
        const time = timeMatch ? timeMatch[1] : '--:--:--';
        const msg = line.replace(/^\[.*?\]\s*/, '');
        return `<div class="log-line"><span class="log-time">${time}</span><span class="${cls}">${escapeHTML(msg)}</span></div>`;
      }).join('');
    } catch (err) {
      container.innerHTML = `<div class="empty-state">
        <div class="empty-state-text">Error cargando logs</div>
        <div class="empty-state-sub">${escapeHTML(err.message)}</div>
      </div>`;
    }
  }

  async activateAP() {
    const btn = document.getElementById('sys-ap-activate-btn');
    const statusText = document.getElementById('sys-ap-status-text');

    const confirmed = await this.modal.confirm(
      'Activar Access Point',
      'Se activará el Access Point WiFi "RPI-Setup" (192.168.4.1).\n\nEl configurador web seguirá disponible en la IP de red actual.\n\nConectate al WiFi "RPI-Setup" para reconfigurar.',
      'Activar AP'
    );
    if (!confirmed) return;

    btn?.classList.add('loading');
    if (statusText) statusText.textContent = 'Activando...';

    try {
      await this.api.setAPState(true);
      if (statusText) statusText.textContent = 'AP activo — conectate al WiFi "RPI-Setup"';
      this.toast.success('Access Point activado', 'Conectate al WiFi "RPI-Setup" en 192.168.4.1');
      this._log('ok', 'Access Point activado manualmente para reconfiguración');
      // Actualizar estado del toggle en la sección AP
      const toggle = document.getElementById('ap-toggle');
      if (toggle) toggle.checked = true;
      this._updateAPStatusUI(true);
    } catch (err) {
      if (statusText) statusText.textContent = '';
      this.toast.error('Error', err.message);
      this._log('error', `Error activando AP: ${err.message}`);
    } finally {
      btn?.classList.remove('loading');
    }
  }

  _renderSystem(data) {
    if (!data) return;

    // Hostname
    const hostnameInput = document.getElementById('sys-hostname');
    if (hostnameInput && !hostnameInput.matches(':focus')) {
      hostnameInput.value = data.hostname || '';
    }
    document.getElementById('sys-uptime').textContent =
      formatUptime(data.uptime_seconds ?? data.uptime?.seconds ?? 0);

    // CPU — acepta alias directo o anidado
    const cpuPct = data.cpu_percent ?? data.cpu?.usage_percent ?? 0;
    _setProgress('sys-cpu-bar', 'sys-cpu-val', cpuPct, `${cpuPct.toFixed(1)}%`);

    // RAM
    const ramTotal = data.ram_total_mb ?? data.memory?.total_mb ?? 0;
    const ramUsed  = data.ram_used_mb  ?? data.memory?.used_mb  ?? 0;
    const ramPct   = ramTotal ? Math.round((ramUsed / ramTotal) * 100) : 0;
    _setProgress('sys-ram-bar', 'sys-ram-val', ramPct,
      ramTotal ? `${Math.round(ramUsed)}/${Math.round(ramTotal)} MB (${ramPct}%)` : 'N/A');

    // Temperatura
    const temp = data.temp_celsius ?? data.temperature?.celsius ?? null;
    if (temp !== null) {
      const tempPct = Math.min(100, Math.round((temp / 85) * 100));
      _setProgress('sys-temp-bar', 'sys-temp-val', tempPct, `${temp.toFixed(1)} °C`, true);
    }

    // Disco
    const diskPct   = data.disk_percent ?? data.disk?.percent ?? null;
    const diskTotal = data.disk?.total_gb;
    const diskUsed  = data.disk?.used_gb;
    if (diskPct !== null) {
      const diskLabel = (diskTotal && diskUsed)
        ? `${diskUsed.toFixed(1)}/${diskTotal.toFixed(1)} GB (${Math.round(diskPct)}%)`
        : `${Math.round(diskPct)}%`;
      _setProgress('sys-disk-bar', 'sys-disk-val', diskPct, diskLabel);
    }
  }

  _renderNetworkStatus(interfaces) {
    if (!interfaces || !Array.isArray(interfaces)) return;

    for (const iface of interfaces) {
      const hasIp      = !!iface.ip;
      const hasInternet = !!iface.has_internet;
      const isEth  = iface.name && (iface.name.startsWith('eth') || iface.name.startsWith('end'));
      const isWifi = iface.type === 'wifi';
      const isAP   = iface.ssid === 'RPI-Setup';

      if (isEth) {
        const ipChip  = document.getElementById('eth-chip-ip');
        const intChip = document.getElementById('eth-chip-internet');
        if (ipChip)  ipChip.innerHTML  = hasIp
          ? `<span class="dot dot-green"></span> ${iface.ip}`
          : `<span class="dot dot-gray"></span> Sin IP`;
        if (intChip) intChip.innerHTML = hasInternet
          ? `<span class="dot dot-green"></span> Internet OK`
          : `<span class="dot dot-red"></span> Sin internet`;
      }

      if (isWifi && !isAP) {
        const ipChip  = document.getElementById('wifi-chip-ip');
        const intChip = document.getElementById('wifi-chip-internet');
        const sigChip = document.getElementById('wifi-chip-signal');
        if (ipChip)  ipChip.innerHTML  = hasIp
          ? `<span class="dot dot-green"></span> ${iface.ip}`
          : `<span class="dot dot-gray"></span> Sin IP`;
        if (intChip) intChip.innerHTML = hasInternet
          ? `<span class="dot dot-green"></span> Internet OK`
          : `<span class="dot dot-red"></span> Sin internet`;
        if (sigChip && iface.signal_dbm != null) {
          const dbm      = iface.signal_dbm;
          const label    = iface.signal_label || `${dbm} dBm`;
          const dotClass = dbm > -65 ? 'dot-green' : dbm > -75 ? 'dot-orange' : 'dot-red';
          sigChip.innerHTML = `<span class="dot ${dotClass}"></span> ${dbm} dBm — ${label}`;
        }
      }
    }
  }

  _renderSystemOffline() {
    ['sys-cpu-val','sys-ram-val','sys-temp-val','sys-disk-val','sys-uptime'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.textContent = 'N/A';
    });
    ['sys-cpu-bar','sys-ram-bar','sys-temp-bar','sys-disk-bar'].forEach(id => {
      const el = document.getElementById(id);
      if (el) { el.style.width = '0%'; el.className = 'progress-bar'; }
    });
  }

  async saveHostname(btn) {
    const input = document.getElementById('sys-hostname');
    const name  = input.value.trim();
    if (!name || !/^[a-zA-Z0-9-]+$/.test(name)) {
      this.toast.error('Hostname inválido', 'Solo letras, números y guiones');
      return;
    }
    const origText = btn.querySelector('.btn-text')?.textContent || btn.textContent;
    setButtonLoading(btn, true);
    try {
      await this.api.setHostname(name);
      this.pending.set('hostname', { hostname: name });
      this.toast.success('Hostname actualizado', `Se estableció como "${name}". Aplica para reiniciar servicios.`);
      this._log('ok', `Hostname guardado: ${name}`);
    } catch (err) {
      this.toast.error('Error', err.message);
    } finally {
      setButtonLoading(btn, false, origText);
    }
  }

  /* ====================================================
     APPLY CHANGES
     ==================================================== */
  async applyChanges(btn) {
    const confirmed = await this.modal.confirm(
      'Aplicar todos los cambios',
      'Se van a aplicar todas las configuraciones guardadas. Esto puede causar una breve interrupción de red. ¿Continuar?',
      { icon: '!', confirmText: 'Aplicar ahora', confirmClass: 'btn-accent', cancelText: 'Cancelar' }
    );
    if (!confirmed) return;

    const origText = btn?.querySelector('.btn-text')?.textContent || 'Aplicar';
    if (btn) setButtonLoading(btn, true);
    this.toast.info('Aplicando...', 'Configurando el sistema, espera un momento');

    try {
      await this.api.applyChanges();
      this.pending.clear();
      this.toast.success('¡Cambios aplicados!', 'El sistema ha sido reconfigurado correctamente');
      this._log('ok', 'Todos los cambios aplicados al sistema');
      // Reload status after a moment
      setTimeout(() => this.loadAll(), 3000);
    } catch (err) {
      this.toast.error('Error al aplicar', err.message);
      this._log('err', `Error al aplicar cambios: ${err.message}`);
    } finally {
      if (btn) setButtonLoading(btn, false, origText);
    }
  }

  /* ====================================================
     REBOOT
     ==================================================== */
  async reboot() {
    const confirmed = await this.modal.confirm(
      'Reiniciar Raspberry Pi',
      'Esto apagará y reiniciará el sistema. Perderás la conexión temporalmente. ¿Estás seguro?',
      { icon: '!', confirmText: 'Reiniciar', confirmClass: 'btn-danger', cancelText: 'Cancelar' }
    );
    if (!confirmed) return;

    const btn = document.getElementById('sys-reboot-btn');
    const origText = btn?.querySelector('.btn-text')?.textContent || 'Reiniciar';
    if (btn) setButtonLoading(btn, true);

    try {
      await this.api.reboot();
      this.toast.warning('Reiniciando...', 'La Raspberry Pi se está reiniciando. Reconéctate en ~30 segundos.');
      this._log('warn', 'Reinicio del sistema iniciado');
      this._setConnected(false);
      this._stopPolling();
      // Start reconnect check
      setTimeout(() => this._waitForReconnect(), 15000);
    } catch (err) {
      this.toast.error('Error al reiniciar', err.message);
      if (btn) setButtonLoading(btn, false, origText);
    }
  }

  _waitForReconnect() {
    let attempts = 0;
    const maxAttempts = 24; // ~2 min
    const interval = setInterval(async () => {
      attempts++;
      try {
        await this.api.getStatus();
        clearInterval(interval);
        this._setConnected(true);
        this._startPolling();
        this.toast.success('Reconectado', 'La Raspberry Pi está en línea nuevamente');
        const btn = document.getElementById('sys-reboot-btn');
        if (btn) setButtonLoading(btn, false, 'Reiniciar');
      } catch (_) {
        if (attempts >= maxAttempts) {
          clearInterval(interval);
          this.toast.error('Sin respuesta', 'No se pudo reconectar. Verifica la conexión.');
          const btn = document.getElementById('sys-reboot-btn');
          if (btn) setButtonLoading(btn, false, 'Reiniciar');
        }
      }
    }, 5000);
  }

  /* ====================================================
     LOG
     ==================================================== */
  _log(type, message) {
    const entry = { type, message, time: new Date().toLocaleTimeString() };
    this.state.logs.unshift(entry);
    if (this.state.logs.length > 50) this.state.logs.pop();
    this._renderLog();
  }

  _renderLog() {
    const box = document.getElementById('sys-log');
    if (!box) return;
    box.innerHTML = this.state.logs.map(({ type, message, time }) =>
      `<div class="log-line"><span class="log-time">${escapeHTML(time)}</span><span class="log-${type}">${escapeHTML(message)}</span></div>`
    ).join('');
    box.scrollTop = 0;
  }

  /* ====================================================
     OTA UPDATES
     ==================================================== */

  _setupUpdateBadge() {
    document.getElementById('update-badge')?.addEventListener('click', () => {
      this._openUpdateModal();
    });
  }

  _startUpdatePolling() {
    // Chequear estado de updates cada 60 segundos
    this._checkUpdateStatus();
    setInterval(() => this._checkUpdateStatus(), 60_000);
  }

  async _checkUpdateStatus() {
    try {
      const data = await this.api.get('/api/update/status');
      this._lastUpdateData = data;
      this._renderUpdateBadge(data);
      // Si hay una instalación en curso, seguir el progreso
      if (['downloading', 'installing', 'restarting'].includes(data.status)) {
        this._renderUpdateProgress(data);
      }
    } catch (_) {}
  }

  _renderUpdateBadge(data) {
    const badge = document.getElementById('update-badge');
    const text  = document.getElementById('update-badge-text');
    if (!badge) return;
    if (data.update_available) {
      text.textContent = `v${data.latest_version}`;
      badge.classList.remove('hidden');
    } else {
      badge.classList.add('hidden');
    }

    // Actualizar badge de versión en el header
    const localVer = data.local_version;
    if (localVer) {
      const headerVer = document.getElementById('header-version');
      if (headerVer) headerVer.textContent = `v${localVer}`;

      // Actualizar stat card en dashboard
      const dashVer    = document.getElementById('dash-version');
      const dashVerSub = document.getElementById('dash-version-sub');
      if (dashVer) dashVer.textContent = `v${localVer}`;
      if (dashVerSub) {
        dashVerSub.innerHTML = data.update_available
          ? `<span style="color:var(--accent)">↑ v${data.latest_version} disponible</span>`
          : `<span style="color:var(--success,#22c55e)">✓ Al día</span>`;
      }
    }
  }

  _openUpdateModal() {
    const data = this._lastUpdateData || {};
    document.getElementById('upd-local-ver').textContent = data.local_version || '—';
    document.getElementById('upd-new-ver').textContent   = data.latest_version || '—';
    document.getElementById('upd-notes').textContent     = data.release_notes  || 'Sin notas de versión.';
    document.getElementById('upd-progress').classList.add('hidden');
    document.getElementById('upd-actions').classList.remove('hidden');
    document.getElementById('update-modal-overlay').classList.add('open');
  }

  _closeUpdateModal() {
    document.getElementById('update-modal-overlay').classList.remove('open');
  }

  async _installUpdate() {
    const btn = document.getElementById('upd-install-btn');
    setButtonLoading(btn, true);
    document.getElementById('upd-actions').classList.add('hidden');
    document.getElementById('upd-progress').classList.remove('hidden');

    try {
      await this.api.post('/api/update/install', {});
      // Polling de progreso cada 2 segundos
      this._updateProgressInterval = setInterval(async () => {
        try {
          const data = await this.api.get('/api/update/status');
          this._renderUpdateProgress(data);
          if (data.status === 'done') {
            clearInterval(this._updateProgressInterval);
            setTimeout(() => window.location.reload(), 3000);
          } else if (data.status === 'error') {
            clearInterval(this._updateProgressInterval);
            setButtonLoading(btn, false, 'Reintentar');
            document.getElementById('upd-actions').classList.remove('hidden');
          }
        } catch (_) {}
      }, 2000);
    } catch (e) {
      this.toast.error('Error', e.message);
      setButtonLoading(btn, false, 'Instalar actualización');
      document.getElementById('upd-actions').classList.remove('hidden');
      document.getElementById('upd-progress').classList.add('hidden');
    }
  }

  _renderUpdateProgress(data) {
    this._lastUpdateData = data;
    const steps = [
      { key: 'downloading', label: 'Descargando actualización...' },
      { key: 'installing',  label: 'Instalando archivos...' },
      { key: 'restarting',  label: 'Reiniciando servicio...' },
      { key: 'done',        label: 'Actualización completada' },
    ];
    const statusOrder = ['downloading', 'installing', 'restarting', 'done'];
    const currentIdx  = statusOrder.indexOf(data.status);

    const container = document.getElementById('upd-steps');
    if (!container) return;
    container.innerHTML = steps.map((step, i) => {
      const idx = statusOrder.indexOf(step.key);
      let cls = '', icon = '○';
      if (data.status === 'error' && idx === currentIdx) {
        cls = 'error'; icon = '✕';
      } else if (idx < currentIdx || data.status === 'done') {
        cls = 'done'; icon = '✓';
      } else if (idx === currentIdx) {
        cls = 'active'; icon = '⟳';
      }
      return `<div class="update-step ${cls}">
        <span class="update-step-icon">${icon}</span>
        <span>${step.label}</span>
      </div>`;
    }).join('');

    if (data.status === 'done') {
      container.innerHTML += `<div class="update-step done" style="margin-top:.5rem;font-weight:600">
        <span class="update-step-icon">✓</span>
        <span>Recargando en 3 segundos...</span>
      </div>`;
    } else if (data.status === 'error') {
      container.innerHTML += `<div class="update-step error" style="margin-top:.5rem">
        <span class="update-step-icon">!</span>
        <span>${data.status_msg}</span>
      </div>`;
    }
  }
}

/* ============================================================
   HELPERS (module-level, used by UI class)
   ============================================================ */
function _setProgress(barId, valId, pct, text, isTemp = false) {
  const bar = document.getElementById(barId);
  const val = document.getElementById(valId);
  if (!bar || !val) return;
  bar.style.width = `${Math.min(100, pct)}%`;
  val.textContent = text;

  if (isTemp) {
    bar.className = `progress-bar${pct > 85 ? ' crit' : pct > 70 ? ' warn' : ''}`;
  } else {
    bar.className = `progress-bar${pct > 90 ? ' crit' : pct > 75 ? ' warn' : ''}`;
  }
}

function _fmtBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B','KB','MB','GB'];
  let i = 0;
  while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
  return `${bytes.toFixed(1)} ${units[i]}`;
}

/* ============================================================
   BOOTSTRAP
   ============================================================ */
document.addEventListener('DOMContentLoaded', () => {
  window.app = new NetworkConfigUI();
});
