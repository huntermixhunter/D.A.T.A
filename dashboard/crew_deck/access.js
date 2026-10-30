'use strict';
/* Credentials stay in memory and headers, never in frame URLs. */
(() => {
  let token = '', scope = '';
  let resolveReady, rejectReady;
  const ready = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
  const rawFetch = window.fetch.bind(window);
  async function request(input, init = {}) {
    await ready;
    const url = new URL(input, location.href);
    if (url.origin !== location.origin) throw new Error('Crew requests must stay on this dashboard.');
    const headers = new Headers(init.headers || {});
    headers.set('X-Data-Request', '1');
    headers.set('X-Crew-Scope', scope);
    if (token) headers.set('X-Data-Token', token);
    const response = await rawFetch(url, { ...init, headers });
    if ([401, 403, 409].includes(response.status)) {
      const body = await response.clone().json().catch(() => ({}));
      if ([401, 403].includes(response.status) || /profile changed/i.test(body.error || '')) {
        window.dispatchEvent(new Event('crew-access-lost'));
      }
    }
    return response;
  }
  function accept(config) {
    if (!config || typeof config.scope !== 'string' || !config.scope) return;
    token = String(config.token || ''); scope = config.scope;
    resolveReady();
  }
  window.CrewAccess = { ready, fetch: request, get scope() { return scope; },
    async download(path) {
      const response = await request(path);
      if (!response.ok) throw new Error((await response.json()).error || 'Download failed.');
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url;
      link.download = new URL(path, location.href).searchParams.get('path')?.split('/').pop() || 'artifact.txt';
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  };
  if (window.parent !== window) {
    window.addEventListener('message', event => {
      if (event.source !== parent || event.origin !== location.origin || event.data?.type !== 'crew-deck-init') return;
      accept(event.data);
    });
    parent.postMessage({ type: 'crew-deck-ready' }, location.origin);
    setTimeout(() => { if (!scope) rejectReady(new Error('Reopen Crew Deck from the dashboard to connect.')); }, 15000);
  } else {
    (async () => {
      try { token = localStorage.getItem('data-bridge-token') || ''; } catch (_) { /* Optional browser storage. */ }
      const headers = { 'X-Data-Request': '1' };
      if (token) headers['X-Data-Token'] = token;
      const response = await rawFetch('/user/active', { headers, cache: 'no-store' });
      if (!response.ok) throw new Error('Open Crew Deck from your authenticated dashboard.');
      accept({ token, scope: (await response.json()).active });
      if (!scope) throw new Error('The active profile is unavailable.');
    })().catch(rejectReady);
  }
})();
