/* Optional applications: setup records and explicit checks, without execution. */
(() => {
  'use strict';
  const root = document.getElementById('advanced-tools-list');
  if (!root) return;
  const status = document.getElementById('advanced-tools-status');
  const listingTools = Object.freeze({
    'library-framepack-video-studio': 'framepack',
    'library-image-studio': 'comfyui',
    'library-local-transcription': 'whispercpp',
  });
  let loading = null;
  let loaded = false;
  let pendingTool = '';

  function node(tag, attrs = {}, children = []) {
    const element = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key.startsWith('on')) element.addEventListener(key.slice(2), value);
      else if (value !== undefined && value !== null) element.setAttribute(key, value);
    }
    for (const child of [children].flat()) element.append(child instanceof Node ? child : document.createTextNode(String(child)));
    return element;
  }
  const button = (label, action) => node('button', { type: 'button', class: 'data-btn-sm', onclick: action }, label);

  async function api(path, data) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(`${API_BASE}/advanced-tools/${path}`, {
        method: data === undefined ? 'GET' : 'POST', credentials: 'same-origin',
        headers: { 'X-Data-Request': '1', ...(data === undefined ? {} : { 'Content-Type': 'application/json' }) },
        body: data === undefined ? undefined : JSON.stringify(data), signal: controller.signal,
      });
      let result;
      try { result = await response.json(); }
      catch (_) { throw new Error('Advanced tools are unavailable. Start the updated DATA server and try again.'); }
      if (!response.ok) throw new Error(result.error || 'Could not complete the tool request.');
      return result;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('The tool check timed out. Try again.');
      throw error;
    } finally { clearTimeout(timer); }
  }

  function renderCard(tool) {
    const card = node('article', { class: 'advanced-tool-card', id: 'advanced-tool-' + tool.id });
    const saved = node('span', { class: 'advanced-tool-badge' }, tool.saved ? 'Locations saved' : 'Not configured');
    const compatibility = node('span', { class: 'advanced-tool-badge', 'data-state': tool.compatibility.state }, tool.compatibility.label);
    card.append(node('div', { class: 'advanced-tool-summary' }, [
      node('div', {}, [node('span', { class: 'advanced-tool-category' }, tool.category), node('h5', {}, tool.name), node('p', {}, tool.description)]),
      node('div', { class: 'advanced-tool-badges' }, [compatibility, saved]),
    ]));
    const details = node('details');
    details.append(node('summary', {}, 'Setup and connection'));
    const facts = node('dl', { class: 'advanced-tool-facts' });
    for (const [label, value] of [['Application', tool.engine], ['Requirements', tool.requirements], ['Downloads', tool.download], ['Costs', tool.costs], ['This computer', tool.compatibility.detail]]) {
      facts.append(node('div', {}, [node('dt', {}, label), node('dd', {}, value)]));
    }
    details.append(facts, node('a', { href: tool.source, target: '_blank', rel: 'noopener noreferrer', class: 'advanced-tool-source' }, 'Official installation instructions'),
      node('ol', { class: 'advanced-tool-steps' }, tool.steps.map(step => node('li', {}, step))));
    const form = node('form', { class: 'advanced-tool-form', 'aria-label': tool.name + ' setup' });
    const fields = {};
    const addField = (key, label, hint) => {
      const id = `advanced-${tool.id}-${key}`;
      fields[key] = node('input', { id, name: key, type: 'text', autocomplete: 'off', spellcheck: 'false', maxlength: key === 'url' ? 200 : 1024, value: tool.settings[key] || '', 'aria-describedby': id + '-hint' });
      form.append(node('div', { class: 'advanced-tool-field' }, [node('label', { for: id }, label), fields[key], node('small', { id: id + '-hint' }, hint)]));
    };
    addField('folder', 'Application folder', 'Optional absolute folder path on this computer. Files are checked without running them.');
    if (tool.default_url) addField('url', 'Local address', 'Use the loopback address shown by the app. Start it yourself before checking the connection.');
    else addField('model', 'Model file', 'Absolute path to your downloaded whisper.cpp model file.');
    const feedback = node('p', { class: 'advanced-tool-feedback', role: 'status', 'aria-live': 'polite' }, tool.files.label);
    const resultActions = node('div', { class: 'advanced-tool-actions' });
    const save = node('button', { type: 'submit', class: 'data-btn-sm' }, 'Save locations');
    let working = false;
    const perform = async action => {
      if (working) return;
      working = true;
      const controls = [...form.querySelectorAll('button, input')];
      controls.forEach(control => { control.disabled = true; });
      feedback.textContent = action === 'check' ? 'Checking...' : 'Saving...';
      resultActions.replaceChildren();
      try {
        let result;
        if (action === 'forget') {
          result = await api(tool.id + '/forget', {});
          fields.folder.value = '';
          if (fields.url) fields.url.value = tool.default_url;
          if (fields.model) fields.model.value = '';
          saved.textContent = 'Not configured';
        } else {
          const values = Object.fromEntries(Object.entries(fields).map(([key, field]) => [key, field.value.trim()]));
          result = await api(tool.id + '/save', values);
          saved.textContent = 'Locations saved';
          if (action === 'check') {
            result = await api(tool.id + '/check', {});
            if (result.state === 'connected' && result.url) {
              const url = new URL(result.url);
              if (url.protocol === 'http:' && ['127.0.0.1', '[::1]'].includes(url.hostname) && url.port) {
                resultActions.append(node('a', { href: url.href, target: '_blank', rel: 'noopener noreferrer', class: 'data-btn-sm' }, 'Open local app'));
              }
            }
          }
        }
        feedback.textContent = result.message;
      } catch (error) { feedback.textContent = error.message; }
      finally { working = false; controls.forEach(control => { control.disabled = false; }); }
    };
    form.addEventListener('submit', event => { event.preventDefault(); perform('save'); });
    form.addEventListener('input', () => {
      resultActions.replaceChildren(); saved.textContent = 'Unsaved changes';
      feedback.textContent = 'Save or check to use these locations.';
    });
    form.append(node('div', { class: 'advanced-tool-actions' }, [save, button(tool.default_url ? 'Save and check connection' : 'Save and check files', () => perform('check')), button('Forget saved locations', () => perform('forget'))]), feedback, resultActions);
    if (tool.files.free_gb !== null) form.append(node('p', { class: 'advanced-tools-note' }, `${tool.files.free_gb} GB free on the saved application drive. Model caches may use a different drive.`));
    details.append(form);
    card.append(details);
    return card;
  }

  function focusTool() {
    if (!pendingTool) return;
    const card = document.getElementById('advanced-tool-' + pendingTool);
    if (card) {
      const details = card.querySelector('details');
      details.open = true;
      scrollSettingsTo(card);
      details.querySelector('summary').focus({ preventScroll: true });
      pendingTool = '';
    }
  }

  function scrollSettingsTo(element) {
    const container = element.closest('.settings-content');
    if (container) container.scrollTop += element.getBoundingClientRect().top - container.getBoundingClientRect().top - 16;
  }

  async function load(force = false) {
    if (loading) return loading;
    if (loaded && !force) { focusTool(); return; }
    root.setAttribute('aria-busy', 'true');
    status.textContent = 'Reading tool setup...';
    loading = (async () => {
      try {
        const data = await api('catalog');
        root.replaceChildren(...data.tools.map(renderCard));
        loaded = true; status.textContent = '3 optional applications';
        focusTool();
      } catch (error) {
        status.textContent = error.message;
        if (!loaded) root.replaceChildren(button('Try again', () => load(true)));
      } finally { root.setAttribute('aria-busy', 'false'); loading = null; }
    })();
    return loading;
  }

  function open(toolId = '') {
    pendingTool = toolId;
    openSettingsTab('upgrades');
    load().then(() => {
      if (!toolId) scrollSettingsTo(document.getElementById('advanced-tools'));
    });
  }
  window.AdvancedToolsUI = { load, open, toolForListing: id => Object.hasOwn(listingTools, id) ? listingTools[id] : null };
})();
