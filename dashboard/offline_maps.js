/* Offline maps. All render assets and tile reads stay on the dashboard host. */
(() => {
  'use strict';
  const base = typeof API_BASE !== 'undefined' ? API_BASE : location.origin;
  const node = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  };
  let panel = document.getElementById('panel-maps');
  const navigation = document.getElementById('sidebar-navigation');
  if (!panel) {
    panel = node('section', undefined, 'panel'); panel.id = 'panel-maps';
    document.querySelector('main')?.append(panel);
  }
  if (!navigation?.querySelector('[data-nav-panel="maps"], [onclick*="\'maps\'"]')) {
    const btn = node('button', 'MAPS', (navigation.querySelector('button')?.className.split(' ').filter(c => c.endsWith('-btn')).join(' ') || 'data-btn') + ' green sidebar-view-hidden');
    btn.type = 'button'; btn.dataset.navPanel = 'maps';
    btn.dataset.sidebarLabel = 'Maps'; btn.dataset.defaultVisible = 'false';
    btn.addEventListener('click', () => window.showPanel('maps'));
    navigation?.append(btn);
  }
  panel.innerHTML = `<div class="om-header"><div><span class="om-eyebrow">SAVED ON THIS DRIVE</span><h2>Offline maps</h2></div><button type="button" id="om-refresh">Refresh</button></div>
    <p id="om-status" role="status" aria-live="polite">Checking map files...</p>
    <div class="om-layout"><aside class="om-sidebar"><section id="om-setup"></section>
    <label for="om-region">Saved regions</label><select id="om-region"><option value="">No regions installed</option></select>
    <label for="om-flavor">Map appearance</label><select id="om-flavor"><option value="light">Light / daylight</option><option value="dark">Dark / low light</option></select>
    <form id="om-coordinates"><label for="om-coordinate-input">Go to coordinates</label><div class="om-inline"><input id="om-coordinate-input" placeholder="48.2766, -116.5532" aria-describedby="om-coordinate-help"><button>Go</button></div><small id="om-coordinate-help">Latitude, longitude. Place-name search and route directions are not included.</small></form>
    <details id="om-downloads" open><summary>Add a region</summary><p class="om-note">Downloads need internet once. Rough size allowances below are not measured download sizes.</p><div id="om-catalog"></div></details>
    <details><summary>Storage and tasks</summary><p id="om-storage" class="om-note"></p><div id="om-jobs"></div></details></aside>
    <div class="om-map-wrap"><div id="om-canvas" aria-label="Interactive offline map"></div><div id="om-empty"><span class="om-compass" aria-hidden="true">+</span><h3>Your map, without a connection</h3><p>Install the free viewer, then save a region. Roads, labels and map tiles will load from your drive.</p></div><p id="om-map-note">Saved maps do not show current road closures, hazards or live traffic.</p></div></div>`;
  const find = id => panel.querySelector('#' + id);
  let state = null, catalog = [], loading = false, map = null, scripts = null, selected = '', activeJobs = false;
  const size = n => Number(n) >= 1024 ** 3 ? `${(Number(n) / 1024 ** 3).toFixed(1)} GiB` : `${(Number(n || 0) / 1024 ** 2).toFixed(1)} MiB`;
  const feedback = text => { find('om-status').textContent = text; };
  async function api(path, data) {
    const response = await fetch(base + '/maps/' + path, data === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
    });
    if (response.status === 404) throw Error('Maps needs the updated dashboard server. Once background tasks have finished, restart the dashboard and refresh.');
    const result = await response.json();
    if (!response.ok || result.error) throw Error(result.error || 'Map request failed');
    return result;
  }
  function action(text, run) {
    const btn = node('button', text); btn.type = 'button';
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try { await run(); } catch (error) { feedback(error.message); }
      finally { btn.disabled = false; }
    });
    return btn;
  }
  async function start(path, data) {
    const result = await api(path, data);
    feedback(result.already ? 'This region is already saved.' : 'Task started. You can leave this page; the dashboard server owns the download.');
    await refresh();
  }
  function render() {
    const setup = find('om-setup'); setup.replaceChildren();
    if (!state.stack_ready || !state.can_download) {
      setup.append(node('h3', state.stack_ready ? 'Download tools needed' : 'Set up your map viewer'),
        node('p', '1. Install the free map tools and local fonts. 2. Download a region below. Keep this drive connected while using maps.'),
        action('Install free map tools', () => start('install', {})));
    } else setup.append(node('p', 'Viewer and fonts are stored locally.', 'om-ready'));
    find('om-storage').textContent = `${state.root}\n${size(state.region_bytes)} of maps. ${size(state.disk_free)} free on this drive.`;
    const select = find('om-region'); select.replaceChildren();
    if (!state.regions.length) select.append(new Option('No regions installed', ''));
    for (const region of state.regions) select.append(new Option(`${region.name} (${size(region.bytes)})`, region.slug));
    if (!state.regions.some(r => r.slug === selected)) selected = state.regions.find(r => r.slug === 'idaho-panhandle')?.slug || state.regions[0]?.slug || '';
    select.value = selected; select.disabled = !state.regions.length;
    const list = find('om-catalog'); list.replaceChildren();
    activeJobs = state.setup_active || (state.jobs || []).some(job => job.status === 'running');
    for (const region of catalog) {
      const saved = state.regions.some(r => r.slug === region.slug);
      const item = node('article', undefined, 'om-region-card');
      item.append(node('h4', region.name), node('p', region.detail), node('small', `Up to zoom ${region.maxzoom}. Allow about ${region.est_gb} GB.`));
      const btn = action(saved ? 'Saved' : 'Download region', () => start('region', {slug: region.slug}));
      btn.disabled = saved || !state.stack_ready || !state.can_download || activeJobs;
      item.append(btn); list.append(item);
    }
    const jobs = find('om-jobs'); jobs.replaceChildren();
    for (const job of (state.jobs || []).slice(0, 4)) {
      const item = node('div', undefined, 'om-job');
      item.append(node('strong', `${job.label}: ${job.status}`));
      if (job.output) item.append(node('pre', job.output));
      jobs.append(item);
    }
    if (activeJobs) setup.querySelectorAll('button').forEach(btn => { btn.disabled = true; });
    feedback(state.ready ? `${state.regions.length} saved region${state.regions.length === 1 ? '' : 's'}. Ready for offline viewing.` : state.setup_active ? 'Archive setup is running. Saved regions will appear here as they finish.' : state.stack_ready ? 'Viewer ready. Download a region to see the map.' : 'The map viewer is not installed yet.');
  }
  function libraryScript(name) {
    return new Promise((resolve, reject) => {
      const el = document.createElement('script'); el.src = base + '/maps/data/assets/' + name;
      el.onload = resolve; el.onerror = () => { el.remove(); reject(Error('Could not load ' + name + '. Check the drive and map installation.')); };
      document.head.append(el);
    });
  }
  async function libraries() {
    if (!scripts) scripts = (async () => {
      if (!document.getElementById('om-maplibre-css')) {
        const css = node('link'); css.id = 'om-maplibre-css'; css.rel = 'stylesheet'; css.href = base + '/maps/data/assets/maplibre-gl.css'; document.head.append(css);
      }
      if (!window.maplibregl) { const mod = await import(base + '/maps/data/assets/maplibre-gl.mjs'); window.maplibregl = mod.default || mod; }
      if (!window.pmtiles) await libraryScript('pmtiles.js');
      if (!window.basemaps) await libraryScript('basemaps.js');
      const protocol = new pmtiles.Protocol(); maplibregl.addProtocol('pmtiles', protocol.tile);
    })().catch(error => { scripts = null; throw error; });
    return scripts;
  }
  async function draw() {
    if (!state?.ready || !selected) return;
    const region = state.regions.find(r => r.slug === selected); if (!region) return;
    await libraries();
    const flavor = find('om-flavor').value;
    const style = {version: 8, glyphs: base + '/maps/data/assets/fonts/{fontstack}/{range}.pbf',
      sprite: base + '/maps/data/assets/sprites/v4/' + flavor,
      sources: {local: {type: 'vector', url: 'pmtiles://' + base + region.url,
        attribution: '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a> | <a href="https://protomaps.com" target="_blank" rel="noopener">Protomaps</a>'}},
      layers: basemaps.layers('local', basemaps.namedFlavor(flavor), {lang: 'en'})};
    find('om-empty').hidden = true;
    if (!map) {
      map = new maplibregl.Map({container: find('om-canvas'), style, center: [0, 25], zoom: 2, attributionControl: true});
      map.addControl(new maplibregl.NavigationControl(), 'top-right');
      map.addControl(new maplibregl.ScaleControl({unit: 'metric'}));
      map.on('error', event => feedback('Map could not finish loading: ' + (event.error?.message || 'Check local assets and region data.')));
    } else map.setStyle(style);
    if (region.bbox) map.fitBounds([[region.bbox[0], region.bbox[1]], [region.bbox[2], region.bbox[3]]], {padding: 30, duration: 0});
    map.resize();
    find('om-map-note').textContent = `${region.name}. Saved detail through zoom ${region.maxzoom || '?'}. No live closures, traffic or route directions.`;
  }
  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const previous = state?.regions.map(r => r.url).join('|');
      const result = await Promise.all([api('status'), api('catalog')]);
      state = result[0]; catalog = result[1].catalog; render();
      if (!map || previous !== state.regions.map(r => r.url).join('|')) await draw();
      else map.resize();
    } catch (error) { feedback(error.message); }
    finally { loading = false; }
  }
  find('om-refresh').addEventListener('click', refresh);
  find('om-region').addEventListener('change', async event => { selected = event.target.value; try { await draw(); } catch (error) { feedback(error.message); } });
  find('om-flavor').addEventListener('change', async () => { try { await draw(); } catch (error) { feedback(error.message); } });
  find('om-coordinates').addEventListener('submit', event => {
    event.preventDefault();
    const parts = find('om-coordinate-input').value.split(',');
    const values = parts.map(v => Number(v.trim()));
    if (values.length !== 2 || parts.some(v => !v.trim()) || !values.every(Number.isFinite) || Math.abs(values[0]) > 85 || Math.abs(values[1]) > 180) return feedback('Enter latitude, longitude. Example: 48.2766, -116.5532');
    if (!map) return feedback('Install and select a region first.');
    const box = state.regions.find(r => r.slug === selected)?.bbox;
    if (box && (values[1] < box[0] || values[1] > box[2] || values[0] < box[1] || values[0] > box[3])) return feedback('Those coordinates are outside the selected region. Choose a region that covers them.');
    map.jumpTo({center: [values[1], values[0]], zoom: 12});
  });
  new MutationObserver(() => { if (panel.classList.contains('active')) refresh(); }).observe(panel, {attributes: true, attributeFilter: ['class']});
  new ResizeObserver(() => map?.resize()).observe(find('om-canvas'));
  setInterval(() => { if (activeJobs && panel.classList.contains('active')) refresh(); }, 7000);
  window.OfflineMapsUI = {refresh, get map() { return map; }};
  // Support old cached markup until the next full page refresh.
  window.loadMaps = refresh;
  window.toggleMapRegions = () => { find('om-downloads').open = !find('om-downloads').open; };
  if (typeof SidebarView !== 'undefined') SidebarView.refresh();
  if (location.hash === '#maps') {
    if (typeof SidebarView === 'undefined' || SidebarView.visible('maps')) window.showPanel('maps');
    else window.openSettingsTab('view');
  }
})();
