import { client, config, redirectUrl, api } from './client.js';
import { node, button, modal, field, busy, note } from './ui.js';
import { captcha } from './captcha.js';

export function accountDialog(account, refresh, mode) {
  mode = mode || (account ? 'profile' : 'login');
  const profile = mode === 'profile';
  const register = mode === 'register';
  const reset = mode === 'reset';
  const recovery = mode === 'recovery';
  const title = profile ? 'Your community account' : register ? 'Join the community' : reset ? 'Reset your password' : recovery ? 'Choose a new password' : 'Sign in to the community';
  const { dialog, body } = modal(title);
  if (register && !config.registrationEnabled) {
    body.append(note('New memberships will open soon. You can still browse all public discussions.'));
    return;
  }
  if (register) body.append(node('p', { class: 'mp-muted' }, 'Your name and handle appear on public posts. Your email stays private. This is a new online account for the community.'));
  const form = node('form', { class: 'mp-form' });
  const name = node('input', { required: true, maxlength: 60, autocomplete: 'nickname', value: account?.name || '' });
  const username = node('input', { required: true, minlength: 3, maxlength: 32, pattern: '[a-z0-9_][a-z0-9_\\-]{2,31}', autocomplete: 'username', value: account?.username || '', autocapitalize: 'none', spellcheck: 'false' });
  const email = node('input', { required: true, type: 'email', maxlength: 254, autocomplete: 'email', autocapitalize: 'none' });
  const password = node('input', { required: true, type: 'password', minlength: 12, maxlength: 128, autocomplete: register || recovery ? 'new-password' : 'current-password' });
  const confirm = node('input', { required: true, type: 'password', minlength: 12, maxlength: 128, autocomplete: 'new-password' });
  if (profile || register) form.append(field('Display name', name));
  if (register || profile) form.append(field('Username', username, '3 to 32 lowercase letters, numbers, underscores, or hyphens. Do not start with a hyphen.'));
  if (!profile && !recovery) form.append(field('Email', email));
  if (!profile && !reset) form.append(field('Password', password, 'Use 12 to 128 characters.'));
  if (recovery) form.append(field('Confirm password', confirm));
  const error = node('p', { class: 'mp-error', role: 'alert' });
  const challenge = !profile && !recovery ? captcha(form, dialog, error) : null;
  const submit = node('button', { type: 'submit', class: 'mp-btn mp-btn-primary' }, profile ? 'Save profile' : register ? 'Create account' : reset ? 'Send reset link' : recovery ? 'Save password' : 'Sign in');
  form.append(error, submit);
  form.addEventListener('submit', event => {
    event.preventDefault();
    busy(submit, error, async () => {
      const inputs = [...form.querySelectorAll('input')];
      inputs.forEach(input => { input.disabled = true; });
      try {
        let result;
        if (register) {
          const available = await api('username?' + new URLSearchParams({ username: username.value.trim() }));
          if (!available.valid) throw new Error('Choose a valid username using the format above.');
          if (!available.available) throw new Error('That username is taken or reserved. Choose another.');
        }
        const captchaToken = challenge?.token();
        if (profile) await api('profile', { name: name.value.trim(), username: username.value.trim() });
        else if (reset) result = await client.auth.resetPasswordForEmail(email.value.trim(), { redirectTo: redirectUrl(), captchaToken });
        else if (recovery) {
          if (password.value !== confirm.value) throw new Error('The passwords do not match.');
          result = await client.auth.updateUser({ password: password.value });
        } else if (register) {
          result = await client.auth.signUp({ email: email.value.trim(), password: password.value,
            options: { data: { display_name: name.value.trim(), username: username.value.trim() }, emailRedirectTo: redirectUrl(), captchaToken } });
        } else result = await client.auth.signInWithPassword({ email: email.value.trim(), password: password.value, options: { captchaToken } });
        if (result?.error) throw result.error;
        password.value = ''; confirm.value = '';
        if (reset || (register && !result.data.session)) {
          body.replaceChildren(note(reset ? 'If an account exists for that email, a password reset link is on its way. Check your inbox and spam folder.' : 'Check your email for a confirmation link before signing in. If the address already has an account, sign in or reset its password.'), button('Back to sign in', () => { dialog.close(); accountDialog(null, refresh, 'login'); }));
          return;
        }
        dialog.close();
        if (recovery) history.replaceState(null, '', location.pathname + location.search);
        await refresh();
      } finally { challenge?.reset(); inputs.forEach(input => { input.disabled = false; }); }
    });
  });
  body.append(form);
  if (profile) body.append(button('Sign out', event => busy(event.currentTarget, error, async () => {
    const { error: failure } = await client.auth.signOut();
    if (failure) throw failure;
    dialog.close();
    await refresh();
  })));
  if (!profile && !recovery) {
    body.append(node('div', { class: 'auth-links' }, [
      button(register || reset ? 'Already have an account? Sign in' : 'Create a new account', () => {
        dialog.close(); accountDialog(null, refresh, register || reset ? 'login' : 'register');
      }),
      !reset && !register ? button('Forgot password?', () => { dialog.close(); accountDialog(null, refresh, 'reset'); }) : null,
    ]));
  }
}
