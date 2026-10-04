(() => {
  const modal = document.getElementById('coin-session-modal');
  if (!modal) return;

  const form = document.querySelector('.single-coin-form');
  const waitingBanner = document.getElementById('coin-waiting');
  const coinCopy = document.querySelector('#coin-action .action-copy');
  const coinStatus = document.querySelector('#coin-action .action-status');
  const coinButton = document.querySelector('#coin-action .single-coin-form button');
  const count = modal.querySelector('#coin-session-count');
  const amount = modal.querySelector('#coin-session-amount');
  const minutes = modal.querySelector('#coin-session-minutes');
  const reward = modal.querySelector('#coin-session-reward');
  const countdown = modal.querySelector('#coin-session-countdown');
  const state = modal.querySelector('#coin-session-state');
  const message = modal.querySelector('#coin-session-message');
  const error = modal.querySelector('#coin-session-error');
  const cancel = modal.querySelector('#coin-session-cancel');
  const extend = modal.querySelector('#coin-session-extend');
  const finish = modal.querySelector('#coin-session-finish');
  let requestId = null;
  let coinCount = 0;
  let secondsRemaining = 0;
  let pollTimer = null;
  let tickTimer = null;
  let busy = false;

  if (coinCopy) coinCopy.textContent = 'Open a coin session, insert your coins, then finish when you are done. Need more time? Extend the countdown from the coin window.';
  if (coinStatus) coinStatus.textContent = 'Your coin total and insertion countdown stay visible in the popup.';
  if (coinButton) coinButton.textContent = 'Open coin window';

  const show = () => {
    modal.hidden = false;
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('coin-session-open');
    modal.querySelector('#coin-session-extend').focus({ preventScroll: true });
  };

  const hide = () => {
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('coin-session-open');
    window.clearInterval(pollTimer);
    window.clearInterval(tickTimer);
    pollTimer = null;
    tickTimer = null;
  };

  const postJson = async (url, body = {}) => {
    const response = await fetch(url, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Coin request failed.');
    return result;
  };

  const applyRequest = (data) => {
    if (data.id) requestId = data.id;
    coinCount = Number(data.coin_count || 0);
    secondsRemaining = Math.max(0, Number(data.seconds_remaining || 0));
    count.textContent = String(coinCount);
    amount.textContent = `P${Number(data.amount || 0).toFixed(0)}`;
    minutes.textContent = String(data.minutes || 0);
    reward.textContent = coinCount
      ? `Current total: ${(data.reward && data.reward.summary) || `${data.minutes} minutes`}.`
      : 'No coins inserted yet. Cancel is available until the first coin lands.';
    cancel.hidden = coinCount > 0;
    finish.hidden = coinCount < 1;
    finish.disabled = coinCount < 1 || busy;
    state.textContent = coinCount ? 'COINS RECEIVED' : 'WAITING FOR COIN';
    renderCountdown();
  };

  const renderCountdown = () => {
    const minutesLeft = Math.floor(secondsRemaining / 60);
    const secondsLeft = secondsRemaining % 60;
    countdown.textContent = `${String(minutesLeft).padStart(2, '0')}:${String(secondsLeft).padStart(2, '0')}`;
  };

  const poll = async () => {
    if (!requestId || busy) return;
    try {
      const response = await fetch(modal.dataset.statusUrl, {
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) return;
      const data = await response.json();
      if (data.status === 'pending' && data.id === requestId) {
        applyRequest(data);
        return;
      }
      if (data.status === 'pending' && requestId === null) {
        requestId = data.id;
        show();
        applyRequest(data);
        message.textContent = 'Your coin request is open. Each coin and each time extension refreshes the countdown.';
        beginTimers();
        return;
      }
      if (data.status === 'accepted') {
        state.textContent = 'CREDITED';
        message.textContent = 'Payment accepted. Loading your active session...';
        if (data.session_active) window.location.reload();
        return;
      }
      if (data.status === 'expired' || data.status === 'cancelled' || data.status === 'none') {
        state.textContent = data.status === 'expired' ? 'TIMED OUT' : 'CLOSED';
        message.textContent = data.status === 'expired' ? 'Request timed out. Open a new coin request to try again.' : 'Coin request closed.';
        requestId = null;
        if (form) form.querySelector('button[type="submit"]').disabled = false;
        window.clearInterval(pollTimer);
        window.clearInterval(tickTimer);
        window.setTimeout(hide, 1200);
      }
    } catch {
      error.textContent = 'Connection interrupted. Still checking the request...';
    }
  };

  const beginTimers = () => {
    window.clearInterval(pollTimer);
    window.clearInterval(tickTimer);
    pollTimer = window.setInterval(poll, 1000);
    tickTimer = window.setInterval(() => {
      if (secondsRemaining > 0) secondsRemaining -= 1;
      renderCountdown();
    }, 1000);
  };

  const startRequest = async () => {
    if (busy) return;
    busy = true;
    error.textContent = '';
    message.textContent = 'Opening your coin request...';
    show();
    try {
      const data = await postJson(modal.dataset.startUrl);
      applyRequest(data);
      message.textContent = 'Insert coins now. Each coin refreshes this inactivity countdown.';
      beginTimers();
    } catch (requestError) {
      error.textContent = requestError.message;
      state.textContent = 'NOT STARTED';
    } finally {
      busy = false;
      finish.disabled = coinCount < 1;
    }
  };

  const extendTime = async () => {
    if (!requestId || busy) return;
    busy = true;
    extend.disabled = true;
    error.textContent = '';
    try {
      const data = await postJson(modal.dataset.extendUrl);
      applyRequest(data);
      message.textContent = 'Insertion time extended. Insert another coin or finish your total.';
    } catch (requestError) {
      error.textContent = requestError.message;
    } finally {
      busy = false;
      extend.disabled = false;
      finish.disabled = coinCount < 1;
    }
  };

  const cancelRequest = async () => {
    if (!requestId || coinCount > 0 || busy) return;
    busy = true;
    cancel.disabled = true;
    try {
      await postJson(modal.dataset.cancelUrl);
      hide();
      requestId = null;
      if (form) form.querySelector('button[type="submit"]').disabled = false;
    } catch (requestError) {
      error.textContent = requestError.message;
    } finally {
      busy = false;
      cancel.disabled = false;
    }
  };

  const finishRequest = async () => {
    if (!requestId || coinCount < 1 || busy) return;
    busy = true;
    finish.disabled = true;
    message.textContent = 'Crediting your coins...';
    error.textContent = '';
    try {
      const data = await postJson(modal.dataset.completeUrl);
      state.textContent = 'CREDITED';
      if (data.session_active) window.location.reload();
      else message.textContent = 'Payment accepted. Waiting for the active session to appear...';
      await poll();
    } catch (requestError) {
      error.textContent = requestError.message;
      finish.disabled = false;
    } finally {
      busy = false;
    }
  };

  if (form) {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      form.querySelector('button[type="submit"]').disabled = true;
      startRequest();
    });
  }

  cancel.addEventListener('click', cancelRequest);
  extend.addEventListener('click', extendTime);
  finish.addEventListener('click', finishRequest);

  document.addEventListener('keydown', (event) => {
    if (modal.hidden) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      message.textContent = coinCount ? 'Finish inserting coins to close this request.' : 'Use Cancel request to release the coin slot.';
    }
  });

  if (waitingBanner) poll();
})();
