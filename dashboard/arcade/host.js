'use strict';
(() => {
  const panel = document.getElementById('panel-arcade');
  const frame = document.getElementById('arcade-frame');
  if (!panel || !frame) return;
  const status = panel.querySelector('.arcade-loading');
  let current = false, profile = '', timer = 0;
  const send = data => frame.contentWindow?.postMessage(data, location.origin);
  function show(name) {
    current = name === 'arcade';
    clearTimeout(timer);
    if (!current) { send({ type: 'data-arcade-visibility', active: false }); return; }
    const activeProfile = window.getArcadeProfile?.() || '';
    if (profile && activeProfile !== profile) reset();
    if (frame.hasAttribute('src')) { send({ type: 'data-arcade-visibility', active: true }); return; }
    if (!activeProfile) {
      status.hidden = false; status.textContent = 'Waiting for your dashboard profile...';
      timer = setTimeout(() => { if (current) show('arcade'); }, 500); return;
    }
    profile = activeProfile;
    frame.src = new URL('arcade/index.html?embedded=1', document.baseURI).href;
    status.hidden = false; status.textContent = 'Loading Void Patrol...';
    timer = setTimeout(() => {
      if (frame.hidden) status.textContent = 'Arcade assets are waiting for the dashboard service update. Reopen this panel after the service restarts.';
    }, 12000);
  }
  function reset() {
    clearTimeout(timer);
    send({ type: 'data-arcade-visibility', active: false });
    frame.removeAttribute('src'); frame.hidden = true; profile = '';
    status.hidden = false; status.textContent = 'Open Arcade to launch Void Patrol.';
    if (current) timer = setTimeout(() => show('arcade'), 600);
  }
  window.addEventListener('message', event => {
    if (event.source !== frame.contentWindow || event.origin !== location.origin || event.data?.type !== 'data-arcade-ready') return;
    if (profile !== window.getArcadeProfile?.()) { reset(); return; }
    clearTimeout(timer);
    send({ type: 'data-arcade-init', profile, active: current });
    status.hidden = true; frame.hidden = false;
  });
  window.ArcadeHost = { show, reset };
  window.addEventListener('pagehide', () => { send({ type: 'data-arcade-visibility', active: false }); });
  window.addEventListener('hashchange', () => { if (location.hash === '#arcade') showPanel('arcade'); });
  if (location.hash === '#arcade') showPanel('arcade');
})();
