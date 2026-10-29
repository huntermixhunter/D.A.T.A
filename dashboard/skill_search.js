/* Public skill discovery in Settings. Search never installs or runs a skill. */
(() => {
  'use strict';
  const form = document.getElementById('skill-search-form');
  if (!form) return;
  const input = document.getElementById('skill-search-query');
  const sort = document.getElementById('skill-search-sort');
  const status = document.getElementById('skill-search-status');
  const results = document.getElementById('skill-search-results');
  const submit = document.getElementById('skill-search-submit');
  const clear = document.getElementById('skill-search-clear');
  const initial = 'Try a task or topic, such as PDF editing, web design, or spreadsheets.';
  let controller = null;
  let revision = 0;
  let timer = null;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  }

  function link(label, value, host) {
    try {
      const url = new URL(value);
      if (url.protocol !== 'https:' || url.host !== host || url.username || url.password) return null;
      const anchor = element('a', 'skill-search-link', label);
      anchor.href = url.href;
      anchor.target = '_blank';
      anchor.rel = 'noopener noreferrer';
      return anchor;
    } catch (_) { return null; }
  }

  function busy(on) {
    results.setAttribute('aria-busy', String(on));
    submit.textContent = on ? 'Searching...' : 'Search';
  }

  function cancel() {
    clearTimeout(timer);
    controller?.abort();
    controller = null;
    revision += 1;
    busy(false);
  }

  function message(text, error = false) {
    status.textContent = text;
    status.classList.toggle('skill-search-error', error);
  }

  function render(items) {
    results.replaceChildren();
    for (const item of items) {
      const card = element('article', 'skill-search-card', '');
      const heading = element('h4', 'skill-search-name', item.name);
      const titleLink = link(item.name, item.url, 'skills.sh');
      if (titleLink) heading.replaceChildren(titleLink);
      card.append(heading, element('p', 'skill-search-source', item.source));
      if (item.description) card.append(element('p', 'skill-search-description', item.description));
      const footer = element('div', 'skill-search-card-footer', '');
      const known = Number.isSafeInteger(item.installs) && item.installs >= 0;
      footer.append(element('span', 'skill-search-installs', known
        ? `${item.installs.toLocaleString()} ${item.installs === 1 ? 'install' : 'installs'}`
        : 'Install count unavailable'));
      const view = link('View skill', item.url, 'skills.sh');
      if (view) { view.setAttribute('aria-label', `View ${item.name} on skills.sh`); footer.append(view); }
      card.append(footer);
      results.append(card);
    }
  }

  async function search() {
    cancel();
    const query = input.value.trim().replace(/\s+/gu, ' ');
    results.replaceChildren();
    if (query.length < 2) {
      message(query ? 'Enter at least 2 characters to search.' : initial);
      return;
    }
    const requestRevision = revision;
    controller = new AbortController();
    const requestController = controller;
    const timeout = setTimeout(() => requestController.abort(), 15000);
    busy(true);
    message(`Searching skills for "${query}"...`);
    try {
      const params = new URLSearchParams({ q: query, sort: sort.value });
      const response = await fetch(`${API_BASE}/upgrades/skills/search?${params}`, { signal: requestController.signal });
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || 'Search is unavailable. Please try again.');
      if (!Array.isArray(data.skills)) throw new Error('The skill catalog response could not be read. Please try again.');
      if (requestRevision !== revision) return;
      render(data.skills);
      const order = sort.value === 'installs' ? 'most installed first' : 'closest matches first, then most installed';
      message(data.skills.length
        ? `${data.skills.length >= data.limit ? 'Top ' : ''}${data.skills.length} ${data.skills.length === 1 ? 'result' : 'results'} for "${query}", ${order}.`
        : `No skills found for "${query}". Try fewer words or a related topic.`);
    } catch (error) {
      if (requestRevision !== revision) return;
      message(error.name === 'AbortError' ? 'Search timed out. Please try again.' : error.message, true);
    } finally {
      clearTimeout(timeout);
      if (requestRevision === revision) { controller = null; busy(false); }
    }
  }

  form.addEventListener('submit', event => { event.preventDefault(); search(); });
  input.addEventListener('input', event => {
    cancel();
    results.replaceChildren();
    const query = input.value.trim();
    clear.hidden = !input.value;
    message(query.length >= 2 ? 'Ready to search...' : query ? 'Enter at least 2 characters to search.' : initial);
    if (!event.isComposing && query.length >= 2) timer = setTimeout(search, 400);
  });
  input.addEventListener('compositionend', () => { clearTimeout(timer); timer = setTimeout(search, 400); });
  sort.addEventListener('change', search);
  clear.addEventListener('click', () => {
    cancel(); input.value = ''; clear.hidden = true;
    results.replaceChildren(); message(initial); input.focus();
  });
  if (location.hash === '#upgrades' && typeof openSettingsTab === 'function') openSettingsTab('upgrades');
})();
