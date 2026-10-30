import { config } from './client.js';
import { node } from './ui.js';

let loading;
function load() {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (!loading) loading = new Promise((resolve, reject) => {
    const script = node('script', { src: 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit', async: true });
    const timer = setTimeout(() => fail(), 20000);
    function fail() {
      clearTimeout(timer);
      script.remove();
      reject(new Error('Security check could not load. Please reopen this form and try again.'));
    }
    script.addEventListener('error', fail, { once: true });
    script.addEventListener('load', () => {
      clearTimeout(timer);
      if (!window.turnstile) { fail(); return; }
      resolve(window.turnstile);
    }, { once: true });
    document.head.append(script);
  }).catch(error => { loading = null; throw error; });
  return loading;
}

export function captcha(form, dialog, error) {
  if (!config.turnstileSiteKey) return { token: () => undefined, reset: () => {} };
  const container = node('div', { class: 'auth-captcha', 'aria-label': 'Security check' });
  form.append(container);
  let token = '', widget, api, closed = false;
  dialog.addEventListener('close', () => {
    closed = true;
    token = '';
    if (widget !== undefined) api.remove(widget);
  }, { once: true });
  load().then(turnstile => {
    if (closed) return;
    api = turnstile;
    widget = api.render(container, {
      sitekey: config.turnstileSiteKey, theme: 'dark', size: 'compact',
      callback: value => { token = value; error.textContent = ''; },
      'expired-callback': () => { token = ''; },
      'error-callback': () => { token = ''; error.textContent = 'Security check failed. Please reopen this form and try again.'; },
    });
  }).catch(failure => { if (!closed) error.textContent = failure.message; });
  return {
    token() {
      if (!token) throw new Error('Complete the security check before continuing.');
      return token;
    },
    reset() {
      token = '';
      if (!closed && widget !== undefined && container.isConnected) api.reset(widget);
    },
  };
}
