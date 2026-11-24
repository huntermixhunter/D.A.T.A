/* Optional Skill Library in Settings > Upgrades: add or remove extra skills one at a time. */
(() => {
  'use strict';
  const root = document.getElementById('skill-library-list');
  if (!root) return;
  const status = document.getElementById('skill-library-status');
  const filter = document.getElementById('skill-library-filter');
  const group = document.getElementById('skill-library-group');
  const show = document.getElementById('skill-library-show');
  const screenBox = document.getElementById('skill-library-screen');
  const screenText = document.getElementById('skill-library-screen-text');
  const screenButton = document.getElementById('skill-library-screen-btn');

  const STATE_LABEL = {
    installed: 'Installed',
    modified: 'Installed, edited',
    present: 'Already on this computer',
    absent: '',
  };
  let data = null;
  let loading = null;
  let busy = new Set();
  let screenPoll = null;

  function node(tag, attrs = {}, children = []) {
    const element = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key.startsWith('on')) element.addEventListener(key.slice(2), value);
      else if (value !== undefined && value !== null && value !== false) element.setAttribute(key, value === true ? '' : value);
    }
    for (const child of [children].flat()) {
      if (child === null || child === undefined || child === '') continue;
      element.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return element;
  }

  async function api(path, body, timeoutMs = 20000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(`${API_BASE}/upgrades/skill-library${path}`, {
        method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'X-Data-Request': '1', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal,
      });
      let result;
      try { result = await response.json(); }
      catch (_) { throw new Error('The skill library is unavailable. Restart DATA after updating and try again.'); }
      if (!response.ok) throw new Error(result.error || 'The skill library request did not finish.');
      return result;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('The request timed out. Check the internet connection and try again.');
      throw error;
    } finally { clearTimeout(timer); }
  }

  function sizeLabel(kb) {
    if (!Number.isFinite(kb)) return '';
    return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb} KB`;
  }

  function matches(skill) {
    const q = filter.value.trim().toLowerCase();
    if (group.value && skill.group !== group.value) return false;
    const installed = skill.state === 'installed' || skill.state === 'modified';
    if (show.value === 'installed' && !installed) return false;
    if (show.value === 'available' && skill.state !== 'absent') return false;
    if (!q) return true;
    const groupLabel = (data.groups && data.groups[skill.group]) || '';
    return [skill.name, skill.description, skill.needs, groupLabel].join(' ').toLowerCase()
      .includes(q);
  }

  async function act(skill, action) {
    busy.add(skill.id);
    render();
    status.classList.remove('skill-library-error');
    status.textContent = action === 'install' ? `Installing ${skill.name}...` : `Removing ${skill.name}...`;
    try {
      const result = await api(`/${action}`, { id: skill.id }, 120000);
      skill.state = result.state;
      status.textContent = result.message;
    } catch (error) {
      status.textContent = error.message;
      status.classList.add('skill-library-error');
    } finally {
      busy.delete(skill.id);
      render();
    }
  }

  function card(skill) {
    const label = STATE_LABEL[skill.state] || '';
    const groupLabel = (data.groups && data.groups[skill.group]) || '';
    const isBusy = busy.has(skill.id);
    let action = null;
    if (skill.state === 'absent') {
      action = node('button', { type: 'button', class: 'data-btn-sm orange', disabled: isBusy,
        'aria-label': `Install ${skill.name}`, onclick: () => act(skill, 'install') }, isBusy ? 'Installing...' : 'Install');
    } else if (skill.state === 'installed' || skill.state === 'modified') {
      action = node('button', { type: 'button', class: 'data-btn-sm', disabled: isBusy,
        'aria-label': `Remove ${skill.name}`, onclick: () => act(skill, 'remove') }, isBusy ? 'Removing...' : 'Remove');
    }
    return node('article', { class: 'skill-library-card', 'data-state': skill.state }, [
      node('div', { class: 'skill-library-card-head' }, [
        node('h5', { class: 'skill-library-name' }, skill.name),
        label ? node('span', { class: 'skill-library-badge', 'data-state': skill.state }, label) : null,
      ]),
      node('p', { class: 'skill-library-meta' }, [groupLabel, sizeLabel(skill.kb)].filter(Boolean).join(' · ')),
      skill.description ? node('p', { class: 'skill-library-description' }, skill.description) : null,
      skill.needs ? node('p', { class: 'skill-library-needs' }, `Needs: ${skill.needs}`) : null,
      action ? node('div', { class: 'skill-library-card-footer' }, action) : null,
    ]);
  }

  function render() {
    if (!data) return;
    const shown = data.skills.filter(matches);
    root.replaceChildren(...shown.map(card));
    if (!shown.length) root.append(node('p', { class: 'skill-library-empty' }, 'No skills match. Try another word or category.'));
  }

  function summary() {
    const installed = data.skills.filter(s => s.state === 'installed' || s.state === 'modified').length;
    return `${data.skills.length} optional skills, ${installed} installed by the library. Nothing downloads until you click Install.`;
  }

  function renderScreen(screen) {
    if (!screen) return;
    clearTimeout(screenPoll);
    if (screen.available && !screen.message) { screenBox.hidden = true; return; }
    screenBox.hidden = false;
    const mac = screen.platform === 'darwin';
    if (screen.running) {
      screenText.textContent = screen.message || 'Installing...';
      screenButton.disabled = true;
      screenButton.textContent = 'Installing...';
      screenPoll = setTimeout(() => load(true), 3000);
      return;
    }
    screenButton.disabled = false;
    screenButton.textContent = 'Install screen control';
    screenButton.hidden = Boolean(screen.available);
    screenText.textContent = screen.message || (
      'DATA needs two free Python packages (pyautogui and Pillow, about 15 MB) before it can see your screen and use the mouse and keyboard.'
      + (mac ? ' The Mac download leaves them out to stay small.' : ''));
    screenText.classList.toggle('skill-library-error', screen.ok === false);
  }

  async function load(force = false) {
    if (loading) return loading;
    if (data && !force) return;
    root.setAttribute('aria-busy', 'true');
    if (!data) status.textContent = 'Reading the skill library...';
    loading = (async () => {
      try {
        const fresh = await api('');
        const first = !data;
        data = fresh;
        if (first || group.options.length <= 1) {
          group.replaceChildren(node('option', { value: '' }, 'All categories'),
            ...Object.entries(data.groups || {}).map(([id, label]) => node('option', { value: id }, label)));
        }
        render();
        if (!busy.size) { status.classList.remove('skill-library-error'); status.textContent = summary(); }
        renderScreen(data.screen_control);
      } catch (error) {
        status.textContent = error.message;
        status.classList.add('skill-library-error');
      } finally { root.setAttribute('aria-busy', 'false'); loading = null; }
    })();
    return loading;
  }

  screenButton.addEventListener('click', async () => {
    screenButton.disabled = true;
    try {
      const result = await api('/screen-control', {});
      renderScreen({ running: result.running, message: result.message, platform: data && data.screen_control && data.screen_control.platform });
      if (!result.running) load(true);
    } catch (error) {
      screenText.textContent = error.message;
      screenText.classList.add('skill-library-error');
      screenButton.disabled = false;
    }
  });
  filter.addEventListener('input', render);
  group.addEventListener('change', render);
  show.addEventListener('change', render);

  window.SkillLibraryUI = { load };
})();
