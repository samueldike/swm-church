(() => {
  if (window.__swmSiteConfirmLoaded) return;
  window.__swmSiteConfirmLoaded = true;

  const style = document.createElement('style');
  style.textContent = `
    .swm-confirm-overlay {
      position: fixed;
      inset: 0;
      z-index: 9999;
      display: grid;
      place-items: center;
      padding: 1rem;
      background: rgba(10, 10, 10, 0.55);
      opacity: 0;
      pointer-events: none;
      transition: opacity 180ms ease;
    }
    .swm-confirm-overlay.is-open {
      opacity: 1;
      pointer-events: auto;
    }
    .swm-confirm-dialog {
      width: min(100%, 25rem);
      border: 1px solid rgba(229, 231, 235, 0.9);
      border-radius: 1rem;
      background: #ffffff;
      box-shadow: 0 24px 70px rgba(15, 23, 42, 0.28);
      padding: 1.25rem;
      color: #111827;
      transform: translateY(14px) scale(0.96);
      opacity: 0;
      transition: transform 220ms cubic-bezier(.2,.8,.2,1), opacity 180ms ease;
    }
    .swm-confirm-overlay.is-open .swm-confirm-dialog {
      transform: translateY(0) scale(1);
      opacity: 1;
    }
    .swm-confirm-icon {
      width: 3rem;
      height: 3rem;
      border-radius: 999px;
      display: grid;
      place-items: center;
      margin-bottom: 0.9rem;
      background: #fef2f2;
      color: #dc2626;
      animation: swm-confirm-pop 360ms cubic-bezier(.2,.8,.2,1);
    }
    .swm-confirm-icon svg {
      width: 1.45rem;
      height: 1.45rem;
      stroke-width: 2.4;
    }
    .swm-confirm-title {
      margin: 0;
      font-size: 1rem;
      line-height: 1.35;
      font-weight: 800;
      letter-spacing: 0;
    }
    .swm-confirm-message {
      margin: 0.45rem 0 0;
      font-size: 0.875rem;
      line-height: 1.55;
      color: #6b7280;
    }
    .swm-confirm-actions {
      display: flex;
      justify-content: flex-end;
      gap: 0.6rem;
      margin-top: 1.1rem;
    }
    .swm-confirm-btn {
      min-height: 2.5rem;
      border: 0;
      border-radius: 0.75rem;
      padding: 0.6rem 1rem;
      font-size: 0.875rem;
      font-weight: 800;
      cursor: pointer;
      transition: transform 140ms ease, background-color 140ms ease, color 140ms ease;
    }
    .swm-confirm-btn:hover {
      transform: translateY(-1px);
    }
    .swm-confirm-cancel {
      background: #f3f4f6;
      color: #374151;
    }
    .swm-confirm-accept {
      background: #dc2626;
      color: #ffffff;
    }
    .dark .swm-confirm-dialog {
      background: #171a21;
      border-color: #303642;
      color: #f3f4f6;
    }
    .dark .swm-confirm-message {
      color: #c4c9d4;
    }
    .dark .swm-confirm-cancel {
      background: #252a35;
      color: #f3f4f6;
    }
    @keyframes swm-confirm-pop {
      0% { transform: scale(0.75); opacity: 0; }
      70% { transform: scale(1.08); opacity: 1; }
      100% { transform: scale(1); opacity: 1; }
    }
    @media (prefers-reduced-motion: reduce) {
      .swm-confirm-overlay,
      .swm-confirm-dialog,
      .swm-confirm-icon,
      .swm-confirm-btn {
        animation: none;
        transition: none;
      }
    }
  `;
  document.head.appendChild(style);

  const overlay = document.createElement('div');
  overlay.className = 'swm-confirm-overlay';
  overlay.setAttribute('role', 'presentation');
  overlay.innerHTML = `
    <section class="swm-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="swm-confirm-title" aria-describedby="swm-confirm-message">
      <div class="swm-confirm-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <path d="M12 9v4"></path>
          <path d="M12 17h.01"></path>
          <path d="M10.3 3.7 2.7 18a2 2 0 0 0 1.8 3h15a2 2 0 0 0 1.8-3L13.7 3.7a2 2 0 0 0-3.4 0Z"></path>
        </svg>
      </div>
      <h2 id="swm-confirm-title" class="swm-confirm-title">Confirm Action</h2>
      <p id="swm-confirm-message" class="swm-confirm-message"></p>
      <div class="swm-confirm-actions">
        <button type="button" class="swm-confirm-btn swm-confirm-cancel" data-confirm-cancel>Cancel</button>
        <button type="button" class="swm-confirm-btn swm-confirm-accept" data-confirm-accept>Yes, continue</button>
      </div>
    </section>
  `;
  document.body.appendChild(overlay);

  const title = overlay.querySelector('#swm-confirm-title');
  const message = overlay.querySelector('#swm-confirm-message');
  const cancelButton = overlay.querySelector('[data-confirm-cancel]');
  const acceptButton = overlay.querySelector('[data-confirm-accept]');
  let resolver = null;
  let lastFocus = null;

  const close = (result) => {
    overlay.classList.remove('is-open');
    document.body.classList.remove('overflow-hidden');
    const resolve = resolver;
    resolver = null;
    if (lastFocus && typeof lastFocus.focus === 'function') lastFocus.focus();
    if (resolve) resolve(result);
  };

  const openConfirm = ({ heading = 'Confirm Action', text = 'Are you sure you want to continue?', accept = 'Yes, continue', cancel = 'Cancel' } = {}) => {
    if (!overlay.isConnected) document.body.appendChild(overlay);
    title.textContent = heading;
    message.textContent = text;
    acceptButton.textContent = accept;
    cancelButton.textContent = cancel;
    lastFocus = document.activeElement;
    overlay.classList.add('is-open');
    document.body.classList.add('overflow-hidden');
    window.setTimeout(() => cancelButton.focus(), 0);

    return new Promise((resolve) => {
      resolver = resolve;
    });
  };

  cancelButton.addEventListener('click', () => close(false));
  acceptButton.addEventListener('click', () => close(true));
  overlay.addEventListener('click', (event) => {
    if (event.target === overlay) close(false);
  });
  document.addEventListener('keydown', (event) => {
    if (!overlay.classList.contains('is-open')) return;
    if (event.key === 'Escape') close(false);
  });

  document.addEventListener('submit', async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.dataset.confirm) return;
    if (form.dataset.confirmApproved === '1') {
      delete form.dataset.confirmApproved;
      return;
    }

    event.preventDefault();
    const approved = await openConfirm({
      heading: form.dataset.confirmTitle || 'Confirm Action',
      text: form.dataset.confirm,
      accept: form.dataset.confirmAccept || 'Yes, continue',
      cancel: form.dataset.confirmCancel || 'Cancel',
    });

    if (!approved) return;
    form.dataset.confirmApproved = '1';
    if (typeof form.requestSubmit === 'function') {
      form.requestSubmit();
    } else {
      form.submit();
    }
  }, true);

  window.SWMConfirm = openConfirm;
})();
