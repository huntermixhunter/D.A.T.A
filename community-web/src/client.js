import { createClient } from '@supabase/supabase-js';

export let client = null;
export let config = null;
export let configured = false;

export async function initialize() {
  const response = await fetch('/config.json', { cache: 'no-store', signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error('The community could not load. Please try again shortly.');
  config = await response.json();
  if (!config.supabaseUrl || !config.supabasePublishableKey) return false;
  const origin = new URL(config.supabaseUrl);
  if (origin.protocol !== 'https:' || !/^[a-z0-9-]+\.supabase\.co$/.test(origin.hostname)) {
    throw new Error('The community connection is not configured correctly.');
  }
  client = createClient(origin.origin, config.supabasePublishableKey, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true, flowType: 'implicit' },
    global: { fetch: (url, options = {}) => fetch(url, { ...options, signal: options.signal || AbortSignal.timeout(20000) }) },
  });
  configured = true;
  return true;
}

export async function api(path, payload) {
  if (!client) throw new Error('The community is not connected yet.');
  const [route, search = ''] = path.split('?');
  const query = Object.fromEntries(new URLSearchParams(search));
  const { data, error, status } = await client.rpc('forum_request', { route, payload: payload || {}, query });
  if (error) {
    let message = error.message;
    if (!status || status >= 500 || /fetch|network|abort/i.test(message)) {
      message = 'Could not reach the community. Your draft is still here; please try again shortly.';
    }
    const failure = new Error(message || 'The request could not be completed.');
    failure.status = status;
    throw failure;
  }
  return data;
}

export function redirectUrl() {
  return new URL('/', config.publicUrl || location.origin).href;
}
