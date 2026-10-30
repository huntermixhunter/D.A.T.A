/* The portable library is shared with the desktop dashboards. */
(() => {
  const base = typeof API_BASE !== 'undefined' ? API_BASE : location.origin;
  const panel = document.createElement('section'); panel.className = 'panel'; panel.id = 'panel-offline';
  panel.innerHTML = `<div class="panel-header"><h2>Offline library</h2></div><div style="padding:24px;font:15px/1.6 system-ui,sans-serif;color:var(--ink-1,#eee)">
    <p>Search your saved books and manuals, then ask a local model to explain the sources. Everything runs from the archive drive.</p>
    <p id="oa-status" role="status">Checking for a portable archive...</p>
    <button id="oa-start" class="oa-button" type="button">Connect offline library</button> <button id="oa-refresh" class="oa-button" type="button">Refresh</button>
    <a id="oa-open" target="_blank" rel="noopener" hidden>Open the offline workspace</a>
    <p id="oa-detail"></p><p id="oa-location" style="overflow-wrap:anywhere"></p>
    <details><summary>First-time setup</summary><ol><li>Connect the drive containing your portable archive.</li><li>Keep its models, library, documents and runtime folders together.</li><li>Use Connect offline library to start its local server.</li></ol><p>A fresh DATA install does not include large models or book archives. Install a portable archive first, or set DATA_ARCHIVE_ROOT to its folder before starting the dashboard.</p></details></div>`;
  document.querySelector('main')?.append(panel);
  const button = document.createElement('button');
  const navigation = document.getElementById('sidebar-navigation');
  button.className = (navigation?.querySelector('button')?.className.split(' ').filter(c => c.endsWith('-btn')).join(' ') || 'data-btn') + ' teal sidebar-view-hidden'; button.type = 'button';
  button.textContent = 'OFFLINE LIBRARY'; button.dataset.navPanel = 'offline';
  button.dataset.sidebarLabel = 'Offline Library'; button.dataset.defaultVisible = 'false';
  button.addEventListener('click', () => window.showPanel('offline'));
  document.getElementById('sidebar-navigation')?.append(button);
  const find = id => panel.querySelector('#' + id);
  async function refresh() {
    try {
      const response = await fetch(base + '/offline/status');
      if (!response.ok) throw Error('Once background tasks have finished, restart the updated dashboard server to enable the library connection.');
      const state = await response.json();
      find('oa-status').textContent = state.running ? `Library running. ${state.books} saved collections.` : state.installed ? 'Archive found. Ready to start.' : 'Connect a drive with a portable archive to get started.';
      find('oa-location').textContent = state.root;
      find('oa-start').disabled = state.running || !state.installed;
      const local = ['localhost', '127.0.0.1'].includes(location.hostname);
      find('oa-open').hidden = !state.running || !local; find('oa-open').href = 'http://127.0.0.1:7788/';
      find('oa-detail').textContent = !local ? 'Open the dashboard on the computer holding the drive to use this local workspace.' : state.embeddings?.installed ? state.embeddings.scope : 'Keyword archive search is available. The optional embedding model adds semantic search of indexed manuals and ranks archive results.';
    } catch (error) { find('oa-status').textContent = error.message; }
  }
  find('oa-start').addEventListener('click', async () => {
    find('oa-start').disabled = true;
    try {
      const response = await fetch(base + '/offline/start', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
      const result = await response.json(); if (!response.ok || result.error) throw Error(result.error || 'Could not start archive');
      find('oa-status').textContent = 'Starting the local library...';
      setTimeout(refresh, 3000);
    } catch (error) { find('oa-status').textContent = error.message; find('oa-start').disabled = false; }
  });
  find('oa-refresh').addEventListener('click', refresh);
  new MutationObserver(() => { if (panel.classList.contains('active')) refresh(); }).observe(panel, {attributes:true, attributeFilter:['class']});
})();
