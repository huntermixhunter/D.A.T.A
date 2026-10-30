'use strict';
/* Share the dashboard's resolved palette without reloading an embedded workspace. */
(() => {
  if (window.parent === window) return;
  let host;
  try {
    host = window.parent.document;
    if (!host.body || !host.querySelector('.data-main')) return;
  } catch (_) { return; }

  const root = document.documentElement;
  const aliases = {
    orange: 'orange', orange2: 'orange2', yellow: 'yellow', blue: 'blue',
    lightblue: 'lightblue', purple: 'purple', teal: 'teal', red: 'red',
    green: 'green', pink: 'pink', bg: 'bg', panel: 'bg2', text: 'text', dim: 'text-dim',
    'ftl-green': 'green', shield: 'lightblue',
  };
  function sync() {
    const style = window.parent.getComputedStyle(host.body);
    const token = name => style.getPropertyValue(name).trim();
    const set = (name, value) => {
      if (value) root.style.setProperty('--' + name, value);
      else root.style.removeProperty('--' + name);
    };
    for (const [name, source] of Object.entries(aliases)) set(name, token('--data-' + source));
    set('panel2', 'color-mix(in srgb, var(--panel) 92%, var(--text))');
    const theme = host.body.classList.contains('theme-event-horizon') ? 'event-horizon'
      : host.body.classList.contains('theme-minimal') ? 'minimal' : 'cyber';
    const edge = theme === 'minimal' ? '--min-border' : theme === 'event-horizon' ? '--p-edge' : '--edge-cool-1';
    set('line', token(edge));
    set('edge', token('--data-text-dim'));
    set('font-main', token('--font-main'));
    set('font-mono', token('--font-mono'));
    root.style.colorScheme = style.colorScheme;
    root.dataset.dashboardTheme = theme;
    window.dispatchEvent(new Event('dashboard-theme-change'));
  }

  const observer = new MutationObserver(sync);
  function connect() {
    observer.observe(host.body, { attributes: true, attributeFilter: ['class', 'style'] });
    observer.observe(host.documentElement, { attributes: true, attributeFilter: ['class', 'style'] });
    observer.observe(host.head, { subtree: true, childList: true, characterData: true,
      attributes: true, attributeFilter: ['href', 'media', 'disabled'] });
    host.addEventListener('load', sync, true);
    sync();
  }
  window.addEventListener('pagehide', () => {
    observer.disconnect();
    host.removeEventListener('load', sync, true);
  });
  window.addEventListener('pageshow', event => { if (event.persisted) connect(); });
  connect();
})();
