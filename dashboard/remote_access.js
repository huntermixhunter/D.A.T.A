/* Remote Access setup. Bot tokens stay out of browser storage; phone links use URL fragments. */
const RemoteAccessUI = (() => {
  let state = null;
  let busy = false;
  let initialized = false;
  let pairing = '';
  let pairExpires = 0;
  let poll = null;
  let sequence = 0;
  const el = id => document.getElementById(`remote-${id}`);
  const visible = (id, show) => { el(id).hidden = !show; };
  const active = () => !document.getElementById('settings-overlay').classList.contains('hidden') &&
    document.querySelector('[data-pane="remote-access"]').classList.contains('active');

  function feedback(message, error = false) {
    el('feedback').textContent = message;
    el('feedback').dataset.error = String(error);
  }

  async function api(action, data) {
    const response = await fetch(`${API_BASE}/remote-access/${action}`, data === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    });
    let result;
    try { result = await response.json(); }
    catch (_) { throw new Error('Remote access setup is unavailable. Restart DATA to load this update.'); }
    if (!response.ok || result.error) {
      const error = new Error(result.error || 'Could not complete remote access setup. Try again.');
      error.approval = result.approval_url;
      throw error;
    }
    return result;
  }

  function approval(url) {
    let safe = false;
    try {
      const parsed = new URL(url);
      safe = parsed.protocol === 'https:' && parsed.hostname === 'login.tailscale.com' &&
        parsed.pathname.startsWith('/f/serve');
    } catch (_) { /* No approval link. */ }
    visible('https-approval', safe);
    if (safe) el('https-approval').href = url;
    else el('https-approval').removeAttribute('href');
  }

  function render() {
    el('controls').disabled = busy || !state || !!state.preview;
    el('refresh').disabled = busy;
    if (!state) return;
    const mobile = state.mobile;
    const tg = state.telegram;
    const ready = mobile.state === 'enabled';
    el('mobile-state').textContent = ready ? 'Connected' : mobile.enabled ? 'Needs attention' : 'Not connected';
    el('mobile-state').dataset.ready = String(ready);
    el('mobile-message').textContent = mobile.message;
    el('mobile-enable').disabled = !['ready', 'enabled'].includes(mobile.state);
    visible('mobile-enable', !ready);
    visible('mobile-disable', mobile.enabled);
    visible('phone-row', ready);
    if (!ready) el('phone-link').value = '';
    approval(mobile.approval_url);
    el('telegram-state').textContent = tg.error ? 'Needs attention' : tg.configured && !tg.profile_active ? 'Paused' : tg.paired && tg.running ? 'Connected' : tg.configured ? 'Pairing / reconnect' : 'Not connected';
    el('telegram-state').dataset.ready = String(tg.paired && tg.running && tg.profile_active && !tg.error);
    visible('telegram-form', !tg.configured);
    visible('bot-name', tg.configured);
    el('bot-name').textContent = tg.username ? `Connected bot: @${tg.username}` : '';
    if (tg.paired || !tg.configured || Date.now() / 1000 > pairExpires) pairing = '';
    visible('pair-open', !!pairing);
    visible('pair-copy', !!pairing);
    visible('pair-new', tg.configured && !tg.paired);
    if (pairing) el('pair-open').href = pairing;
    else el('pair-open').removeAttribute('href');
    visible('telegram-test', tg.paired && tg.profile_active);
    visible('telegram-restart', tg.configured && !tg.running);
    visible('telegram-disconnect', tg.configured);
    el('pair-status').textContent = tg.error || (!tg.profile_active ? 'Paused: select the DATA profile that paired this bot.' :
      tg.paired ? `Paired with ${tg.owner_name || 'your Telegram account'}. Send a text message to your bot to talk to DATA.` :
      pairing ? 'Waiting for you to open Telegram and tap Start. This page checks automatically.' :
      tg.configured ? 'Create a new pairing link to connect your private chat.' : 'Connect your bot first.');
    if (!busy) el('keep-running').checked = state.keep_running;
    el('keep-running').disabled = state.daemon;
  }

  function schedulePoll() {
    clearTimeout(poll);
    if (active() && state?.telegram.configured && !state.telegram.paired && pairing) {
      poll = setTimeout(() => load(true), 4000);
    }
  }

  async function load(quiet = false) {
    init();
    if (busy) return;
    const request = ++sequence;
    if (!quiet) feedback('Checking connections...');
    el('refresh').disabled = true;
    try {
      const result = await api('status');
      if (request !== sequence) return;
      state = result;
      render();
      if (!quiet) feedback(state.preview ? 'Local preview. Setup controls are inactive here. Open Settings in DATA after restarting it to connect your devices.' :
        'Choose an option below to set up or manage your connection.');
    } catch (error) {
      if (request !== sequence) return;
      state = null;
      render();
      feedback(error.message, true);
    } finally {
      if (request === sequence) el('refresh').disabled = busy;
      schedulePoll();
    }
  }

  async function perform(action, data = {}) {
    if (busy) return null;
    busy = true;
    ++sequence;
    clearTimeout(poll);
    render();
    feedback('Updating connection...');
    let result = null;
    try {
      result = await api(action, data);
      if (result.pair_url) {
        const url = new URL(result.pair_url);
        if (url.protocol !== 'https:' || url.hostname !== 't.me') throw new Error('Invalid Telegram pairing link.');
        pairing = result.pair_url;
        pairExpires = result.expires_at;
      }
      if (action === 'telegram/connect') el('bot-token').value = '';
      if (action === 'telegram/disconnect') pairing = '';
      state = await api('status');
      feedback(result.message || 'Connection updated.');
      render();
    } catch (error) {
      feedback(error.message, true);
      render();
      if (error.approval) approval(error.approval);
    } finally {
      busy = false;
      render();
      schedulePoll();
    }
    return result;
  }

  async function copy(value, fallback) {
    try {
      await navigator.clipboard.writeText(value);
      feedback('Private link copied. Open it on your phone and keep it private.');
    } catch (_) {
      if (fallback) { fallback.focus(); fallback.select(); }
      feedback(fallback ? 'Select and copy the private link shown above.' : 'Use Pair in Telegram, or copy its link from the browser menu.', true);
    }
  }

  function choose(which) {
    ['mobile', 'telegram'].forEach(name => {
      const selected = name === which;
      visible(`${name}-panel`, selected);
      el(`choose-${name}`).classList.toggle('selected', selected);
      el(`choose-${name}`).setAttribute('aria-pressed', String(selected));
    });
  }

  function hide() {
    clearTimeout(poll);
    ++sequence;
    if (initialized) {
      el('bot-token').value = '';
      el('phone-link').value = '';
    }
  }

  function init() {
    if (initialized) return;
    initialized = true;
    ['mobile', 'telegram'].forEach(name => el(`choose-${name}`).addEventListener('click', () => choose(name)));
    el('refresh').addEventListener('click', () => load());
    el('mobile-enable').addEventListener('click', () => perform('mobile/enable', { confirmed: true }));
    el('mobile-disable').addEventListener('click', () => perform('mobile/disable'));
    el('phone-copy').addEventListener('click', async () => {
      try {
        const result = await api('mobile/link', {});
        el('phone-link').value = result.link;
        await copy(result.link, el('phone-link'));
      } catch (error) { feedback(error.message, true); }
    });
    el('telegram-form').addEventListener('submit', event => {
      event.preventDefault();
      perform('telegram/connect', { token: el('bot-token').value.trim(), confirmed: true });
    });
    el('pair-new').addEventListener('click', () => perform('telegram/pair'));
    el('pair-copy').addEventListener('click', () => copy(pairing));
    ['test', 'restart', 'disconnect'].forEach(action => el(`telegram-${action}`).addEventListener('click', () => perform(`telegram/${action}`)));
    el('keep-running').addEventListener('change', () => perform('keep-running', { enabled: el('keep-running').checked }));
  }

  window.addEventListener('DOMContentLoaded', () => {
    if (location.hash === '#remote-access') openSettingsTab('remote-access');
  });
  return { load, hide };
})();
