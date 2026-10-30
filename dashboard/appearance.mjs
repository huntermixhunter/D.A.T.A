import { REWARD_SECTOR, hasEventHorizon } from './arcade/engine.mjs';

const REWARD = 'event-horizon';
const names = { minimal: 'MINIMAL', cyber: 'CYBER', [REWARD]: 'EVENT HORIZON' };
const sessionProgress = new Map();
const selections = new Map();
let profile = '';

function read(key) {
  try { return localStorage.getItem(key); } catch (_) { return null; }
}
function savedProgress() {
  try { return JSON.parse(read('data-void-patrol-v1:' + profile) || 'null'); } catch (_) { return null; }
}
function unlocked() {
  return !!profile && (hasEventHorizon(savedProgress()) || hasEventHorizon(sessionProgress.get(profile)));
}
function available() { return ['minimal', 'cyber', ...(unlocked() ? [REWARD] : [])]; }
function currentTheme() {
  if (document.body.classList.contains('theme-event-horizon')) return REWARD;
  return document.body.classList.contains('theme-cyberpunk') ? 'cyber' : 'minimal';
}
function paint() {
  const earned = unlocked();
  const button = document.querySelector('[data-theme="event-horizon"]');
  if (button) {
    button.disabled = !earned;
    button.textContent = earned ? 'EVENT HORIZON' : 'EVENT HORIZON (LOCKED)';
    button.title = earned ? 'Earned by clearing Sector ' + REWARD_SECTOR : 'Clear Sector ' + REWARD_SECTOR + ' in one run';
  }
  document.querySelectorAll('#settings-theme-row [data-theme]').forEach(node => {
    node.classList.toggle('active', node.dataset.theme === currentTheme());
    node.setAttribute('aria-pressed', String(node.dataset.theme === currentTheme()));
  });
  const status = document.getElementById('event-horizon-status');
  if (status) status.textContent = earned
    ? (hasEventHorizon(savedProgress()) ? 'Event Horizon unlocked. Available for this profile in this browser.' : 'Event Horizon unlocked for this visit. Browser storage is unavailable.')
    : 'Clear Sector ' + REWARD_SECTOR + ' in one Void Patrol run to unlock Event Horizon.';
  const label = document.getElementById('theme-toggle-label');
  const cycle = available();
  if (label) label.textContent = names[cycle[(cycle.indexOf(currentTheme()) + 1) % cycle.length]];
}
function applyTheme(theme, { persist = true } = {}) {
  if (!available().includes(theme)) { paint(); return false; }
  document.body.classList.toggle('theme-event-horizon', theme === REWARD);
  document.body.classList.toggle('theme-cyberpunk', theme === 'cyber' || theme === REWARD);
  document.body.classList.toggle('theme-minimal', theme === 'minimal');
  document.body.classList.remove('theme-light');
  if (persist) {
    selections.set(profile, theme);
    try { localStorage.setItem(profile ? 'data-theme:' + profile : 'data-theme', theme); }
    catch (_) {
      const status = document.getElementById('event-horizon-status');
      paint();
      if (status) status.textContent = 'Appearance changed for this visit. Browser storage is unavailable.';
      return true;
    }
  }
  paint();
  return true;
}
function setProfile(id) {
  profile = typeof id === 'string' ? id : '';
  let saved = selections.get(profile) || read('data-theme:' + profile) || read('data-theme') || 'minimal';
  if (saved === 'cyberpunk') saved = 'cyber';
  applyTheme(available().includes(saved) ? saved : 'minimal', { persist: false });
}
function updateProgress(id, clearedSector) {
  if (!id || id !== profile || !Number.isInteger(clearedSector) || clearedSector < 0 || clearedSector > 999) return;
  const previous = sessionProgress.get(id)?.clearedSector || 0;
  sessionProgress.set(id, { clearedSector: Math.max(previous, clearedSector) });
  paint();
}

const orbit = '<circle cx="32" cy="32" r="11"/><ellipse cx="32" cy="32" rx="28" ry="12" transform="rotate(-30 32 32)"/><circle cx="32" cy="32" r="23" stroke-dasharray="2 7"/><circle cx="53" cy="19" r="3" fill="currentColor" stroke="none"/>';
const emblem = '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">' + orbit + '</svg>';
const lockup = document.createElement('div');
lockup.className = 'event-horizon-lockup';
lockup.innerHTML = '<span class="theme-mark">' + emblem + '</span><span class="theme-lockup"><strong>EVENT HORIZON</strong><small>VOID PATROL REWARD</small></span>';
document.querySelector('.header-left-elbow')?.append(lockup);
const atmosphere = document.createElement('div');
atmosphere.className = 'event-horizon-atmosphere';
atmosphere.setAttribute('aria-hidden', 'true');
atmosphere.innerHTML = '<svg viewBox="0 0 600 600" fill="none" stroke="currentColor"><circle cx="300" cy="300" r="205" opacity=".3"/><circle cx="300" cy="300" r="232" opacity=".25"/><circle cx="300" cy="300" r="153" stroke-width=".7"/><circle cx="300" cy="300" r="146" stroke-width="3" opacity=".4"/><ellipse cx="300" cy="300" rx="285" ry="86" transform="rotate(-28 300 300)"/><ellipse cx="300" cy="300" rx="270" ry="78" opacity=".35" transform="rotate(-28 300 300)"/><g class="orbit-motion"><circle cx="300" cy="300" r="248" stroke-dasharray="2 12"/><circle cx="300" cy="52" r="4" fill="currentColor"/><path d="M300 20v25M300 555v25M20 300h25M555 300h25"/></g></svg>';
document.querySelector('.data-main')?.prepend(atmosphere);

window.currentTheme = currentTheme;
window.applyTheme = applyTheme;
window.toggleTheme = () => {
  const cycle = available();
  applyTheme(cycle[(cycle.indexOf(currentTheme()) + 1) % cycle.length]);
};
window._themeLabel = paint;
window.RewardThemes = { setProfile, updateProgress, unlocked, paint };
setProfile(window.getArcadeProfile?.() || '');
window.addEventListener('storage', event => {
  if (event.key === null || event.key === 'data-void-patrol-v1:' + profile || event.key === 'data-theme:' + profile) {
    if (event.key === null || event.key === 'data-void-patrol-v1:' + profile) sessionProgress.delete(profile);
    if (event.key === null || event.key === 'data-theme:' + profile) selections.delete(profile);
    setProfile(profile);
  }
});
