/* Shared Magimatix navigation and optional ambient video. No account state. */
const embedded = window.parent !== window && new URLSearchParams(location.search).get('embed') === '1';
if (embedded) {
  document.documentElement.classList.add('forum-embedded');
  window.addEventListener('message', event => {
    if (event.source !== window.parent || event.data?.type !== 'data:forum:connect') return;
    let origin;
    try { origin = new URL(event.origin); } catch { return; }
    if (!['http:', 'https:'].includes(origin.protocol) || !['localhost', '127.0.0.1'].includes(origin.hostname)) return;
    event.source.postMessage({ type: 'data:forum:ready' }, event.origin);
  });
}
const toggle = document.querySelector('.menu-toggle');
const nav = document.getElementById('site-navigation');
if (toggle && nav) {
  const close = () => {
    toggle.setAttribute('aria-expanded', 'false');
    nav.classList.remove('is-open');
  };
  toggle.addEventListener('click', () => {
    const open = toggle.getAttribute('aria-expanded') !== 'true';
    toggle.setAttribute('aria-expanded', String(open));
    nav.classList.toggle('is-open', open);
  });
  nav.addEventListener('click', event => { if (event.target.closest('a')) close(); });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') {
      close();
      toggle.focus();
    }
  });
  matchMedia('(min-width: 1024px)').addEventListener('change', close);
}

const year = document.getElementById('copyright-year');
if (year) year.textContent = String(new Date().getFullYear());

const video = document.querySelector('.site-video');
const motionToggle = document.querySelector('.ambient-toggle');
if (video && motionToggle) {
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const connection = navigator.connection;
  const syncLabel = () => {
    motionToggle.textContent = video.paused ? 'Play background' : 'Pause background';
    motionToggle.setAttribute('aria-pressed', String(!video.paused));
  };
  const syncPreference = () => {
    if (embedded || reducedMotion.matches || connection?.saveData) {
      video.pause();
      video.removeAttribute('src');
      video.load();
      motionToggle.hidden = true;
      return;
    }
    if (!video.getAttribute('src')) video.src = '/bg-video-0605.mp4';
    video.muted = true;
    motionToggle.hidden = false;
    video.play().catch(syncLabel);
    syncLabel();
  };
  motionToggle.addEventListener('click', () => {
    if (video.paused) video.play().catch(syncLabel);
    else video.pause();
  });
  video.addEventListener('play', syncLabel);
  video.addEventListener('pause', syncLabel);
  video.addEventListener('error', () => { motionToggle.hidden = true; });
  reducedMotion.addEventListener('change', syncPreference);
  connection?.addEventListener('change', syncPreference);
  syncPreference();
}
