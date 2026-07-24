(() => {
  if (window.__swmLiveActionsLoaded) return;
  window.__swmLiveActionsLoaded = true;

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
