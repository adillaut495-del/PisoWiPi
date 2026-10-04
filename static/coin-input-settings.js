(() => {
  const panel = document.getElementById('coin-input');
  if (!panel) return;

  const api = panel.dataset.settingsApi;
  const gpio = panel.querySelector('#coin-gpio');
  const edge = panel.querySelector('#coin-edge-debounce');
  const quiet = panel.querySelector('#coin-burst-quiet');
  const pullUp = panel.querySelector('#coin-pull-up');
  const activeLow = panel.querySelector('#coin-active-low');
  const pulseMap = panel.querySelector('#coin-pulse-map');
  const save = panel.querySelector('#coin-input-save');
  const state = panel.querySelector('#coin-input-state');
  const message = panel.querySelector('#coin-input-message');
  let currentGpio = 17;

  const showMessage = (text, isError = false) => {
    message.textContent = text;
    message.dataset.error = String(isError);
  };

  const request = async (url, options = {}) => {
    const response = await fetch(url, {
      ...options,
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...options.headers },
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Coin settings request failed.');
    return result;
  };

  const load = async () => {
    save.disabled = true;
    try {
      const settings = await request(api);
      currentGpio = settings.gpio;
      gpio.value = `GPIO${settings.gpio} / BCM ${settings.gpio}`;
      edge.value = settings.edge_debounce_ms;
      quiet.value = settings.burst_quiet_ms;
      pullUp.checked = settings.pull_up;
      activeLow.checked = settings.active_low;
      pulseMap.value = JSON.stringify(settings.pulse_map);
      state.textContent = settings.listener_status.toUpperCase();
      save.disabled = false;
    } catch (error) {
      state.textContent = 'UNAVAILABLE';
      showMessage(error.message, true);
    }
  };

  save.addEventListener('click', async () => {
    let map;
    try {
      map = JSON.parse(pulseMap.value);
      if (!map || typeof map !== 'object' || Array.isArray(map)) throw new Error();
    } catch {
      showMessage('Pulse map must be valid JSON, for example {"1":1,"2":5}.', true);
      return;
    }

    save.disabled = true;
    showMessage('Applying coin input settings...');
    try {
      const result = await request(api, {
        method: 'POST',
        body: JSON.stringify({
          gpio: currentGpio,
          edge_debounce_ms: edge.value,
          burst_quiet_ms: quiet.value,
          pull_up: pullUp.checked,
          active_low: activeLow.checked,
          pulse_map: map,
        }),
      });
      state.textContent = result.listener.status.toUpperCase();
      showMessage(result.listener.detail || `Saved. GPIO${currentGpio} listener is ${result.listener.status}.`, result.listener.status === 'offline');
      pulseMap.value = JSON.stringify(result.settings.pulse_map);
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      save.disabled = false;
    }
  });

  load();
})();
