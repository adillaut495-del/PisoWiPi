/*
 * Live internet status for the operator console and the customer portal.
 *
 * Elements:
 *   [data-net-status]  chip with [data-net-label] and [data-net-latency] children
 *   [data-net-row]     hardware-panel row with <small> value, .status-icon and .status-tag
 *   [data-net-alert]   banner that is only visible while the uplink is offline
 *   [data-net-url]     endpoint override (default /api/internet-status)
 *   [data-net-interval] poll seconds (default 5)
 *   data-net-sound="1" on a chip makes the console alarm when the uplink drops
 */
(() => {
  const chips = [...document.querySelectorAll('[data-net-status]')];
  const rows = [...document.querySelectorAll('[data-net-row]')];
  const alerts = [...document.querySelectorAll('[data-net-alert]')];
  if (!chips.length && !rows.length && !alerts.length) return;
  const endpoint = document.querySelector('[data-net-url]')?.dataset.netUrl || '/api/internet-status';
  const seconds = Math.max(2, Number(document.querySelector('[data-net-interval]')?.dataset.netInterval || 5));
  const LABELS = { online: 'Internet online', offline: 'Internet offline', checking: 'Checking internet' };
  const ROW_LABELS = { online: 'Online', offline: 'Offline', checking: 'Checking' };
  let current = null;

  const paint = (state, latency) => {
    chips.forEach((chip) => {
      chip.dataset.netState = state;
      const label = chip.querySelector('[data-net-label]');
      const meter = chip.querySelector('[data-net-latency]');
      if (label) label.textContent = LABELS[state] || LABELS.checking;
      if (meter) meter.textContent = state === 'online' && latency != null ? `${Math.round(latency)} ms` : '';
    });
    rows.forEach((row) => {
      const value = row.querySelector('small');
      const icon = row.querySelector('.status-icon');
      const tag = row.querySelector('.status-tag');
      if (value) value.textContent = ROW_LABELS[state] || ROW_LABELS.checking;
      if (icon) icon.classList.toggle('warn', state !== 'online');
      if (tag) {
        tag.classList.toggle('warn', state !== 'online');
        tag.textContent = state === 'online' ? 'READY' : 'UPLINK';
      }
    });
    alerts.forEach((alert) => { alert.hidden = state !== 'offline'; });
  };

  const poll = async () => {
    try {
      const response = await fetch(endpoint, { cache: 'no-store', headers: { Accept: 'application/json' } });
      if (!response.ok) return;
      const data = await response.json();
      const state = data.state || (data.online ? 'online' : 'offline');
      const changed = current !== null && current !== state;
      current = state;
      paint(state, data.latency_ms);
      document.dispatchEvent(new CustomEvent('piso:internet', { detail: { state, online: Boolean(data.online), latency_ms: data.latency_ms } }));
      if (changed && state === 'offline' && chips.some((chip) => chip.dataset.netSound === '1') && window.PisoSound) {
        window.PisoSound.play('warn');
      }
    } catch (error) {
      // The console polls again on the next tick.
    }
  };

  poll();
  setInterval(poll, seconds * 1000);
})();
