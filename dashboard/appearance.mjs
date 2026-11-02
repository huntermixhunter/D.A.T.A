// Dashboard themes: Minimal (default), Cyber, and Event Horizon, all available from the start.
// Each dashboard profile keeps its own choice. The RewardThemes name dates from when Event Horizon
// was a Void Patrol reward; app.js and the arcade host still call it to re-apply a profile's theme.
const EVENT_HORIZON = 'event-horizon';
const THEMES = ['minimal', 'cyber', EVENT_HORIZON];
const names = { minimal: 'MINIMAL', cyber: 'CYBER', [EVENT_HORIZON]: 'EVENT HORIZON' };
const selections = new Map();
let profile = '';

function read(key) {
  try { return localStorage.getItem(key); } catch (_) { return null; }
}
function currentTheme() {
  if (document.body.classList.contains('theme-event-horizon')) return EVENT_HORIZON;
  return document.body.classList.contains('theme-cyberpunk') ? 'cyber' : 'minimal';
}
function paint() {
  document.querySelectorAll('#settings-theme-row [data-theme]').forEach(node => {
    node.classList.toggle('active', node.dataset.theme === currentTheme());
    node.setAttribute('aria-pressed', String(node.dataset.theme === currentTheme()));
  });
  const label = document.getElementById('theme-toggle-label');
  if (label) label.textContent = names[THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length]];
}
function applyTheme(theme, { persist = true } = {}) {
  if (!THEMES.includes(theme)) { paint(); return false; }
  document.body.classList.toggle('theme-event-horizon', theme === EVENT_HORIZON);
  document.body.classList.toggle('theme-cyberpunk', theme === 'cyber' || theme === EVENT_HORIZON);
  document.body.classList.toggle('theme-minimal', theme === 'minimal');
  document.body.classList.remove('theme-light');
  if (persist) {
    selections.set(profile, theme);
    try { localStorage.setItem(profile ? 'data-theme:' + profile : 'data-theme', theme); }
    catch (_) {
      const status = document.getElementById('theme-status');
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
  applyTheme(THEMES.includes(saved) ? saved : 'minimal', { persist: false });
}

const orbit = '<circle cx="32" cy="32" r="11"/><ellipse cx="32" cy="32" rx="28" ry="12" transform="rotate(-30 32 32)"/><circle cx="32" cy="32" r="23" stroke-dasharray="2 7"/><circle cx="53" cy="19" r="3" fill="currentColor" stroke="none"/>';
const emblem = '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">' + orbit + '</svg>';
const lockup = document.createElement('div');
lockup.className = 'event-horizon-lockup';
lockup.innerHTML = '<span class="theme-mark">' + emblem + '</span><span class="theme-lockup"><strong>EVENT HORIZON</strong><small>ORBITAL INTERFACE</small></span>';
document.querySelector('.header-left-elbow')?.append(lockup);
const atmosphere = document.createElement('div');
atmosphere.className = 'event-horizon-atmosphere';
atmosphere.setAttribute('aria-hidden', 'true');
atmosphere.innerHTML = '<svg viewBox="0 0 600 600" fill="none" stroke="currentColor"><circle cx="300" cy="300" r="205" opacity=".3"/><circle cx="300" cy="300" r="232" opacity=".25"/><circle cx="300" cy="300" r="153" stroke-width=".7"/><circle cx="300" cy="300" r="146" stroke-width="3" opacity=".4"/><ellipse cx="300" cy="300" rx="285" ry="86" transform="rotate(-28 300 300)"/><ellipse cx="300" cy="300" rx="270" ry="78" opacity=".35" transform="rotate(-28 300 300)"/><g class="orbit-motion"><circle cx="300" cy="300" r="248" stroke-dasharray="2 12"/><circle cx="300" cy="52" r="4" fill="currentColor"/><path d="M300 20v25M300 555v25M20 300h25M555 300h25"/></g></svg>';
document.querySelector('.data-main')?.prepend(atmosphere);

window.currentTheme = currentTheme;
window.applyTheme = applyTheme;
window.toggleTheme = () => applyTheme(THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length]);
window._themeLabel = paint;
window.RewardThemes = { setProfile, paint };
setProfile(window.getArcadeProfile?.() || '');
window.addEventListener('storage', event => {
  if (event.key === null || event.key === 'data-theme:' + profile) {
    selections.delete(profile);
    setProfile(profile);
  }
});
