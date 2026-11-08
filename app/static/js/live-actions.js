(() => {
  if (window.__swmLiveActionsLoaded) return;
  window.__swmLiveActionsLoaded = true;

  // Every password field gets an eye button that shows or hides what was typed, including fields
  // that Turbo swaps in or scripts add later.
  const EYE = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.9 17.9A10.4 10.4 0 0 1 12 19c-6.4 0-10-7-10-7a18.5 18.5 0 0 1 5.1-5.9M9.9 5.2A9.7 9.7 0 0 1 12 5c6.4 0 10 7 10 7a18.6 18.6 0 0 1-2.2 3.2M14.1 14.1a3 3 0 1 1-4.2-4.2M2 2l20 20"/></svg>';
  const style = document.createElement('style');
  style.textContent = '.pw-wrap{position:relative;display:block}.pw-wrap>input{padding-right:2.75rem!important;width:100%}'
    + '.pw-eye{position:absolute;top:50%;right:.35rem;transform:translateY(-50%);display:grid;place-items:center;width:2.25rem;height:2.25rem;'
    + 'border:0;border-radius:.4rem;background:none;color:inherit;opacity:.65;cursor:pointer}.pw-eye:hover,.pw-eye:focus-visible{opacity:1}';
  document.head.appendChild(style);
  const setEye = (btn, shown) => {
    btn.innerHTML = shown ? EYE_OFF : EYE;
    btn.setAttribute('aria-label', shown ? 'Hide password' : 'Show password');
    btn.setAttribute('aria-pressed', String(shown));
    btn.title = btn.getAttribute('aria-label');
  };
  const addEye = (input) => {
    if (input.dataset.pwEye) return;
    input.dataset.pwEye = '1';
    const wrap = document.createElement('span');
    wrap.className = 'pw-wrap';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'pw-eye';
    setEye(btn, false);
    btn.addEventListener('click', () => {
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      setEye(btn, show);
    });
    wrap.appendChild(btn);
    // Hide it again on submit, so the browser never saves or shows it as plain text.
    input.form?.addEventListener('submit', () => { input.type = 'password'; setEye(btn, false); });
  };
  const scanPasswords = (root) => root.querySelectorAll?.('input[type="password"]:not([data-pw-eye])').forEach(addEye);
  scanPasswords(document);
  document.addEventListener('DOMContentLoaded', () => scanPasswords(document));
  document.addEventListener('turbo:load', () => scanPasswords(document));
  document.addEventListener('turbo:frame-load', (e) => scanPasswords(e.target));
  new MutationObserver((records) => records.forEach((r) => r.addedNodes.forEach((n) => {
    if (n.nodeType !== 1) return;
    if (n.matches('input[type="password"]')) addEye(n); else scanPasswords(n);
  }))).observe(document.documentElement, { childList: true, subtree: true });

  if (window.Turbo) {
    window.Turbo.session.drive = true;
    window.Turbo.setProgressBarDelay(120);
    if (window.Turbo.config?.forms && window.SWMConfirm) {
      window.Turbo.config.forms.confirm = (message) => window.SWMConfirm({
        heading: 'Confirm Action',
        text: message,
        accept: 'Yes, continue',
      });
    }
  }

  document.addEventListener('turbo:before-fetch-request', (event) => {
    event.detail.fetchOptions.headers['X-SWM-Live-Action'] = '1';
  });

  document.addEventListener('turbo:submit-start', (event) => {
    event.target?.querySelectorAll?.('button[type="submit"], input[type="submit"]').forEach((button) => {
      button.dataset.liveOriginalText = button.textContent || '';
      button.disabled = true;
      if (button.tagName === 'BUTTON') button.textContent = button.dataset.loadingText || 'Please wait...';
    });
  });

  document.addEventListener('turbo:submit-end', (event) => {
    event.target?.querySelectorAll?.('button[type="submit"], input[type="submit"]').forEach((button) => {
      button.disabled = false;
      if (button.tagName === 'BUTTON' && button.dataset.liveOriginalText !== undefined) {
        button.textContent = button.dataset.liveOriginalText;
      }
      delete button.dataset.liveOriginalText;
    });
  });

  document.addEventListener('turbo:before-cache', () => {
    document.querySelectorAll('[aria-hidden="false"].fixed.inset-0').forEach((modal) => {
      modal.classList.add('hidden');
      modal.setAttribute('aria-hidden', 'true');
    });
    document.body.classList.remove('overflow-hidden');
  });
})();
