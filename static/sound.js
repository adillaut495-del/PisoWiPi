/*
 * PisoPilot sound effects.
 *
 * Synthesised with the Web Audio API so the machine ships no audio assets.
 * Sounds: coin (a coin landed), request (a client is waiting), success (credited),
 *        warn (uplink lost), error (a coin or request was rejected).
 *
 * Browsers keep an AudioContext suspended until the page receives a gesture, so
 * every page installs a one-shot gesture listener that unlocks playback and the
 * toggle button reports "tap to enable" until that happens.
 */
window.PisoSound = (() => {
  const STORAGE_KEY = 'piso.sound.enabled';
  const VOLUME = 0.22;
  const PITCH_BY_AMOUNT = { 1: 1.12, 5: 1, 10: 0.86, 20: 0.72 };
  const RECIPES = {
    coin: [{ f: 1568, d: 0.09 }, { f: 2093, d: 0.13, at: 0.055 }],
    request: [{ f: 880, d: 0.13 }, { f: 1318.5, d: 0.22, at: 0.14 }],
    success: [{ f: 784, d: 0.1 }, { f: 1046.5, d: 0.1, at: 0.1 }, { f: 1318.5, d: 0.26, at: 0.2 }],
    warn: [{ f: 587.33, d: 0.18 }, { f: 440, d: 0.3, at: 0.2 }],
    error: [{ f: 330, d: 0.16 }, { f: 247, d: 0.3, at: 0.16 }],
  };
  const toggles = new Set();
  let context = null;
  let blocked = false;
  let enabled = readPreference();

  function readPreference() {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      return stored === null ? true : stored === '1';
    } catch (error) {
      return true;
    }
  }

  function writePreference(value) {
    try {
      window.localStorage.setItem(STORAGE_KEY, value ? '1' : '0');
    } catch (error) {
      // Private browsing: the preference simply does not persist.
    }
  }

  function engine() {
    if (context) return context;
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return null;
    try {
      context = new Ctor();
    } catch (error) {
      return null;
    }
    return context;
  }

  function unlock() {
    const ctx = engine();
    if (!ctx) {
      blocked = true;
      syncToggles();
      return false;
    }
    if (ctx.state === 'running') {
      blocked = false;
      syncToggles();
      return true;
    }
    blocked = true;
    if (ctx.resume) {
      ctx.resume().then(() => {
        blocked = false;
        syncToggles();
      }).catch(() => {
        blocked = true;
        syncToggles();
      });
    }
    syncToggles();
    return false;
  }

  function schedule(recipe, rate) {
    const ctx = context;
    const now = ctx.currentTime + 0.01;
    recipe.forEach((note) => {
      const oscillator = ctx.createOscillator();
      const gain = ctx.createGain();
      const start = now + (note.at || 0);
      oscillator.type = 'triangle';
      oscillator.frequency.setValueAtTime(note.f * rate, start);
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(VOLUME, start + 0.008);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + note.d);
      oscillator.connect(gain);
      gain.connect(ctx.destination);
      oscillator.start(start);
      oscillator.stop(start + note.d + 0.03);
    });
  }

  function play(name, options = {}) {
    if (!enabled) return false;
    const ctx = engine();
    if (!ctx) {
      blocked = true;
      syncToggles();
      return false;
    }
    if (ctx.state !== 'running') {
      unlock();
      return false;
    }
    blocked = false;
    schedule(RECIPES[name] || RECIPES.coin, PITCH_BY_AMOUNT[options.amount] || 1);
    syncToggles();
    return true;
  }

  function syncToggles() {
    toggles.forEach((button) => {
      const label = enabled ? (blocked ? 'Sound: tap to enable' : 'Sound on') : 'Sound off';
      if (button.textContent !== label) button.textContent = label;
      button.setAttribute('aria-pressed', enabled ? 'true' : 'false');
      button.dataset.soundBlocked = blocked ? '1' : '0';
      button.title = enabled ? 'Sound effects are on. Click to mute.' : 'Sound effects are muted. Click to enable.';
    });
  }

  function mount(button) {
    if (!button || toggles.has(button)) return;
    toggles.add(button);
    button.addEventListener('click', () => {
      if (enabled && blocked) {
        unlock();
      } else {
        enabled = !enabled;
        writePreference(enabled);
        if (enabled) unlock();
      }
      syncToggles();
      play('success');
    });
    syncToggles();
  }

  function mountAll(root) {
    (root || document).querySelectorAll('[data-sound-toggle]').forEach(mount);
  }

  const wake = () => {
    if (enabled) unlock();
  };
  ['pointerdown', 'keydown', 'touchstart'].forEach((type) => window.addEventListener(type, wake, { passive: true }));
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => mountAll());
  else mountAll();

  return {
    play,
    mount,
    mountAll,
    unlock,
    isEnabled: () => enabled,
    isBlocked: () => blocked,
    setEnabled: (value) => {
      enabled = Boolean(value);
      writePreference(enabled);
      if (enabled) unlock();
      syncToggles();
    },
  };
})();
