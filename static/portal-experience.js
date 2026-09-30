/*
 * Customer portal experience: the first-paint splash and the rates modal.
 *
 * Splash
 *   [data-portal-splash]  overlay that is dismissed once the page has loaded
 *   [data-splash-skip]    lets an impatient customer dismiss it immediately
 *   The overlay also carries a no-JavaScript auto-dismiss animation, and this
 *   script keeps a watchdog so the portal can never be trapped behind it.
 *
 * Modal
 *   [data-modal-open="id"]      opens .portal-modal with that id
 *   [data-modal-close]          closes the enclosing modal (or the backdrop)
 *   [data-portal-scroll="id"]   closes, then scrolls to and focuses that section
 */
(() => {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const scrollToSection = (id) => {
    const target = document.getElementById(id);
    if (!target) return;
    target.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'center' });
    const focusable = target.querySelector('button, input, a[href]');
    if (focusable) window.setTimeout(() => focusable.focus({ preventScroll: true }), reduceMotion ? 0 : 420);
  };

  /* ---------- Splash ---------- */
  const splash = document.querySelector('[data-portal-splash]');
  if (splash) {
    const started = Date.now();
    const minimum = reduceMotion ? 150 : 900;
    let dismissed = false;
    const dismiss = () => {
      if (dismissed) return;
      dismissed = true;
      splash.classList.add('is-dismissed');
      splash.setAttribute('aria-hidden', 'true');
      window.setTimeout(() => { splash.hidden = true; }, reduceMotion ? 0 : 420);
    };
    const schedule = () => window.setTimeout(dismiss, Math.max(0, minimum - (Date.now() - started)));
    if (document.readyState === 'complete') schedule();
    else window.addEventListener('load', schedule, { once: true });
    window.setTimeout(dismiss, 4000);
    const skip = splash.querySelector('[data-splash-skip]');
    if (skip) skip.addEventListener('click', dismiss);
  }

  /* ---------- Modal ---------- */
  const modals = [...document.querySelectorAll('.portal-modal')];
  if (!modals.length) return;
  let opener = null;

  const openModal = (modal, trigger) => {
    if (!modal) return;
    opener = trigger || null;
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('portal-modal-open');
    const focusable = modal.querySelector('button, input, a[href]');
    if (focusable) focusable.focus({ preventScroll: true });
  };
  const closeModal = (modal) => {
    if (!modal) return;
    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
    if (!document.querySelector('.portal-modal.is-open')) document.body.classList.remove('portal-modal-open');
    if (opener) {
      opener.focus({ preventScroll: true });
      opener = null;
    }
  };

  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-modal-open]');
    if (trigger) {
      openModal(document.getElementById(trigger.dataset.modalOpen), trigger);
      return;
    }
    const scroll = event.target.closest('[data-portal-scroll]');
    if (scroll) window.setTimeout(() => scrollToSection(scroll.dataset.portalScroll), reduceMotion ? 0 : 140);
    const closer = event.target.closest('[data-modal-close]');
    if (closer) {
      closeModal(closer.closest('.portal-modal'));
      return;
    }
    if (event.target.classList.contains('portal-modal')) closeModal(event.target);
  });

  document.addEventListener('keydown', (event) => {
    const modal = document.querySelector('.portal-modal.is-open');
    if (!modal) return;
    if (event.key === 'Escape') {
      closeModal(modal);
      return;
    }
    if (event.key !== 'Tab') return;
    const items = [...modal.querySelectorAll('button, input, a[href], [tabindex]:not([tabindex="-1"])')]
      .filter((element) => !element.disabled && !element.hidden);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
})();
