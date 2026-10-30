'use strict';
/* The embedded page is loaded only while its navigation panel is selected. */
(() => {
  const panel = document.getElementById('panel-crew-deck');
  const frame = document.getElementById('crew-deck-frame');
  const status = panel.querySelector('.crew-deck-loading');
  let current = false, scope = '', timer;
  function reset() {
    clearTimeout(timer);
    frame.hidden = true;
    frame.removeAttribute('src');
    scope = '';
    status.hidden = false;
    status.textContent = 'Connecting to your crew workspace...';
    if (current) timer = setTimeout(() => show('crew-deck'), 750);
  }
  function show(name) {
    current = name === 'crew-deck';
    if (!current) { reset(); return; }
    if (frame.hasAttribute('src')) return;
    clearTimeout(timer);
    scope = window.getCrewDeckContext().scope || '';
    if (!scope) {
      status.textContent = 'Waiting for your dashboard profile...';
      timer = setTimeout(() => { if (current) show('crew-deck'); }, 600);
      return;
    }
    const url = new URL('crew-deck/', window.getCrewDeckContext().base + '/');
    url.searchParams.set('embedded', '1');
    frame.src = url.href;
    status.textContent = 'Connecting to your crew workspace...';
    timer = setTimeout(() => {
      if (frame.hidden) status.textContent = 'Crew Deck is waiting for the dashboard service update. Reopen this panel after the service restarts.';
    }, 15000);
  }
  window.addEventListener('message', event => {
    if (!current || event.source !== frame.contentWindow || event.origin !== new URL(window.getCrewDeckContext().base).origin || event.data?.type !== 'crew-deck-ready') return;
    if (scope !== window.getCrewDeckContext().scope) { reset(); show('crew-deck'); return; }
    frame.contentWindow.postMessage({ type: 'crew-deck-init', scope, token: window.getCrewDeckContext().token || '' }, event.origin);
    clearTimeout(timer);
    status.hidden = true;
    frame.hidden = false;
  });
  window.CrewDeckHost = { show, reset };
  window.addEventListener('pagehide', reset);
  if (location.hash === '#crew-deck') showPanel('crew-deck');
})();
